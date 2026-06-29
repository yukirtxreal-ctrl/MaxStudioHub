# Max Studio Hub - run the NATIVE app window directly from source (no browser).
# This is the "run from source" path. Most users should just use the installed
# app via the "Max Studio Hub" Desktop shortcut (built by Build.ps1).

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Refresh-Path {
  $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $u = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = ($m, $u | Where-Object { $_ }) -join ";"
}

function Find-Py310 {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
      $exe = & py -3.10 -c "import sys;print(sys.executable)"
      if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe.Trim())) { return $exe.Trim() }
    } catch { }
  }
  foreach ($c in @(
      "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
      "C:\Python310\python.exe",
      "$env:ProgramFiles\Python310\python.exe")) {
    if ($c -and (Test-Path $c)) { return $c }
  }
  return $null
}

$py = Find-Py310
if (-not $py) {
  Write-Host "Installing Python 3.10 (one-time)..." -ForegroundColor Yellow
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install -e --id Python.Python.3.10 --scope user --accept-source-agreements --accept-package-agreements
    Refresh-Path; Start-Sleep 2; $py = Find-Py310
  }
}
if (-not $py) {
  Write-Host "Python 3.10 is required. Install from https://www.python.org/downloads/release/python-31011/ then retry." -ForegroundColor Red
  Read-Host "Press Enter to exit"; exit 1
}

$venv = Join-Path $here ".venv"
$vpy  = Join-Path $venv "Scripts\python.exe"
$vpw  = Join-Path $venv "Scripts\pythonw.exe"
if (-not (Test-Path $vpw)) {
  Write-Host "Setting up launcher environment (one-time)..." -ForegroundColor Yellow
  & $py -m venv $venv
  & $vpy -m pip install --upgrade pip --quiet
  & $vpy -m pip install pywebview --quiet
}

# pythonw = no console window; launch detached so this shell can close.
Start-Process -FilePath $vpw -ArgumentList "`"$(Join-Path $here 'app.py')`"" -WorkingDirectory $here
Write-Host "Max Studio Hub launched." -ForegroundColor Green
