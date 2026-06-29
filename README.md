# 🎛️ Max Studio Hub

A native Windows **desktop app** that **installs, launches, updates, and stops** four local
AI image tools — each with a single click — and tells you when an update is available. It's a
real application window (Edge WebView2), **not** a browser tab: no tabs, no address bar.

| Tool | What it's for | Web UI |
|------|----------------|--------|
| 🧩 **ComfyUI** | Node-graph diffusion engine (SD / SDXL / Flux) | http://127.0.0.1:8188 |
| 🔥 **SD WebUI Forge** | A1111-style all-rounder, optimized | http://127.0.0.1:7860 |
| 🎨 **Fooocus** | Simplest SDXL app, Midjourney-like | http://127.0.0.1:7865 |
| 🎓 **Kohya_ss** | LoRA / Dreambooth **training** GUI | http://127.0.0.1:7806 |

Each tool gets its **own isolated Python environment**, so they never fight over package
versions.

> 🧩 **ComfyUI ships with [ComfyUI-Manager](https://github.com/Comfy-Org/ComfyUI-Manager) built in.**
> Installing ComfyUI also clones ComfyUI-Manager into `custom_nodes/` and installs its requirements,
> so the **Manager** button is right there in the ComfyUI UI for adding custom nodes and models.

---

## ▶ How to start

Double-click the **“Max Studio Hub”** icon on your **Desktop** (also in the Start menu).
That opens the app window. The launched AI tools still serve their own web UIs — the app's
**🌐 Open UI** button opens each one for you when it's ready.

> The app is installed at `%LOCALAPPDATA%\Programs\MaxStudioHub\` (outside OneDrive). The
> source lives in this `AI-Launcher` folder.

**Prerequisites:** the four tools need **Python 3.10** and **Git**. `Start.bat` installs Python 3.10
automatically via winget if it's missing, and the app shows a banner with a one-click fix when needed.

### 🔁 The app follows this folder live

The installed app **mirrors this `AI-Launcher` folder**: it serves its UI from `web/` and reads
`tools.json` straight from here (a `live_source.txt` next to the exe points at this folder).
So when these files change, the app updates itself:

- Edit anything in **`web/`** (look, layout, logo, text) → the open app **auto-reloads** within ~2 s.
- Edit **`tools.json`** (ports, a tool's commands) → applied live by the built-in watcher.
- **Add/remove tools** and **install state** → already tracked live.

You don't have to rebuild for those. Only changes to the **backend code** (`server.py` / `app.py`)
need a repackage — run `Build.ps1` for that.

---

## ➕ Add any repository

Click **➕ Add tool** (top-right) and paste any GitHub link (or `owner/repo`). Max Studio Hub
downloads it and **works out how to set it up and run it — no coding needed**:

- **If the repo ships its own app/UI** (Gradio, Streamlit, a web app), it runs that.
- **If the repo is just a library/tool with no UI** (like a one-function image refiner), Max Studio
  Hub **auto-builds a simple web UI for it** — it inspects the repo's main function and generates an
  upload-your-input → run → see-the-result page (powered by Gradio, **no AI and no cost**). So you can
  actually *use* the tool, not just run a demo. Click **▶ Launch**, then **🌐 Open UI**.
- Also detects **Node** apps, repos with a `run.bat`/`setup.bat`, **Docker** projects, and **static** sites.
- Works out the **run command** by reading the README, then looking for a known entry file
  (`app.py`, `main.py`, `example.py`, a `streamlit_app.py`, a script that imports a web framework,
  or one with a `__main__` block).
- Creates an isolated environment, installs the project's dependencies (`requirements.txt`,
  `pyproject.toml`, `package.json`, …) **and** the extra libraries the chosen script actually
  imports (e.g. it adds `opencv-python`/`matplotlib` if a demo needs them).
- When the app starts a web UI, it **auto-detects the address** from the output and lights up
  **🌐 Open UI** — even if the port wasn't known in advance.

**You just click ▶ Launch — it sets itself up the first time and runs.** No “run config” step for
the common cases. The added repo is a normal card with the same **Install / Launch / Update / Stop /
🛡 Scan** buttons (and is safety-scanned automatically).

For the rare repo it can't figure out, open **⚙ Run config** and paste the command from the README
(e.g. `python app.py --port 7860`), an optional **port**, and any extra **pip packages**. Remove a
tool with **🗑** (your downloaded files are kept).

> Works best with Python / Node / `.bat` repos. Some repos need extra system tools (a specific Python
> version, CUDA, ffmpeg, etc.) — the app installs what it can and shows anything missing in the log.

---

## 🖱️ What the buttons do

- **Install** – clones the tool from GitHub into your install folder and sets up its
  environment. Forge and Fooocus finish their heavy setup (torch, models) on first launch.
- **Launch** – starts the tool. When its page is ready, the button becomes **🌐 Open UI**.
  Live output streams into the console at the right (pick the tool's tab).
- **⏹ Stop** – cleanly kills the tool and all its child processes.
- **⟳ Update** – your "update by a click" button: `git pull` (auto-stashing local tweaks),
  submodule update where needed, and reinstall of changed dependencies. When a newer version
  exists upstream the button turns **orange** with a count, e.g. **Update (7)**.
- **Check all for updates** (top right) – fetches from GitHub for every installed tool
  *without changing anything*, so you can see what has updates before deciding.
- **📁** opens the tool's folder · **GitHub ↗** opens its repo.

Nothing installs or updates unless you click it.

---

## 🔄 Stays in sync with your folders

The app watches the install folder in the background and **follows up automatically** when the
data on disk changes — no manual refresh:

- Install, clone, **delete**, or move a tool's folder outside the app → its card updates
  (Installed ↔ Not installed) within ~2 seconds.
- Update a tool with `git` outside the app, or check out a different commit → the displayed
  **branch / version / commit** follows the new state.
- Edit `tools.json` or change the install folder in **⚙ Settings** → the app reloads it live.
- A tool's **GitHub “About” introduction** changes → its card description follows. Each card shows
  the repo's live intro (with a small **↻ GitHub** badge); repos without an About blurb keep the
  built-in description. Intros refresh on startup, every ~6 hours, and on **Check all for updates**,
  and are cached so they still show offline.

When it notices a change it briefly shows “Updated — refreshed.” (It ignores ordinary model/output
file writes, so generating images doesn't spam refreshes — it only reacts to install state, git
position, and repo intros.)

---

## 🛡 Safety scan

Every installed tool has a **🛡 Scan** button, and a scan also runs **automatically right after an
install**. It's a **read-only heuristic check — it never executes any scanned code** — that walks the
tool's files (skipping the venv and model folders) and flags:

- 🔴 **High-risk signals** — obfuscated `exec`/`eval` (base64/hex/marshal), PowerShell `-EncodedCommand`,
  `curl|wget | sh`, data-exfiltration webhooks (Discord/Telegram), crypto-miner strings, `netcat -e`
  reverse shells, autostart Run keys, and stray `.exe`/`.dll`/`.scr` files in the repo.
- 🟡 **Review items** — normal-but-powerful things these tools legitimately do: `subprocess`, network
  calls, `pickle`/`torch.load`, and pickle-format models (`.ckpt`/`.pt`/`.bin`) that can run code when
  loaded (prefer `.safetensors`).

The card shows a verdict — green **“No high-risk signals”** or red **“N high-risk signals”** — and you
click it for the full report (each finding shows `file:line` + the reason). This is most useful **after
you add ComfyUI custom nodes or WebUI extensions** (third-party code that runs on launch): hit **Scan**
to vet them before launching.

> ⚠️ It's a heuristic aid, **not a guarantee**. It can miss cleverly hidden malware and can flag harmless
> code. Treat red as “investigate,” green as “no obvious red flags” — not “certified safe.” Only install
> nodes/models/extensions from sources you trust.

---

## 📁 Where things are installed

Default install folder for the **tools/models**: **`C:\AItools`** (each tool in its own
subfolder). Change it via **⚙ Settings**.

> ⚠️ Keep the tools folder **out of OneDrive**. Multi-GB models and Python environments synced
> by OneDrive sync slowly and can corrupt. The app defaults to `C:\AItools` and warns you if
> you pick a synced path. (Models go inside each tool, e.g.
> `C:\AItools\ComfyUI\models\checkpoints`.)

---

## 🟢 GPU notes (NVIDIA RTX 50-series / Blackwell)

RTX 50-series (Blackwell) GPUs need **CUDA 12.8** PyTorch wheels. The app sets this up: ComfyUI
installs torch from the `cu128` index, and Forge/Fooocus get a `cu128` `TORCH_COMMAND` override so
they don't grab an older torch that can't drive a 50-series GPU. On an older GPU, edit `tools.json`
and change/remove the `cu128` lines.

---

## 🔧 Customizing & rebuilding

Everything is driven by **`tools.json`** — ports, `python_version`, the torch index,
`launch_env`, and the `install`/`launch`/`update` commands. Placeholders filled at runtime:
`${venv_python}`, `${python}`, `${git}`, `${port}`, `${tool_dir}`, `${clone_url}`, `${branch}`.

After editing source, rebuild the app with:

```
powershell -ExecutionPolicy Bypass -File Build.ps1
```

That regenerates `MaxStudioHub.exe`, reinstalls it, and refreshes the shortcuts.
For quick dev runs without rebuilding, double-click **`Start.bat`** (runs the same native
window straight from source).

---

## 🩹 Troubleshooting

- **First launch of a tool takes a while** – Forge/Fooocus download several GB the first time.
  Watch that tool's console tab; it's normal. Fooocus pulls ~30 GB of default models.
- **"Port already in use" when launching** – something else is on that port. Stop it or change
  the tool's `port` in `tools.json` and rebuild.
- **Update says "couldn't reach remote"** – you're offline; the installed copy is untouched.
- **Kohya training needs Visual C++ / CUDA toolkit** – install those from Microsoft / NVIDIA
  if its setup complains.

---

## 🧱 Project layout

```
AI-Launcher/
├─ app.py            native window (Edge WebView2) hosting the dashboard
├─ server.py         stdlib HTTP server + process manager (git/python orchestration)
├─ web/              dashboard UI (index.html, style.css, app.js)
├─ tools.json        the registry of the four tools
├─ assets/app.ico    app icon  (make_icon.py regenerates it)
├─ Start.bat/.ps1    run the native window from source
├─ Build.ps1         build MaxStudioHub.exe + install + shortcuts
└─ config.json       saved settings (install folder)
```

Update detection uses `git fetch` + `git rev-list --count HEAD..@{u}`, so it can tell you an
update exists **without** touching your working copy. The app leaves running tools alive when
you close its window (they run in their own process trees).
