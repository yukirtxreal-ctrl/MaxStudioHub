# Max Studio Hub - build the standalone .exe and install it (Desktop + Start Menu
# shortcuts). Run this after editing the source to regenerate the app.
#
#   powershell -ExecutionPolicy Bypass -File Build.ps1

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Py310 {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    try { $e = & py -3.10 -c "import sys;print(sys.executable)"
          if ($LASTEXITCODE -eq 0 -and (Test-Path $e.Trim())) { return $e.Trim() } } catch {}
  }
  foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
                   "C:\Python310\python.exe","$env:ProgramFiles\Python310\python.exe")) {
    if (Test-Path $c) { return $c }
  }
  return $null
}

$py = Find-Py310
if (-not $py) { Write-Host "Python 3.10 not found - run Start.bat once to install it." -ForegroundColor Red; exit 1 }

$venv = Join-Path $here ".venv"
$vpy  = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) { Write-Host "Creating build venv..."; & $py -m venv $venv }
Write-Host "Installing build tools (pywebview, pyinstaller, pillow)..."
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install pywebview pyinstaller pillow --quiet

$icon = Join-Path $here "assets\app.ico"
if (-not (Test-Path $icon)) { Write-Host "Generating icon..."; & $vpy (Join-Path $here "assets\make_icon.py") }

Write-Host "Building MaxStudioHub.exe..." -ForegroundColor Cyan
& $vpy -m PyInstaller --noconfirm --clean --windowed --name MaxStudioHub `
  --icon $icon `
  --add-data "$here\web;web" `
  --add-data "$here\tools.json;." `
  --distpath "$here\dist" --workpath "$here\build" --specpath $here `
  (Join-Path $here "app.py")

# Install to a clean per-user location (outside OneDrive) + shortcuts.
Get-Process MaxStudioHub -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1
$installDir = "$env:LOCALAPPDATA\Programs\MaxStudioHub"
# preserve user data (added tools, settings, caches) across rebuilds
$userFiles = @("config.json", "custom_tools.json", "repo_cache.json")
$backup = @{}
foreach ($f in $userFiles) { $p = "$installDir\$f"; if (Test-Path $p) { $backup[$f] = Get-Content $p -Raw } }
if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }
Copy-Item -Recurse -Force "$here\dist\MaxStudioHub" $installDir
foreach ($f in $userFiles) { if ($backup.ContainsKey($f)) { [System.IO.File]::WriteAllText("$installDir\$f", $backup[$f]) } }
$exe = "$installDir\MaxStudioHub.exe"
# ship the .ico next to the exe under a FRESH filename each build — Windows caches
# shortcut icons by path, so a new name guarantees the new icon actually shows
$iconPath = "$exe,0"
if (Test-Path "$here\assets\app.ico") {
  Get-ChildItem $installDir -Filter "logo-*.ico" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
  $iconPath = "$installDir\logo-$((Get-Date).Ticks).ico"
  Copy-Item "$here\assets\app.ico" $iconPath -Force
}
# point the installed app at this source folder so it mirrors web/ + tools.json LIVE
[System.IO.File]::WriteAllText("$installDir\live_source.txt", $here)

$ws = New-Object -ComObject WScript.Shell
foreach ($p in @("$([Environment]::GetFolderPath('Desktop'))\Max Studio Hub.lnk",
                 "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Max Studio Hub.lnk")) {
  $s = $ws.CreateShortcut($p)
  $s.TargetPath = $exe; $s.WorkingDirectory = $installDir
  $s.IconLocation = $iconPath
  $s.Description = "Max Studio Hub - install, launch & update ComfyUI, Forge, Fooocus, Kohya_ss"
  $s.Save()
}
# clear + rebuild the icon cache and tell Explorer to refresh, so the icon updates now
try { & ie4uinit.exe -ClearIconCache } catch {}
try { & ie4uinit.exe -show } catch {}
try {
  Add-Type -Namespace Win32 -Name Sh -MemberDefinition '[System.Runtime.InteropServices.DllImport("shell32.dll")] public static extern void SHChangeNotify(int e, int f, System.IntPtr a, System.IntPtr b);'
  [Win32.Sh]::SHChangeNotify(0x08000000, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero)
} catch {}
Write-Host "Done. Installed to $installDir and refreshed Desktop + Start Menu shortcuts." -ForegroundColor Green
