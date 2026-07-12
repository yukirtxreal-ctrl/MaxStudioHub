<p align="center">
  <img src="assets/app.png" alt="Max Studio Hub" width="116" />
</p>

<h1 align="center">Max Studio Hub</h1>

<p align="center">
  One app to install, run, and update your local AI image tools.<br/>
  Works on <b>Windows</b> and <b>Mac</b>. No coding needed.
</p>

<p align="center">
  <a href="https://github.com/yukirtxreal-ctrl/MaxStudioHub/releases/latest/download/MaxStudioHub-Windows.zip"><b>⬇&nbsp; Download for Windows</b></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/yukirtxreal-ctrl/MaxStudioHub/releases/latest/download/MaxStudioHub-macOS.zip"><b>⬇&nbsp; Download for Mac</b></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/yukirtxreal-ctrl/MaxStudioHub/releases/latest">all versions</a>
</p>

---

## What is this?

AI tools like ComfyUI are great, but setting them up is hard — Python versions, git
commands, long install guides. Max Studio Hub does all of that for you. You click
**Install**, it sets the tool up. You click **Launch**, it runs. It also tells you
when a tool has an update, and it can set up almost any GitHub project you give it.

It comes with these four tools built in:

| Tool | What it does | On a Mac |
|------|--------------|----------|
| 🧩 **ComfyUI** | Make images with a node graph. Very powerful. | ✅ works well |
| 🔥 **SD WebUI Forge** | Classic image maker with lots of options. | ⚠️ experimental |
| 🎨 **Fooocus** | The easiest image maker. Type a prompt, get a picture. | ✅ works (slower than NVIDIA) |
| 🎓 **Kohya_ss** | Train your own styles (LoRA). Not for making images. | ⚠️ limited — training really wants an NVIDIA card |

Each tool gets its own private Python setup, so they never break each other.

---

## Set it up on Windows

Works on any PC with **Windows 10 or newer**.

1. Download **[MaxStudioHub-Windows.zip](https://github.com/yukirtxreal-ctrl/MaxStudioHub/releases/latest/download/MaxStudioHub-Windows.zip)**.
2. Right-click the zip → **Extract All** → open the new folder.
3. Double-click **`MaxStudioHub.exe`**.
4. If Windows says *"Windows protected your PC"*: click **More info**, then **Run anyway**.
   (This happens because the app is free and not code-signed. All the code is open, right here.)

You also need **Git** and **Python 3.10** for the AI tools themselves.
Don't worry — if they are missing, the app shows a yellow bar at the top with
easy install steps.

## Set it up on a Mac

Works on Macs with **macOS 11 or newer** — both **Apple chips (M1 and newer)**
and **Intel chips**. Apple chips make images much faster.

1. Download **[MaxStudioHub-macOS.zip](https://github.com/yukirtxreal-ctrl/MaxStudioHub/releases/latest/download/MaxStudioHub-macOS.zip)** and open it.
2. Drag **`Max Studio Hub.app`** into your **Applications** folder.
3. Double-click it. The **first** time, your Mac will block it (the app is free
   and not registered with Apple). To open it:
   **System Settings → Privacy & Security → scroll down → click "Open Anyway"**.
   You only do this once. On older Macs: right-click the app → **Open** → **Open**.

You also need:

- **Git** — open the Terminal app and run: `xcode-select --install`
- **Python 3.10** — run: `brew install python@3.10` (get Homebrew at [brew.sh](https://brew.sh))

The app shows a yellow bar with these steps if something is missing.
On a Mac, images take longer to make than on an NVIDIA PC — that is normal.

---

## How to use it

1. Pick a tool card and click **⬇ Install**. This is a one-time download and setup.
2. Click **▶ Launch**. Watch the black console on the right if you are curious.
3. When the button changes to **🌐 Open UI**, click it. The tool opens in your browser. Have fun!
4. When you are done, click **⏹ Stop**.

Good to know:

- **First launch is slow** for Forge and Fooocus — they download several GB
  (Fooocus gets about 30 GB of models). Later launches are fast.
- **⟳ Update** updates a tool. The button turns **orange** with a number when a
  new version exists. Nothing updates unless you click it.
- **Check all for updates** (top right) just looks — it changes nothing.
- **📁** opens the tool's folder. **GitHub ↗** opens its web page.
- **🛡 Scan** checks the tool's files for danger signs (see below).

## Add your own tool

Found a cool AI project on GitHub? Click **➕ Add tool** (top right) and paste
the link. Then click **Download & set up** on its new card, and **▶ Launch**.

The app reads the project and works out how to run it by itself. It handles
Python apps, Node apps, and projects with a `run.bat` / `run.sh`. If a project
has no screen of its own, the app even **builds a simple web page for it** so
you can still use it.

If the app can't work it out (rare), open **⚙ Run config** on the card and
paste the start command from that project's README.

To remove a tool you added, click **🗑**. This also deletes its files from your disk.

## Where do the files go?

Tools and models are stored in **`C:\AItools`** on Windows, or **`~/AItools`**
on a Mac. You can change this in **⚙ Settings**.

> ⚠️ Keep this folder **out of OneDrive and iCloud**. Cloud sync breaks big
> model files. The app warns you if you pick a synced folder.

## The safety scan

Every tool gets a read-only safety check after each install and update — and you
can run it any time with **🛡 Scan**. It looks for danger signs in the files,
like hidden code, password stealers, and crypto miners. Green means "no red
flags found". Red means "read the report before you launch". It never runs any
of the code it checks.

It is a helper, not a guarantee. Only install things from people you trust.

---

## Problems?

- **A tool's first launch takes forever** — normal. It is downloading models.
  Watch its console tab to see progress.
- **"Port already in use"** — another program is using that address. Stop it,
  or change the tool's `port` in `tools.json`.
- **Mac says the app is damaged or can't be opened** — that is the one-time
  block for free apps. System Settings → Privacy & Security → **Open Anyway**.
- **Update says "couldn't reach remote"** — you are offline. Nothing was changed.
- **Kohya asks for Visual C++ or CUDA (Windows)** — install those from
  Microsoft / NVIDIA and try again.

---

## For developers

Want to change the app itself? Everything is in this repo.

- **Run from source:** double-click `Start.bat` (Windows) or run
  `bash Start.command` (Mac). They set up what they need by themselves.
- **Build the app:** `powershell -ExecutionPolicy Bypass -File Build.ps1`
  (Windows) or `bash Build_mac.sh` (Mac). These also install what they need,
  including Python 3.10 if it is missing.
- **`tools.json`** controls everything: ports, install and launch commands.
  A tool can have `"platform_overrides"` with `"windows"`, `"posix"`, `"mac"`,
  or `"linux"` blocks — that is how the built-in tools use `.bat` files on
  Windows and `.sh` files on a Mac, and skip CUDA on Macs.
- **Live edit:** the installed app follows this folder. Change anything in
  `web/` or `tools.json` and the app picks it up in about 2 seconds — no rebuild.
  Only `server.py` / `app.py` changes need a rebuild.
- **Releases are built in the cloud:** publishing a GitHub release runs
  `.github/workflows/release.yml` — it tests the app on real Windows and Mac
  machines (`ci/smoke_test.py`, 33 checks), builds both zips, and attaches them
  to the release. Every push to `main` also runs the tests on both systems.

```
AI-Launcher/
├─ app.py               the desktop window
├─ server.py            the engine: installs, launches, updates, stops tools
├─ web/                 the dashboard you see (HTML/CSS/JS)
├─ tools.json           the list of tools and how to run them (per OS)
├─ assets/              app icons (app.ico for Windows, app.icns for Mac)
├─ Start.bat / .ps1     run from source on Windows
├─ Start.command        run from source on a Mac
├─ Build.ps1            build + install the Windows app
├─ Build_mac.sh         build + install the Mac app
├─ ci/smoke_test.py     the automatic test suite
└─ .github/workflows/   cloud builds and tests
```
