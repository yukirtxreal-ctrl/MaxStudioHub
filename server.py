#!/usr/bin/env python3
"""
Max Studio Hub - local control panel for ComfyUI, SD WebUI Forge, Fooocus, and Kohya_ss.

Pure standard-library Python (no pip installs). Serves a small web UI and exposes a
JSON API to install / launch / update / stop each tool by shelling out to git + python.

Run it via Start.bat (which makes sure a suitable Python 3.10 exists first), or directly:
    python server.py
"""

import json
import os
import re
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _asset_dir():
    # read-only assets (web/, tools.json). When frozen by PyInstaller they live
    # in the temporary extraction dir.
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    # writable data (config.json) — must live next to the exe / script, never in
    # the read-only bundle.
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _asset_dir()
DATA_DIR = _data_dir()


def _live_source_dir():
    """A folder whose web/ + tools.json the app mirrors LIVE, so it follows source
    edits with no rebuild. Set by a 'live_source.txt' pointer (written at build
    time); when running from source it is the source folder itself."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(os.path.dirname(sys.executable), "live_source.txt"))
    cands.append(os.path.join(DATA_DIR, "live_source.txt"))
    for c in cands:
        try:
            if c and os.path.exists(c):
                with open(c, "r", encoding="utf-8") as f:
                    p = f.read().strip()
                if p and os.path.isdir(p):
                    return p
        except OSError:
            pass
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    return None


LIVE_SOURCE = _live_source_dir()
_src_web = os.path.join(LIVE_SOURCE, "web") if LIVE_SOURCE else None
_src_reg = os.path.join(LIVE_SOURCE, "tools.json") if LIVE_SOURCE else None
WEB_DIR = _src_web if (_src_web and os.path.isdir(_src_web)) else os.path.join(APP_DIR, "web")
_BUNDLED_REGISTRY = os.path.join(APP_DIR, "tools.json")
if _src_reg and os.path.exists(_src_reg):
    REGISTRY_PATH = _src_reg                       # live: source tools.json edits apply
else:
    REGISTRY_PATH = os.path.join(DATA_DIR, "tools.json")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DESC_CACHE_PATH = os.path.join(DATA_DIR, "repo_cache.json")
CUSTOM_PATH = os.path.join(DATA_DIR, "custom_tools.json")


def _file_mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def assets_version():
    """A token that changes when any web/ UI file is edited (for live reload)."""
    mt = 0.0
    try:
        for f in os.listdir(WEB_DIR):
            if f.lower().endswith((".html", ".css", ".js")):
                mt = max(mt, _file_mtime(os.path.join(WEB_DIR, f)))
    except OSError:
        pass
    return f"{mt:.0f}"

# Detect a local web-UI URL printed by an arbitrary app's output (Gradio /
# Streamlit / Flask / uvicorn / vite all print one), so "Open UI" works even
# when we didn't know the port in advance.
URL_RE = re.compile(
    r"https?://(?:127\.0\.0\.1|0\.0\.0\.0|localhost)(?::(\d{2,5}))?(?:/\S*)?", re.I)
CUSTOM_PALETTE = ["#22d3ee", "#a78bfa", "#34d399", "#fb7185", "#f59e0b",
                  "#60a5fa", "#f472b6", "#2dd4bf"]


def slugify(s):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s or "repo"


def _long_path(path):
    """Enable >260-char paths on Windows (deep venv trees) via the \\\\?\\ prefix."""
    p = os.path.abspath(path)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = ("\\\\?\\UNC\\" + p[2:]) if p.startswith("\\\\") else ("\\\\?\\" + p)
    return p


def _is_reparse(path):
    """True for a Windows junction or symlink (a reparse point). Used so we never
    recurse THROUGH one and delete its target outside the folder."""
    try:
        return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (OSError, AttributeError, ValueError):
        try:
            return os.path.islink(path)
        except OSError:
            return False


def _rmtree_safe(path):
    """Recursively delete `path`. Clears read-only bits and, crucially, removes
    junctions/symlinks as link entries instead of following them into their
    targets (so we can never delete data outside the folder)."""
    try:
        entries = list(os.scandir(path))
    except OSError:
        entries = []
    for e in entries:
        full = os.path.join(path, e.name)
        try:
            is_dir = e.is_dir(follow_symlinks=False)
        except OSError:
            is_dir = False
        try:
            if is_dir and not _is_reparse(full):
                _rmtree_safe(full)
            else:
                try:
                    os.chmod(full, stat.S_IWRITE)
                except OSError:
                    pass
                if is_dir:                 # junction / dir-symlink: drop the link only
                    os.rmdir(full)
                else:
                    os.remove(full)
        except OSError:
            pass
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def force_rmtree(path):
    """Robustly delete a directory tree on Windows: handles read-only git objects,
    deep (>260 char) venv paths, briefly-locked files (retry), and never follows
    junctions/symlinks out of the tree. Returns True only if fully gone."""
    if not os.path.exists(path):
        return True
    lp = _long_path(path)
    for _ in range(12):
        _rmtree_safe(lp)
        if not os.path.exists(path):
            return True
        time.sleep(0.5)
    return not os.path.exists(path)


def is_safe_install_root(path):
    """Reject drive/UNC roots and top-level system folders so a misconfigured
    install root can never let tool removal delete system data."""
    try:
        p = os.path.abspath(path)
    except Exception:
        return False
    drive, tail = os.path.splitdrive(p)
    parts = [x for x in tail.replace("/", "\\").split("\\") if x]
    if not parts:                       # a drive root such as C:\ or a UNC root
        return False
    bad = {"windows", "system32", "syswow64", "program files",
           "program files (x86)", "programdata", "users", "$recycle.bin", "boot"}
    if parts[0].lower() in bad and len(parts) < 2:
        return False
    sysroot = os.environ.get("SystemRoot", "")
    if sysroot and os.path.normcase(p) == os.path.normcase(os.path.abspath(sysroot)):
        return False
    return True


# Repo auto-run detection
PY_ENTRY_NAMES = ["app.py", "webui.py", "gui.py", "main.py", "run.py", "start.py",
                  "launch.py", "server.py", "demo.py", "run_demo.py", "example.py",
                  "examples.py", "gradio_app.py", "streamlit_app.py", "infer.py",
                  "inference.py", "cli.py", "run_gui.py", "__main__.py"]
WEB_IMPORTS = ("gradio", "streamlit", "flask", "fastapi", "uvicorn", "dash", "nicegui")
DETECT_VERSION = 2  # bump when detection logic changes, so custom tools re-detect
# import name -> pip package, for the common mismatches
IMPORT_MAP = {
    "cv2": "opencv-python", "PIL": "pillow", "sklearn": "scikit-learn",
    "skimage": "scikit-image", "yaml": "pyyaml", "bs4": "beautifulsoup4",
    "OpenGL": "PyOpenGL", "Crypto": "pycryptodome", "dotenv": "python-dotenv",
    "fitz": "PyMuPDF", "serial": "pyserial", "usb": "pyusb", "magic": "python-magic",
    "google": "google-api-python-client", "Xlib": "python-xlib", "gi": "PyGObject",
    "cairo": "pycairo", "win32api": "pywin32", "win32com": "pywin32",
    "imageio_ffmpeg": "imageio-ffmpeg", "moviepy": "moviepy",
}
# import names that are framework-internal / not pip-installable
DEP_DENYLIST = {"folder_paths", "comfy", "comfy_extras", "nodes", "execution",
                "latent_preview", "model_management", "comfyui", "server",
                "custom_nodes", "aiohttp_cors", "__future__"}

# A self-contained Gradio app that introspects a repo function and exposes it as
# a simple upload-input -> run -> see-output web page. Written into a repo when it
# ships no UI of its own, so non-coders can actually use the tool.
WRAPPER_TEMPLATE = r'''# Auto-generated by Max Studio Hub - a simple web UI for this repo's tool.
import os, sys, inspect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gradio as gr
try:
    import numpy as np
except Exception:
    np = None

__IMPORT_LINE__
_FN = __FUNC_REF__

IMG = ("image", "img", "rgb", "bgr", "input_image", "inputimage", "picture",
       "photo", "frame", "pixel", "mask", "im")
AUD = ("audio", "wav", "sound", "speech", "voice")
TXT = ("text", "prompt", "string", "content", "input_text", "sentence", "query",
       "caption", "message", "msg", "description")
PATHS = ("path", "file", "filepath", "filename", "input_path", "dir", "directory")


def _is_image(v):
    if np is not None and isinstance(v, np.ndarray) and v.ndim in (2, 3):
        return True
    try:
        from PIL import Image as _I
        if isinstance(v, _I.Image):
            return True
    except Exception:
        pass
    if isinstance(v, str) and os.path.isfile(v) and v.lower().endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff")):
        return True
    return False


def _norm_img(v):
    if np is not None and isinstance(v, np.ndarray):
        a = v
        if a.dtype.kind == "f":
            lo, hi = float(a.min()), float(a.max())
            a = ((a - lo) / (hi - lo + 1e-9) * 255.0).astype("uint8")
        elif a.dtype != np.uint8:
            a = a.astype("uint8")
        return a
    return v


_sig = inspect.signature(_FN)
_params = [p for p in _sig.parameters.values()
           if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
_comps = []
for _p in _params:
    _name = _p.name
    _ln = _name.lower()
    _d = None if _p.default is inspect.Parameter.empty else _p.default
    _ann = _p.annotation
    if any(k in _ln for k in IMG) and not isinstance(_d, (int, float, bool)):
        _comps.append((_name, gr.Image(label=_name, type="numpy")))
    elif any(k in _ln for k in AUD):
        _comps.append((_name, gr.Audio(label=_name, type="filepath")))
    elif isinstance(_d, bool) or _ann is bool:
        _dv = False if _ln in ("debug", "verbose", "show", "plot", "display",
                               "visualize") else bool(_d)
        _comps.append((_name, gr.Checkbox(label=_name, value=_dv)))
    elif isinstance(_d, float) or _ann is float:
        _dv = float(_d) if _d is not None else 0.0
        if 0.0 <= _dv <= 1.0:
            _comps.append((_name, gr.Slider(0, 1, value=_dv, step=0.01, label=_name)))
        else:
            _comps.append((_name, gr.Number(value=_dv, label=_name)))
    elif isinstance(_d, int) or _ann is int:
        _comps.append((_name, gr.Number(value=int(_d) if _d is not None else 0,
                                        precision=0, label=_name)))
    elif any(k in _ln for k in PATHS):
        _comps.append((_name, gr.File(label=_name)))
    else:
        _comps.append((_name, gr.Textbox(label=_name, value="" if _d is None else str(_d))))

_inputs = [c[1] for c in _comps]


def _run(*vals):
    kwargs = {}
    for (name, comp), v in zip(_comps, vals):
        _p = _sig.parameters[name]
        _has_def = _p.default is not inspect.Parameter.empty
        # leave optional params at their real default (e.g. grid_size=None) when
        # the field is left blank, instead of forcing an empty string through
        if v is None and _has_def:
            continue
        if isinstance(v, str) and v.strip() == "" and _has_def and not isinstance(_p.default, str):
            continue
        kwargs[name] = v
    result = _FN(**kwargs)
    items = list(result) if isinstance(result, (tuple, list)) else [result]
    imgs, texts, files = [], [], []
    for it in items:
        if _is_image(it):
            imgs.append(it if isinstance(it, str) else _norm_img(it))
        elif isinstance(it, (int, float, bool)):
            texts.append(str(it))
        elif isinstance(it, str):
            (files if os.path.isfile(it) else texts).append(it)
        elif it is not None:
            texts.append(repr(it)[:1000])
    return (imgs[0] if imgs else None, imgs or None,
            "\n".join(texts), files[0] if files else None)


_outputs = [gr.Image(label="Result"), gr.Gallery(label="All results"),
            gr.Textbox(label="Output / info", lines=3), gr.File(label="Download")]
demo = gr.Interface(fn=_run, inputs=_inputs, outputs=_outputs, title="__TITLE__",
                    description="Auto-generated by Max Studio Hub - provide your input and click Submit.")
_port = int(os.environ.get("MAXSTUDIO_PORT", "7860"))
print("Max Studio Hub: launching the web UI on port %d ..." % _port, flush=True)
demo.launch(server_name="127.0.0.1", server_port=_port, inbrowser=False, show_error=True)
'''


def _seed_registry():
    """In the frozen exe, tools.json ships inside the read-only bundle. Copy it
    next to the exe on first run so it's user-editable and the disk watcher can
    live-reload edits. In source mode the paths are identical and this is a no-op."""
    try:
        if (os.path.abspath(REGISTRY_PATH) != os.path.abspath(_BUNDLED_REGISTRY)
                and os.path.exists(_BUNDLED_REGISTRY)
                and not os.path.exists(REGISTRY_PATH)):
            shutil.copyfile(_BUNDLED_REGISTRY, REGISTRY_PATH)
    except Exception:
        pass


_seed_registry()

# Windows process-creation flags
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

GIT = shutil.which("git")


# --------------------------------------------------------------------------- #
#  Small helpers
# --------------------------------------------------------------------------- #
def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"[warn] could not read {path}: {e}")
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def onedrive_roots():
    roots = []
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = os.environ.get(var)
        if v:
            roots.append(os.path.normcase(os.path.abspath(v)))
    return roots


def is_under_onedrive(path):
    try:
        p = os.path.normcase(os.path.abspath(path))
    except Exception:
        return False
    if "\\onedrive" in p:
        return True
    return any(p == r or p.startswith(r + os.sep) for r in onedrive_roots())


def port_open(port, host="127.0.0.1", timeout=0.35):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run_capture(argv, cwd=None, timeout=25):
    """Run a command, return (rc, stdout_stripped). Never raises."""
    try:
        r = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode, (r.stdout or "").strip()
    except Exception as e:
        return 1, f"{e}"


# --------------------------------------------------------------------------- #
#  Manager: owns config, registry and per-tool runtime state
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  Security scan — read-only heuristic risk flags (never executes any code)
# --------------------------------------------------------------------------- #
SCAN_SKIP_DIRS = {".git", "venv", ".venv", "env", "__pycache__", "node_modules",
                  "models", "model", "output", "outputs", "input", "temp", "tmp",
                  ".cache", "site-packages", ".idea", ".vs", "wheels", "dist-info"}
SCAN_TEXT_EXT = {".py", ".pyw", ".js", ".mjs", ".ts", ".bat", ".cmd", ".ps1",
                 ".psm1", ".sh", ".bash", ".vbs", ".rb", ".pl"}
SCAN_PICKLE_EXT = {".ckpt", ".pt", ".pth", ".pkl", ".pickle", ".bin"}
SCAN_EXEC_EXT = {".exe", ".scr", ".msi", ".com", ".jar", ".apk", ".dll", ".dylib"}
SCAN_MAX_FILE = 1_500_000
SCAN_MAX_FILES = 9000

_HIGH = [(re.compile(p, re.I), d) for p, d in [
    (r"powershell[^\n]*-e(nc|ncodedcommand)\b", "PowerShell encoded command (obfuscated payload)"),
    (r"-EncodedCommand\b", "PowerShell -EncodedCommand (obfuscated payload)"),
    (r"certutil[^\n]*(-urlcache|/urlcache|-decode|/decode)", "certutil used to download/decode a payload"),
    (r"bitsadmin[^\n]*/transfer", "bitsadmin transfer (malware download technique)"),
    (r"discord(app)?\.com/api/webhooks/", "Discord webhook (data-exfiltration channel)"),
    (r"api\.telegram\.org/bot", "Telegram bot API (exfiltration channel)"),
    (r"stratum\+tcp://", "Crypto-miner pool address"),
    (r"\bxmrig\b", "Crypto-miner reference (xmrig)"),
    (r"(curl|wget)[^\n|]*\|\s*(bash|sh)\b", "Pipe-to-shell remote execution (curl/wget | sh)"),
    (r"os\.(system|popen)\(\s*[\"'][^\"']*(powershell|cmd\.exe|/c |curl |wget |certutil)",
     "Shells out to powershell/cmd/curl/wget"),
    (r"\b(nc|ncat|netcat)\b[^\n]*\s-e\b", "Reverse shell (netcat -e)"),
    (r"CurrentVersion.{0,3}Run\b", "References a Windows autostart Run key"),
]]
_MEDIUM = [(re.compile(p, re.I), d) for p, d in [
    (r"\beval\s*\(", "eval() — dynamic code execution"),
    (r"\bexec\s*\(", "exec() — dynamic code execution"),
    (r"os\.system\s*\(", "os.system() — runs a shell command"),
    (r"subprocess\.(Popen|call|run|check_output|check_call|getoutput)",
     "subprocess — runs external commands"),
    (r"pickle\.load", "pickle.load — can execute code while unpickling"),
    (r"torch\.load\s*\(", "torch.load — loads pickle (code-exec risk unless weights_only)"),
    (r"__import__\s*\(", "__import__ — dynamic import"),
    (r"(requests\.(get|post|put)|urllib\.request\.urlopen)\s*\(", "outbound network request"),
    (r"socket\.socket\s*\(", "raw network socket"),
    (r"shutil\.rmtree\s*\(", "recursive delete"),
    (r"os\.(remove|unlink)\s*\(", "file deletion"),
    (r"marshal\.loads\s*\(", "marshal.loads — executes serialized code objects"),
]]
_EXEC_EVAL_RE = re.compile(r"\b(exec|eval)\s*\(", re.I)
_DECODE_RE = re.compile(
    r"(base64\.b64decode|bytes\.fromhex|codecs\.decode|zlib\.decompress|"
    r"marshal\.loads|\.decode\(\s*['\"]hex)", re.I)


def _scan_text(rel, text):
    """Return findings for one text file. File-level obfuscation check + per-line."""
    res = []
    m = _EXEC_EVAL_RE.search(text)
    if m and _DECODE_RE.search(text):
        ln = text.count("\n", 0, m.start()) + 1
        res.append({"severity": "high", "file": rel, "line": ln,
                    "reason": "Executes code built from decoded/obfuscated data "
                              "(exec/eval + base64/hex/marshal).",
                    "snippet": text.split("\n")[ln - 1].strip()[:180]})
    for i, line in enumerate(text.split("\n"), 1):
        seg = line if len(line) <= 2000 else line[:2000]
        for rx, reason in _HIGH:
            if rx.search(seg):
                res.append({"severity": "high", "file": rel, "line": i,
                            "reason": reason, "snippet": seg.strip()[:180]})
        for rx, reason in _MEDIUM:
            if rx.search(seg):
                res.append({"severity": "medium", "file": rel, "line": i,
                            "reason": reason, "snippet": seg.strip()[:180]})
        if len(res) >= 60:
            break
    return res


class ToolRuntime:
    def __init__(self):
        self.lock = threading.RLock()
        self.state = "idle"          # idle | installing | updating | running | stopping
        self.proc = None             # running launch Popen
        self.pid = None
        self.ready = False           # web port is accepting connections
        self.log = deque(maxlen=6000)
        self.seq = 0
        self.error = None
        self.update = {"checked": 0, "behind": None, "ahead": None,
                       "subject": None, "error": None}
        self.commit = {"short": None, "subject": None, "branch": None}
        self.scanning = False
        self.scan = {"done": False, "verdict": None, "high": 0, "medium": 0,
                     "pickles": 0, "execs": 0, "scanned": 0, "when": 0,
                     "findings": [], "error": None}
        self.detected_url = None     # web URL discovered in a running app's output


class Manager:
    def __init__(self):
        self.registry = load_json(REGISTRY_PATH, {}) or {}
        self.rt = {}
        self._rebuild_tools()

        cfg = load_json(CONFIG_PATH, {}) or {}
        self.install_root = cfg.get("install_root") or self.registry.get(
            "default_install_root", "C:\\AItools")
        self._save_config()
        self._python_cache = {}

        # disk-watcher state: revision bumps whenever the watcher notices an
        # on-disk change so the UI can "follow up" automatically.
        self.revision = 0
        self._watch_started = False
        self._stop_watch = False
        self._sig_cache = {}
        self._reg_mtime = self._mtime(REGISTRY_PATH)
        self._cfg_mtime = self._mtime(CONFIG_PATH)
        self._custom_mtime = self._mtime(CUSTOM_PATH)

        # GitHub "About" descriptions, refreshed from the repos and cached to disk
        # so each card's intro tracks the repo's introduction.
        self.repo_desc = load_json(DESC_CACHE_PATH, {}) or {}
        self._last_desc_fetch = 0.0

    # ----- config -----
    def _save_config(self):
        save_json(CONFIG_PATH, {"install_root": self.install_root})
        # keep the watcher from treating our own write as an external change
        self._cfg_mtime = self._mtime(CONFIG_PATH)

    def set_install_root(self, path):
        if not is_safe_install_root(path):
            return False
        self.install_root = os.path.abspath(path)
        self._save_config()
        self.revision += 1
        return True

    # ----- tool registry (built-in + user-added custom tools) -----
    def _load_custom(self):
        data = load_json(CUSTOM_PATH, []) or []
        return data if isinstance(data, list) else []

    def _rebuild_tools(self):
        base = list(self.registry.get("tools", []))
        seen = {t["id"] for t in base}
        merged = base
        for c in self._load_custom():
            if c.get("id") and c["id"] not in seen:
                merged.append(c)
                seen.add(c["id"])
        self.tools = merged
        self.by_id = {t["id"]: t for t in merged}
        for t in merged:
            self.rt.setdefault(t["id"], ToolRuntime())

    def _save_custom(self):
        custom = [t for t in self.tools if t.get("custom")]
        save_json(CUSTOM_PATH, custom)
        self._custom_mtime = self._mtime(CUSTOM_PATH)

    def add_tool(self, repo_url, name=None):
        repo_url = (repo_url or "").strip()
        if not repo_url:
            return False, "Enter a repository URL.", None
        # normalise: accept "owner/repo", full https, or .git URLs
        m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s#?]+)", repo_url, re.I)
        if m:
            owner, repo = m.group(1), re.sub(r"\.git$", "", m.group(2))
            web = f"https://github.com/{owner}/{repo}"
            clone = web + ".git"
        elif re.fullmatch(r"[\w.-]+/[\w.-]+", repo_url):
            owner, repo = repo_url.split("/")
            repo = re.sub(r"\.git$", "", repo)
            web = f"https://github.com/{owner}/{repo}"
            clone = web + ".git"
        elif repo_url.endswith(".git") or repo_url.startswith("http"):
            web = re.sub(r"\.git$", "", repo_url)
            clone = repo_url if repo_url.endswith(".git") else repo_url + ".git"
            repo = re.sub(r"\.git$", "", repo_url.rstrip("/").split("/")[-1])
        else:
            return False, "That doesn't look like a git/GitHub URL.", None

        if not GIT:
            return False, "Git is required to add repositories.", None
        # verify it exists & get the default branch
        rc, out = run_capture([GIT, "ls-remote", "--symref", clone, "HEAD"], timeout=25)
        if rc != 0:
            return False, "Couldn't reach that repository (check the URL / your connection).", None
        bm = re.search(r"refs/heads/(\S+)\s+HEAD", out)
        branch = bm.group(1) if bm else "main"

        base_id = slugify(repo)
        tid = base_id
        i = 2
        while tid in self.by_id:
            tid = f"{base_id}-{i}"
            i += 1
        dir_name = re.sub(r"[^\w.-]", "_", repo).strip(". ") or tid
        color = CUSTOM_PALETTE[len(self.tools) % len(CUSTOM_PALETTE)]
        tool = {
            "id": tid, "name": name.strip() if name else repo,
            "emoji": "📦", "color": color,
            "description": f"Custom repo — {web.replace('https://github.com/', '')}",
            "repo": web, "clone_url": clone, "branch": branch, "submodules": True,
            "dir": dir_name, "python_version": "3.10", "needs_venv": False,
            "port": 0, "url": "", "auto_update_on_launch": False,
            "first_launch_installs": False, "install_steps": [], "launch_cmd": [],
            "launch_env": {}, "update_steps": [], "notes": "",
            "custom": True, "kind": "unknown", "configured": False,
            "detect": {},
        }
        self.tools.append(tool)
        self.by_id[tid] = tool
        self.rt.setdefault(tid, ToolRuntime())
        self._save_custom()
        self.revision += 1
        # fetch its GitHub description in the background
        threading.Thread(target=self.refresh_descriptions, daemon=True).start()
        return True, "added", tool

    def remove_tool(self, tid, delete_files=True):
        tool = self.by_id.get(tid)
        if not tool or not tool.get("custom"):
            return False, "Only user-added tools can be removed."
        rt = self.rt.get(tid)
        if rt and rt.state == "running":
            return False, "Stop the tool before removing it."

        leftover = False
        if delete_files:
            d = os.path.abspath(self.tool_dir(tool))
            root = os.path.abspath(self.install_root)
            # Only ever delete a DIRECT child folder of a sane install root — this
            # blocks '..' traversal, multi-level paths, and a bogus install root.
            safe = (os.path.isdir(d) and not _is_reparse(d)
                    and is_safe_install_root(root)
                    and os.path.normcase(os.path.dirname(d)) == os.path.normcase(root)
                    and (tool.get("dir") or "").strip(" ./\\") != "")
            if safe:
                self.log(tid, "sys", f"Deleting {d} …")
                if not force_rmtree(d):
                    leftover = True

        self.tools = [t for t in self.tools if t["id"] != tid]
        self.by_id.pop(tid, None)
        self.rt.pop(tid, None)
        self.repo_desc.pop(tid, None)
        self._sig_cache.pop(tid, None)        # don't let the watcher resurrect it
        self._save_custom()
        try:
            save_json(DESC_CACHE_PATH, self.repo_desc)   # drop its cached blurb
        except Exception:
            pass
        self.revision += 1
        if leftover:
            return True, ("removed from the app, but some files were locked and "
                          "left on disk — close anything using them, then delete "
                          "the folder manually.")
        return True, "removed"

    # ----- auto-detect how to set up & run an arbitrary repo -----
    @staticmethod
    def _npm_script(tdir):
        try:
            with open(os.path.join(tdir, "package.json"), "r",
                      encoding="utf-8", errors="ignore") as f:
                scripts = (json.load(f).get("scripts") or {})
            for s in ("dev", "start", "serve", "preview", "develop"):
                if s in scripts:
                    return s
        except Exception:
            pass
        return None

    def _ensure_node(self, tid):
        if shutil.which("node"):
            return True
        cand = r"C:\Program Files\nodejs\node.exe"
        if os.path.exists(cand):
            os.environ["PATH"] = os.path.dirname(cand) + os.pathsep + os.environ.get("PATH", "")
            return True
        self.log(tid, "sys", "Node.js not found — installing it via winget (one-time)…")
        run_capture(["winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS",
                     "--accept-source-agreements", "--accept-package-agreements"],
                    timeout=400)
        if os.path.exists(cand):
            os.environ["PATH"] = os.path.dirname(cand) + os.pathsep + os.environ.get("PATH", "")
            return True
        return bool(shutil.which("node"))

    @staticmethod
    def _local_module_names(tdir, root_files):
        names = {"src", "tests", "test", "docs", "assets", "images", "examples"}
        for f in root_files:
            if f.lower().endswith(".py"):
                names.add(f[:-3].lower())
            elif os.path.isdir(os.path.join(tdir, f)):
                names.add(f.lower())
        for sub in ("src",):
            p = os.path.join(tdir, sub)
            if os.path.isdir(p):
                try:
                    for f in os.listdir(p):
                        names.add(f.lower().replace(".py", ""))
                except OSError:
                    pass
        return names

    def _scan_entry_imports(self, path, local_names):
        """Third-party packages imported at the top of an entry script, so we can
        install what a demo/example actually needs (e.g. cv2 -> opencv-python)."""
        pip = set()
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(20000)
        except OSError:
            return pip
        std = getattr(sys, "stdlib_module_names", frozenset())
        for m in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z0-9_.]+)", text, re.M):
            top = m.group(1).split(".")[0]
            if (not top or top.startswith("_") or top.lower() in local_names
                    or top in std):
                continue
            pip.add(IMPORT_MAP.get(top, top))
            if len(pip) >= 25:
                break
        return pip

    def _find_web_entry(self, tdir, root_files):
        for f in sorted(f for f in root_files if f.lower().endswith(".py")):
            try:
                with open(os.path.join(tdir, f), "r", encoding="utf-8",
                          errors="ignore") as fp:
                    head = fp.read(6000)
            except OSError:
                continue
            for w in WEB_IMPORTS:
                if re.search(r"(?:^|\n)\s*(?:import|from)\s+" + w + r"\b", head):
                    return f, w
        return None, None

    def _find_main_guard(self, tdir):
        try:
            entries = os.listdir(tdir)
        except OSError:
            return None
        roots = [f for f in entries if f.lower().endswith(".py")]
        for f in sorted(roots):
            try:
                with open(os.path.join(tdir, f), "r", encoding="utf-8",
                          errors="ignore") as fp:
                    if re.search(r"__name__\s*==\s*['\"]__main__['\"]", fp.read()):
                        return f
            except OSError:
                continue
        return None

    def _readme_run_command(self, tdir, root_files):
        """Extract a documented run command from the README that points at a file
        that actually exists in the repo."""
        low = {f.lower(): f for f in root_files}
        readme = (low.get("readme.md") or low.get("readme.rst")
                  or low.get("readme.txt") or low.get("readme"))
        if not readme:
            return None
        try:
            with open(os.path.join(tdir, readme), "r", encoding="utf-8",
                      errors="ignore") as f:
                text = f.read(60000)
        except OSError:
            return None
        py = "${venv_python}"
        for line in text.splitlines():
            s = line.strip().lstrip("$> ").strip().strip("`").strip()
            m = re.match(r"streamlit\s+run\s+([^\s`'\"]+\.py)", s, re.I)
            if m and os.path.exists(os.path.join(tdir, m.group(1))):
                return ([py, "-m", "streamlit", "run", m.group(1),
                         "--server.port", "${port}", "--server.headless", "true"],
                        8501, f"README: streamlit run {m.group(1)}", m.group(1))
            m = re.match(r"python3?\s+([^\s`'\"-][^\s`'\"]*\.py)", s, re.I)
            if m and os.path.exists(os.path.join(tdir, m.group(1))):
                return ([py, m.group(1)], 0, f"README: python {m.group(1)}", m.group(1))
            m = re.match(r"python3?\s+-m\s+([\w.]+)", s, re.I)
            if m:
                return ([py, "-m", m.group(1)], 0, f"README: python -m {m.group(1)}", None)
        return None

    def _find_app_entry(self, tdir, root_files, reqtext, py):
        """Return a launch for a repo that already ships an interactive UI
        (Gradio / Streamlit / Flask / FastAPI), else None."""
        low = {f.lower(): f for f in root_files}
        # streamlit declared in requirements
        if "streamlit" in reqtext:
            target = low.get("streamlit_app.py") or low.get("app.py")
            if not target:
                pys = sorted(f for f in root_files if f.lower().endswith(".py"))
                target = pys[0] if pys else None
            if target:
                return ([py, "-m", "streamlit", "run", target, "--server.port",
                         "${port}", "--server.headless", "true"], 8501,
                        f"Streamlit app — runs `streamlit run {target}`.")
        # a root script that imports a web/UI framework
        we, fw = self._find_web_entry(tdir, root_files)
        if we:
            if fw == "streamlit":
                return ([py, "-m", "streamlit", "run", we, "--server.port", "${port}",
                         "--server.headless", "true"], 8501,
                        f"Streamlit app — `streamlit run {we}`.")
            return ([py, we], 0, f"Runs `python {we}` ({fw} web app).")
        return None

    def _extract_repo_function(self, tdir, root_files, local_names):
        """Find an importable function the repo exposes (from its example/README/
        source), so we can wrap it in a UI. Returns (import_line, mod) or None."""
        low = {f.lower(): f for f in root_files}
        order = []
        for n in ("example.py", "demo.py", "examples.py", "usage.py", "quickstart.py"):
            if low.get(n):
                order.append(low[n])
        for n in ("readme.md", "readme.rst", "readme"):
            if low.get(n):
                order.append(low[n])
        order += sorted(f for f in root_files if f.lower().endswith(".py"))
        for fname in order:
            res = self._scan_source_for_func(os.path.join(tdir, fname), local_names)
            if res:
                return res
        return None

    @staticmethod
    def _scan_source_for_func(path, local_names):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(40000)
        except OSError:
            return None
        imports = {}   # alias -> (module, original)
        for m in re.finditer(r"from\s+([\w.]+)\s+import\s+([^\n#()]+)", text):
            mod = m.group(1)
            if mod.split(".")[0].lower() not in local_names:
                continue
            for part in m.group(2).split(","):
                pm = re.match(r"\s*([A-Za-z_]\w*)(?:\s+as\s+(\w+))?", part)
                if pm:
                    orig = pm.group(1)
                    alias = pm.group(2) or orig
                    if orig != "*":
                        imports[alias] = (mod, orig)
        if not imports:
            return None
        # prefer an imported name that is actually called like a function
        for alias, (mod, orig) in imports.items():
            if re.search(r"\b" + re.escape(alias) + r"\s*\(", text):
                return (f"from {mod} import {orig} as _FN_TARGET", mod)
        alias, (mod, orig) = next(iter(imports.items()))
        return (f"from {mod} import {orig} as _FN_TARGET", mod)

    def _scan_imports_for_wrapper(self, tdir, root_files, target_mod, local_names):
        """Third-party packages the repo (its root scripts + the target module's
        package) imports, so the generated UI can actually import the function."""
        paths = [os.path.join(tdir, f) for f in root_files if f.lower().endswith(".py")]
        if target_mod:
            md = os.path.join(tdir, *target_mod.split("."))
            if os.path.isdir(md):
                for r, _d, fs in os.walk(md):
                    for f in fs:
                        if f.endswith(".py"):
                            paths.append(os.path.join(r, f))
            elif os.path.exists(md + ".py"):
                paths.append(md + ".py")
        std = getattr(sys, "stdlib_module_names", frozenset())
        pip = set()
        for p in paths[:120]:
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(20000)
            except OSError:
                continue
            for m in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z0-9_.]+)", text, re.M):
                top = m.group(1).split(".")[0]
                if (not top or top.startswith("_") or top.lower() in local_names
                        or top in std or top in DEP_DENYLIST):
                    continue
                pip.add(IMPORT_MAP.get(top, top))
        return pip

    def _generate_wrapper(self, tool, tdir, root_files, local_names):
        """Write a Gradio UI wrapper for a library/CLI repo. Returns
        (launch_cmd, port, notes, required_deps) or None."""
        found = self._extract_repo_function(tdir, root_files, local_names)
        if not found:
            return None
        import_line, target_mod = found
        code = (WRAPPER_TEMPLATE
                .replace("__IMPORT_LINE__", import_line)
                .replace("__FUNC_REF__", "_FN_TARGET")
                .replace("__TITLE__", tool.get("name", "Tool").replace('"', "'")))
        try:
            with open(os.path.join(tdir, "_maxstudio_ui.py"), "w", encoding="utf-8") as f:
                f.write(code)
        except OSError as e:
            self.log(tool["id"], "err", f"Couldn't write the UI wrapper: {e}")
            return None
        deps = self._scan_imports_for_wrapper(tdir, root_files, target_mod, local_names)
        deps.discard("gradio")
        required = ["gradio"] + sorted(deps)
        notes = ("No app in this repo — Max Studio Hub built a simple web UI around "
                 f"`{import_line.split(' import ')[1].split(' as ')[0]}`.")
        return (["${venv_python}", "_maxstudio_ui.py"], 7861, notes, required)

    def _detect_and_configure(self, tool):
        tid = tool["id"]
        tdir = self.tool_dir(tool)
        try:
            root_files = set(os.listdir(tdir))
        except OSError:
            root_files = set()
        root_files.discard("_maxstudio_ui.py")  # ignore our own generated wrapper
        low = {f.lower(): f for f in root_files}

        def rget(name):
            return low.get(name.lower())

        py = "${venv_python}"
        install, launch, env = [], [], {}
        needs_venv = False
        kind = "unknown"
        port = 0
        notes = ""
        first_launch = False
        candidates = []

        reqtext = ""
        rq = rget("requirements.txt")
        if rq:
            try:
                with open(os.path.join(tdir, rq), "r", encoding="utf-8",
                          errors="ignore") as f:
                    reqtext = f.read().lower()
            except OSError:
                pass

        has_pkgjson = bool(rget("package.json"))
        has_pyproj = bool(rget("pyproject.toml") or rget("setup.py"))
        has_py = bool(rq) or has_pyproj or bool(rget("environment.yml")) \
            or any(f.lower().endswith(".py") for f in root_files)
        setup_bat = rget("setup.bat") or rget("install.bat")
        run_bat = (rget("run.bat") or rget("start.bat") or rget("webui-user.bat")
                   or rget("webui.bat") or rget("gui.bat") or rget("run_gui.bat"))
        dockerfile = rget("docker-compose.yml") or rget("compose.yaml") or rget("dockerfile")

        if has_pkgjson:
            kind = "node"
            pm = "pnpm" if rget("pnpm-lock.yaml") else ("yarn" if rget("yarn.lock") else "npm")
            install = [[pm, "install"]]
            script = self._npm_script(tdir)
            launch = [pm, "run", script] if script else [pm, "start"]
            notes = f"Node.js app — `{pm} install` then `{pm} run {script or 'start'}`."
            candidates = [script or "start"]
        elif has_py and run_bat and not (rq or has_pyproj):
            kind = "script"
            install = [[setup_bat]] if setup_bat else []
            first_launch = not setup_bat
            launch = [run_bat]
            notes = (f"Runs its own `{run_bat}`" +
                     (f", set up by `{setup_bat}`." if setup_bat else
                      " (it sets itself up on first run)."))
        elif has_py:
            kind = "python"
            needs_venv = True
            install = [[py, "-m", "pip", "install", "--upgrade", "pip", "wheel"]]
            if rq:
                install.append([py, "-m", "pip", "install", "-r", rq])
            elif has_pyproj:
                install.append([py, "-m", "pip", "install", "."])
            candidates = sorted(f for f in root_files if f.lower().endswith(".py"))[:25]
            local_names = self._local_module_names(tdir, root_files)
            entry = None

            # A) the repo already ships an interactive UI → run it as-is
            app = self._find_app_entry(tdir, root_files, reqtext, py)
            if app:
                launch, port, notes = app
            else:
                # B) a library / tool with no UI → BUILD a simple web UI for it
                wrap = self._generate_wrapper(tool, tdir, root_files, local_names)
                if wrap:
                    launch, port, notes, req = wrap
                    env = {"MAXSTUDIO_PORT": "${port}"}
                    install.append([py, "-m", "pip", "install", "gradio"])
                    others = [d for d in req if d != "gradio"]
                    if others:
                        install.append({"besteffort": [py, "-m", "pip", "install", *others]})
                    kind = "python-ui"
                else:
                    # C) fall back to running a script the repo provides
                    rc = self._readme_run_command(tdir, root_files)
                    if rc:
                        launch, port, notes, entry = rc
                    if not launch:
                        for c in PY_ENTRY_NAMES:
                            if rget(c):
                                entry = rget(c)
                                break
                        if entry:
                            launch = [py, entry]
                            notes = f"Runs `python {entry}`."
                    if not launch:
                        mg = self._find_main_guard(tdir)
                        if mg:
                            launch = [py, mg]
                            entry = mg
                            notes = f"Runs `python {mg}`."
                    if not launch and run_bat:
                        launch = [run_bat]
                        needs_venv = False
                        notes = f"Runs `{run_bat}`."
                    if not launch:
                        pys = [f for f in root_files if f.lower().endswith(".py")]
                        if len(pys) == 1:
                            entry = pys[0]
                            launch = [py, entry]
                            notes = f"Runs `python {entry}`."
                    if entry and needs_venv:
                        extra = self._scan_entry_imports(
                            os.path.join(tdir, entry), local_names)
                        extra = {p for p in extra if p.lower() not in reqtext}
                        if extra:
                            install.append({"besteffort":
                                            [py, "-m", "pip", "install", *sorted(extra)]})
                            notes += f" (auto-installs: {', '.join(sorted(extra))})"
            if not launch:
                notes = ("Python repo — couldn't pick how to run it automatically. Open "
                         "⚙ Run config and paste the command from the README.")
        elif run_bat or setup_bat:
            kind = "script"
            install = [[setup_bat]] if setup_bat else []
            launch = [run_bat] if run_bat else []
            notes = "Script-based repo (Windows .bat)."
        elif dockerfile and shutil.which("docker"):
            kind = "docker"
            launch = ["docker", "compose", "up"]
            notes = "Docker project — runs `docker compose up` (Docker Desktop must be running)."
        elif rget("index.html"):
            kind = "static"
            launch = ["${python}", "-m", "http.server", "${port}"]
            port = 8080
            notes = "Static web page — served locally."
        else:
            notes = ("Couldn't auto-detect how to run this repo. Open ⚙ Run config "
                     "and enter the command from its README.")

        tool["kind"] = kind
        tool["needs_venv"] = needs_venv
        tool["install_steps"] = install
        tool["launch_cmd"] = launch
        tool["launch_env"] = env
        tool["first_launch_installs"] = first_launch
        if port:
            tool["port"] = port
            tool["url"] = f"http://127.0.0.1:{port}"
        tool["notes"] = notes
        tool["configured"] = True
        tool["detect_version"] = DETECT_VERSION
        tool["detect"] = {"kind": kind, "candidates": candidates,
                          "needs_node": kind == "node",
                          "run": " ".join(launch) if launch else ""}
        self._save_custom()
        self.log(tid, "sys", f"Detected: {notes}")
        if launch:
            self.log(tid, "cmd", "Run command → " + " ".join(launch))
        return kind

    def configure(self, tid, run_cmd=None, port=None, pip=None, python_version=None):
        """Manual override for a custom tool's run command / port / deps."""
        tool = self.by_id.get(tid)
        if not tool or not tool.get("custom"):
            return False, "Only user-added tools can be reconfigured."
        if python_version:
            tool["python_version"] = python_version.strip()
        if run_cmd is not None:
            try:
                parts = shlex.split(run_cmd.strip())
            except ValueError:
                parts = run_cmd.strip().split()
            if parts:
                head = parts[0].lower()
                if head in ("python", "python.exe", "py"):
                    parts[0] = "${venv_python}"
                    tool["needs_venv"] = True
                tool["launch_cmd"] = parts
        if pip is not None:
            pkgs = pip.split()
            if pkgs:
                tool["needs_venv"] = True
                tool["install_steps"] = [
                    ["${venv_python}", "-m", "pip", "install", "--upgrade", "pip"],
                    ["${venv_python}", "-m", "pip", "install", *pkgs]]
            else:
                tool["install_steps"] = []
        if port is not None:
            try:
                p = int(port)
                tool["port"] = p
                tool["url"] = f"http://127.0.0.1:{p}" if p else ""
            except (TypeError, ValueError):
                pass
        tool["configured"] = True
        self._save_custom()
        self.revision += 1
        return True, "saved"

    # ----- paths -----
    def tool_dir(self, tool):
        return os.path.join(self.install_root, tool["dir"])

    def venv_python(self, tool):
        return os.path.join(self.tool_dir(tool), "venv", "Scripts", "python.exe")

    def is_installed(self, tool):
        return os.path.isdir(os.path.join(self.tool_dir(tool), ".git"))

    # ----- python resolution -----
    def resolve_python(self, version):
        if version in self._python_cache:
            return self._python_cache[version]
        cur = f"{sys.version_info.major}.{sys.version_info.minor}"
        found = None
        # When frozen, sys.executable is the .exe (not a python interpreter), so it
        # cannot create venvs — always resolve a real interpreter in that case.
        if (version == cur and not getattr(sys, "frozen", False)
                and "windowsapps" not in sys.executable.lower()):
            found = sys.executable
        if not found:
            rc, out = run_capture(["py", f"-{version}", "-c",
                                   "import sys;print(sys.executable)"], timeout=12)
            if rc == 0 and out and os.path.exists(out):
                found = out
        if not found:
            v = version.replace(".", "")
            cands = [
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                             "Python", f"Python{v}", "python.exe"),
                os.path.join("C:\\", f"Python{v}", "python.exe"),
                os.path.join(os.environ.get("ProgramFiles", ""),
                             f"Python{v}", "python.exe"),
            ]
            for c in cands:
                if c and os.path.exists(c):
                    found = c
                    break
        self._python_cache[version] = found
        return found

    # ----- placeholder substitution -----
    def subst(self, s, tool):
        if not isinstance(s, str):
            return s
        py = self.resolve_python(tool.get("python_version", "3.10")) or "python"
        return (s.replace("${venv_python}", self.venv_python(tool))
                 .replace("${python}", py)
                 .replace("${git}", GIT or "git")
                 .replace("${port}", str(tool["port"]))
                 .replace("${tool_dir}", self.tool_dir(tool))
                 .replace("${clone_url}", tool["clone_url"])
                 .replace("${branch}", tool["branch"]))

    def build_env(self, tool):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        # Make sure the tool's required python is first on PATH (matters for .bat
        # installers like setup.bat / webui.bat that call bare `python`).
        py = self.resolve_python(tool.get("python_version", "3.10"))
        if py:
            d = os.path.dirname(py)
            env["PATH"] = d + os.pathsep + os.path.join(d, "Scripts") + \
                os.pathsep + env.get("PATH", "")
        for k, v in (tool.get("launch_env") or {}).items():
            env[k] = self.subst(v, tool)
        return env

    # ----- logging -----
    def log(self, tid, kind, text):
        rt = self.rt[tid]
        with rt.lock:
            for line in str(text).splitlines() or [""]:
                rt.seq += 1
                rt.log.append({"seq": rt.seq, "kind": kind, "text": line})

    @staticmethod
    def _wrap(argv):
        """Run .bat/.cmd and the node package-manager shims through cmd /c."""
        if not argv:
            return argv
        head = argv[0].lower()
        if (head.endswith(".bat") or head.endswith(".cmd")
                or os.path.basename(head) in ("npm", "pnpm", "yarn", "npx")):
            return ["cmd", "/c"] + argv
        return argv

    # ----- low-level streamed step -----
    def run_step(self, tool, argv_tmpl, cwd, env):
        tid = tool["id"]
        argv = [self.subst(a, tool) for a in argv_tmpl]
        full = self._wrap(argv)
        self.log(tid, "cmd", "> " + " ".join(argv))
        try:
            proc = subprocess.Popen(
                full, cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
                encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError as e:
            self.log(tid, "err", f"command not found: {e}")
            raise RuntimeError(str(e))
        for line in proc.stdout:
            self.log(tid, "out", line.rstrip("\n"))
        proc.wait()
        rc = proc.returncode
        self.log(tid, "ok" if rc == 0 else "err", f"[exit {rc}]")
        if rc != 0:
            raise RuntimeError(f"step failed (exit {rc}): {' '.join(argv_tmpl)}")
        return rc

    def _run_steps(self, tool, steps, cwd):
        """Run install steps. A step shaped {"besteffort": [...]} won't abort the
        run if it fails (used for auto-installing a script's scanned imports)."""
        for step in steps:
            if isinstance(step, dict) and "besteffort" in step:
                try:
                    self.run_step(tool, step["besteffort"], cwd=cwd,
                                  env=self.build_env(tool))
                except Exception as e:
                    self.log(tool["id"], "sys",
                             f"(optional step skipped — continuing: {e})")
            else:
                self.run_step(tool, step, cwd=cwd, env=self.build_env(tool))

    # ----- install -----
    def install(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        with rt.lock:
            if rt.state != "idle":
                return False, f"{tool['name']} is busy ({rt.state})."
            if self.is_installed(tool):
                return False, f"{tool['name']} is already installed."
            rt.state = "installing"
            rt.error = None
        threading.Thread(target=self._install_worker, args=(tid,), daemon=True).start()
        return True, "started"

    def _install_worker(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        tdir = self.tool_dir(tool)
        try:
            if not GIT:
                raise RuntimeError("git is not installed / not on PATH.")
            os.makedirs(self.install_root, exist_ok=True)
            self.log(tid, "sys", f"=== Installing {tool['name']} into {tdir} ===")

            if os.path.exists(tdir) and not self.is_installed(tool):
                if os.listdir(tdir):
                    raise RuntimeError(
                        f"Folder already exists and is not a git repo: {tdir}. "
                        f"Remove it and retry.")

            if not self.is_installed(tool):
                clone = [GIT, "clone", "--progress"]
                if tool.get("submodules"):
                    clone += ["--recurse-submodules"]
                clone += [tool["clone_url"], tdir]
                self.run_step(tool, clone, cwd=self.install_root, env=self.build_env(tool))

            # For a user-added repo, figure out how to set it up now that we have files.
            if tool.get("custom") and not tool.get("configured"):
                self.log(tid, "sys", "Analyzing the repository to work out how to set it up…")
                self._detect_and_configure(tool)
                if tool.get("kind") == "node":
                    self._ensure_node(tid)
                if not tool.get("launch_cmd"):
                    self.log(tid, "sys", "No run command detected — after install, open "
                                         "⚙ Run config on the card to set one.")

            if tool.get("needs_venv"):
                vpy = self.venv_python(tool)
                if not os.path.exists(vpy):
                    base = self.resolve_python(tool.get("python_version", "3.10"))
                    if not base:
                        raise RuntimeError(
                            f"Python {tool.get('python_version')} not found — "
                            f"install it, then retry.")
                    self.log(tid, "sys", f"Creating virtual environment (Python "
                                         f"{tool.get('python_version')})…")
                    self.run_step(tool, [base, "-m", "venv", "venv"],
                                  cwd=tdir, env=self.build_env(tool))

            self._run_steps(tool, tool.get("install_steps", []), tdir)

            self.log(tid, "ok", f"=== {tool['name']} installed successfully ===")
            if tool.get("first_launch_installs"):
                self.log(tid, "sys", "Note: the FIRST launch will download more "
                                     "dependencies/models and may take several minutes.")
            # automatically run a safety scan on the freshly installed code
            with rt.lock:
                rt.scanning = True
            threading.Thread(target=self._scan_worker, args=(tid,), daemon=True).start()
        except Exception as e:
            self.log(tid, "err", f"Install failed: {e}")
            with rt.lock:
                rt.error = str(e)
        finally:
            with rt.lock:
                rt.state = "idle"
            self.refresh_commit(tid)

    # ----- update -----
    def update(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        with rt.lock:
            if not self.is_installed(tool):
                return False, f"{tool['name']} is not installed yet."
            if rt.state == "running":
                return False, "Stop the tool before updating."
            if rt.state != "idle":
                return False, f"{tool['name']} is busy ({rt.state})."
            rt.state = "updating"
            rt.error = None
        threading.Thread(target=self._update_worker, args=(tid,), daemon=True).start()
        return True, "started"

    def _update_worker(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        tdir = self.tool_dir(tool)
        try:
            self.log(tid, "sys", f"=== Updating {tool['name']} ===")
            env = self.build_env(tool)
            self.run_step(tool, [GIT, "fetch", "--all", "--prune"], cwd=tdir, env=env)
            self.run_step(tool, [GIT, "pull", "--autostash"], cwd=tdir, env=env)
            if tool.get("submodules"):
                self.run_step(tool, [GIT, "submodule", "update", "--init", "--recursive"],
                              cwd=tdir, env=env)
            for step in tool.get("update_steps", []):
                self.run_step(tool, step, cwd=tdir, env=env)
            self.log(tid, "ok", f"=== {tool['name']} updated ===")
        except Exception as e:
            self.log(tid, "err", f"Update failed: {e}")
            with rt.lock:
                rt.error = str(e)
        finally:
            with rt.lock:
                rt.state = "idle"
            self.refresh_commit(tid)
            self.check_updates(tid)

    # ----- launch -----
    def launch(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        with rt.lock:
            if not self.is_installed(tool):
                return False, f"{tool['name']} is not installed yet."
            if rt.state == "running":
                return False, f"{tool['name']} is already running."
            if rt.state != "idle":
                return False, f"{tool['name']} is busy ({rt.state})."
            if tool.get("port") and port_open(tool["port"]):
                return False, (f"Port {tool['port']} is already in use — another "
                               f"instance may be running.")
            # A user-added repo with no run command yet → auto-set-up on Launch
            # (re-detect + install the bits it needs), so the user never has to
            # touch ⚙ Run config for the common cases.
            needs_setup = tool.get("custom") and (
                not tool.get("launch_cmd")
                or tool.get("detect_version") != DETECT_VERSION)
            rt.state = "installing" if needs_setup else "running"
            rt.ready = False
            rt.detected_url = None
            rt.error = None
        if needs_setup:
            threading.Thread(target=self._setup_then_launch, args=(tid,),
                             daemon=True).start()
            return True, "setting up"
        return self._spawn(tid)

    def _setup_then_launch(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        try:
            self.log(tid, "sys", "Figuring out how to run this repo…")
            self._detect_and_configure(tool)
            if not tool.get("launch_cmd"):
                self.log(tid, "err", "Couldn't auto-detect a run command. Open "
                                     "⚙ Run config and paste the command from the README.")
                with rt.lock:
                    rt.state = "idle"
                self.revision += 1
                return
            if tool.get("needs_venv") and not os.path.exists(self.venv_python(tool)):
                base = self.resolve_python(tool.get("python_version", "3.10"))
                if base:
                    self.run_step(tool, [base, "-m", "venv", "venv"],
                                  cwd=self.tool_dir(tool), env=self.build_env(tool))
            self._run_steps(tool, tool.get("install_steps", []), self.tool_dir(tool))
        except Exception as e:
            self.log(tid, "err", f"Auto-setup failed: {e}")
            with rt.lock:
                rt.state = "idle"
                rt.error = str(e)
            self.revision += 1
            return
        with rt.lock:
            rt.state = "running"
            rt.ready = False
            rt.detected_url = None
        ok, _ = self._spawn(tid)
        if not ok:
            with rt.lock:
                rt.state = "idle"

    def _spawn(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        try:
            argv = [self.subst(a, tool) for a in tool.get("launch_cmd", [])]
            if not argv:
                with rt.lock:
                    rt.state = "idle"
                return False, "No run command set. Open ⚙ Run config to set one."
            full = self._wrap(argv)
            self.log(tid, "sys", f"=== Launching {tool['name']} ===")
            if tool.get("first_launch_installs"):
                self.log(tid, "sys", "First launch may take several minutes "
                                     "(installing deps / downloading models)…")
            self.log(tid, "cmd", "> " + " ".join(argv))
            proc = subprocess.Popen(
                full, cwd=self.tool_dir(tool), env=self.build_env(tool),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
                encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
        except Exception as e:
            self.log(tid, "err", f"Launch failed: {e}")
            with rt.lock:
                rt.state = "idle"
                rt.error = str(e)
            return False, str(e)
        with rt.lock:
            rt.proc = proc
            rt.pid = proc.pid
        threading.Thread(target=self._pump_output, args=(tid, proc), daemon=True).start()
        threading.Thread(target=self._watch_ready, args=(tid, tool), daemon=True).start()
        return True, "started"

    def _pump_output(self, tid, proc):
        rt = self.rt[tid]
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            self.log(tid, "out", line)
            if not rt.detected_url:
                m = URL_RE.search(line)
                if m and m.group(1):  # require an explicit port to avoid noise
                    url = m.group(0).rstrip("/.,)\"'").replace("0.0.0.0", "127.0.0.1")
                    with rt.lock:
                        rt.detected_url = url
                        rt.ready = True
                    self.log(tid, "ok", f"=== Web UI detected at {url} ===")
        proc.wait()
        rt = self.rt[tid]
        with rt.lock:
            if rt.proc is proc:
                self.log(tid, "sys", f"=== Process exited (code {proc.returncode}) ===")
                rt.state = "idle"
                rt.proc = None
                rt.pid = None
                rt.ready = False

    def _watch_ready(self, tid, tool):
        rt = self.rt[tid]
        port = tool.get("port") or 0
        for _ in range(2400):  # up to ~20 min for big first-time setups
            with rt.lock:
                if rt.state != "running":
                    return
                if rt.detected_url:   # found in the app's output
                    return
            if port and port_open(port):
                with rt.lock:
                    if not rt.ready:
                        rt.ready = True
                        if not rt.detected_url:
                            rt.detected_url = tool.get("url") or f"http://127.0.0.1:{port}"
                        self.log(tid, "ok", f"=== Ready at {rt.detected_url} ===")
                return
            time.sleep(0.5)

    # ----- stop -----
    def stop(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        with rt.lock:
            if rt.state != "running" or not rt.pid:
                return False, f"{tool['name']} is not running."
            pid = rt.pid
            rt.state = "stopping"
        self.log(tid, "sys", f"Stopping {tool['name']} (pid {pid})…")
        run_capture(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=20)
        # give the pump thread a moment to notice the exit
        for _ in range(20):
            with rt.lock:
                if rt.proc is None:
                    break
            time.sleep(0.1)
        with rt.lock:
            rt.state = "idle"
            rt.proc = None
            rt.pid = None
            rt.ready = False
        self.log(tid, "ok", f"{tool['name']} stopped.")
        return True, "stopped"

    # ----- git info -----
    def refresh_commit(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        if not self.is_installed(tool):
            with rt.lock:
                rt.commit = {"short": None, "subject": None, "branch": None}
            return
        tdir = self.tool_dir(tool)
        _, short = run_capture([GIT, "rev-parse", "--short", "HEAD"], cwd=tdir)
        _, subj = run_capture([GIT, "log", "-1", "--format=%s"], cwd=tdir)
        _, br = run_capture([GIT, "rev-parse", "--abbrev-ref", "HEAD"], cwd=tdir)
        with rt.lock:
            rt.commit = {"short": short, "subject": subj, "branch": br}

    def check_updates(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        if not self.is_installed(tool) or not GIT:
            return
        tdir = self.tool_dir(tool)
        fc, fout = run_capture([GIT, "fetch", "--quiet"], cwd=tdir, timeout=60)
        if fc != 0:
            with rt.lock:
                rt.update = {**rt.update, "checked": time.time(),
                             "error": "couldn't reach remote"}
            return
        bc, behind = run_capture([GIT, "rev-list", "--count", "HEAD..@{u}"], cwd=tdir)
        ac, ahead = run_capture([GIT, "rev-list", "--count", "@{u}..HEAD"], cwd=tdir)
        _, subj = run_capture([GIT, "log", "-1", "--format=%s", "@{u}"], cwd=tdir)
        if bc != 0:
            # no upstream tracking — fall back to origin/<branch>
            ref = f"origin/{tool['branch']}"
            bc, behind = run_capture([GIT, "rev-list", "--count", f"HEAD..{ref}"], cwd=tdir)
            ac, ahead = run_capture([GIT, "rev-list", "--count", f"{ref}..HEAD"], cwd=tdir)
            _, subj = run_capture([GIT, "log", "-1", "--format=%s", ref], cwd=tdir)
        with rt.lock:
            rt.update = {
                "checked": time.time(),
                "behind": int(behind) if behind.isdigit() else None,
                "ahead": int(ahead) if ahead.isdigit() else None,
                "subject": subj or None,
                "error": None,
            }
        self.refresh_commit(tid)

    def check_all(self):
        threading.Thread(target=self.refresh_descriptions, daemon=True).start()
        for t in self.tools:
            if self.is_installed(t):
                threading.Thread(target=self.check_updates, args=(t["id"],),
                                 daemon=True).start()

    # ----- keep each card's intro in sync with the repo's GitHub "About" -----
    @staticmethod
    def _repo_slug(tool):
        url = (tool.get("repo") or tool.get("clone_url") or "").strip()
        for p in ("https://github.com/", "http://github.com/", "github.com/"):
            if url.startswith(p):
                url = url[len(p):]
        if url.endswith(".git"):
            url = url[:-4]
        parts = url.strip("/").split("/")
        return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else None

    def fetch_repo_description(self, tool):
        slug = self._repo_slug(tool)
        if not slug:
            return None
        req = urllib.request.Request(
            f"https://api.github.com/repos/{slug}",
            headers={"User-Agent": "MaxStudioHub",
                     "Accept": "application/vnd.github+json"})
        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                data = json.load(r)
            desc = (data.get("description") or "").strip()
            return desc[:240] if desc else None
        except Exception:
            return None

    def refresh_descriptions(self):
        """Fetch each repo's GitHub About blurb; update the cache + bump revision
        when one changes so the UI follows up. Never clobbers a known description
        with None (offline / no-About repos keep the curated fallback)."""
        self._last_desc_fetch = time.time()
        changed = False
        for t in list(self.tools):
            desc = self.fetch_repo_description(t)
            if desc and desc != self.repo_desc.get(t["id"]):
                self.repo_desc[t["id"]] = desc
                changed = True
        if changed:
            try:
                save_json(DESC_CACHE_PATH, self.repo_desc)
            except Exception:
                pass
            self.revision += 1

    # ----- security scan -----
    def scan(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        if not self.is_installed(tool):
            return False, f"{tool['name']} is not installed yet."
        with rt.lock:
            if rt.scanning:
                return False, "A scan is already running."
            rt.scanning = True
        threading.Thread(target=self._scan_worker, args=(tid,), daemon=True).start()
        return True, "started"

    def _scan_worker(self, tid):
        tool = self.by_id[tid]
        rt = self.rt[tid]
        tdir = self.tool_dir(tool)
        findings = []
        high = medium = pickles = execs = scanned = 0
        err = None
        self.log(tid, "sys", f"=== Security scan: {tool['name']} ===")
        self.log(tid, "sys", "Read-only heuristic scan (no code is executed). "
                             "Skipping venv & model folders.")
        try:
            for root, dirs, files in os.walk(tdir):
                dirs[:] = [d for d in dirs if d.lower() not in SCAN_SKIP_DIRS
                           and not d.lower().endswith(".dist-info")]
                for fn in files:
                    if scanned >= SCAN_MAX_FILES:
                        raise StopIteration
                    ext = os.path.splitext(fn)[1].lower()
                    fpath = os.path.join(root, fn)
                    rel = os.path.relpath(fpath, tdir)
                    if ext in SCAN_EXEC_EXT:
                        execs += 1
                        high += 1
                        findings.append({"severity": "high", "file": rel, "line": 0,
                                         "reason": f"Executable/binary file ({ext}) shipped in the repo.",
                                         "snippet": fn})
                        self.log(tid, "err", f"  [HIGH] {rel} — executable file ({ext})")
                        continue
                    if ext in SCAN_PICKLE_EXT:
                        pickles += 1
                        findings.append({"severity": "medium", "file": rel, "line": 0,
                                         "reason": "Pickle-format model/data — can run code when loaded; "
                                                   "prefer .safetensors.",
                                         "snippet": fn})
                        continue
                    if ext not in SCAN_TEXT_EXT:
                        continue
                    try:
                        if os.path.getsize(fpath) > SCAN_MAX_FILE:
                            continue
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                    except OSError:
                        continue
                    scanned += 1
                    for fr in _scan_text(rel, text):
                        findings.append(fr)
                        if fr["severity"] == "high":
                            high += 1
                            self.log(tid, "err",
                                     f"  [HIGH] {fr['file']}:{fr['line']} — {fr['reason']}")
                        else:
                            medium += 1
        except StopIteration:
            self.log(tid, "sys", f"(scan capped at {SCAN_MAX_FILES} files)")
        except Exception as e:
            err = str(e)
            self.log(tid, "err", f"scan error: {e}")

        verdict = "error" if err else ("danger" if high > 0 else "ok")
        result = {"done": True, "verdict": verdict, "high": high, "medium": medium,
                  "pickles": pickles, "execs": execs, "scanned": scanned,
                  "when": time.time(), "findings": findings[:300], "error": err}
        with rt.lock:
            rt.scan = result
            rt.scanning = False
        if high > 0:
            self.log(tid, "err",
                     f"=== SCAN RESULT: {high} HIGH-RISK signal(s) found — review before launching. "
                     f"({medium} to review, {pickles} pickle file(s)) ===")
        else:
            self.log(tid, "ok",
                     f"=== SCAN RESULT: no high-risk signals. {medium} code item(s) + {pickles} "
                     f"pickle file(s) flagged for review (normal for these tools). ===")
        self.revision += 1

    # ----- disk watcher: keep the app in sync with the folders on disk -----
    @staticmethod
    def _mtime(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def _repo_signature(self, tool):
        """A cheap fingerprint of a tool's install state + git position. Changes
        when the folder appears/disappears, a commit/pull/checkout happens, or the
        working tree is staged — but NOT when models/outputs are written."""
        tdir = self.tool_dir(tool)
        if not os.path.isdir(tdir):
            return ("absent",)
        gitdir = os.path.join(tdir, ".git")
        if not os.path.isdir(gitdir):
            return ("nogit",)
        parts = ["repo"]
        for rel in ("HEAD", "packed-refs", "index"):
            parts.append(str(self._mtime(os.path.join(gitdir, rel))))
        try:
            with open(os.path.join(gitdir, "HEAD"), "r",
                      encoding="utf-8", errors="replace") as f:
                head = f.read().strip()
            parts.append(head)
            if head.startswith("ref:"):
                parts.append(str(self._mtime(
                    os.path.join(gitdir, head[4:].strip()))))
        except OSError:
            pass
        return tuple(parts)

    def _reload_config(self):
        cfg = load_json(CONFIG_PATH, {}) or {}
        root = cfg.get("install_root")
        if root and os.path.abspath(root) != os.path.abspath(self.install_root):
            self.install_root = os.path.abspath(root)
            self.revision += 1
            return True
        return False

    def _reload_registry(self):
        reg = load_json(REGISTRY_PATH, None)
        if not reg or not reg.get("tools"):
            return False
        self.registry = reg
        self._rebuild_tools()   # keeps user-added custom tools merged in
        self._python_cache = {}
        self.revision += 1
        return True

    def start_watcher(self):
        if self._watch_started:
            return
        self._watch_started = True
        threading.Thread(target=self._watch_loop, daemon=True).start()
        # fetch repo intros shortly after startup
        threading.Thread(target=self.refresh_descriptions, daemon=True).start()

    def _watch_loop(self):
        # prime caches first so we don't fire on the initial state
        self._sig_cache = {t["id"]: self._repo_signature(t) for t in self.tools}
        while not self._stop_watch:
            time.sleep(2.0)
            try:
                # re-check the repos' GitHub intros every ~6 hours
                if time.time() - self._last_desc_fetch > 21600:
                    self._last_desc_fetch = time.time()
                    threading.Thread(target=self.refresh_descriptions,
                                     daemon=True).start()
                m = self._mtime(CONFIG_PATH)
                if m != self._cfg_mtime:
                    self._cfg_mtime = m
                    self._reload_config()
                m = self._mtime(REGISTRY_PATH)
                if m != self._reg_mtime:
                    self._reg_mtime = m
                    self._reload_registry()
                    for t in self.tools:
                        self._sig_cache.setdefault(t["id"], self._repo_signature(t))
                m = self._mtime(CUSTOM_PATH)
                if m != self._custom_mtime:
                    self._custom_mtime = m
                    self._rebuild_tools()
                    for t in self.tools:
                        self._sig_cache.setdefault(t["id"], self._repo_signature(t))
                    self.revision += 1
                for t in list(self.tools):
                    tid = t["id"]
                    rt = self.rt.get(tid)
                    if not rt:
                        continue
                    # install/update churn the repo and refresh commit themselves
                    if rt.state in ("installing", "updating"):
                        self._sig_cache[tid] = self._repo_signature(t)
                        continue
                    sig = self._repo_signature(t)
                    if self._sig_cache.get(tid) != sig:
                        self._sig_cache[tid] = sig
                        self.refresh_commit(tid)
                        self.revision += 1
            except Exception:
                pass

    # ----- snapshots for the UI -----
    def prereqs(self):
        py310 = self.resolve_python("3.10")
        py_ver = None
        if py310:
            _, py_ver = run_capture([py310, "--version"])
        git_ver = None
        if GIT:
            _, git_ver = run_capture([GIT, "--version"])
        return {
            "git": {"ok": bool(GIT), "path": GIT, "version": git_ver},
            "python": {"ok": bool(py310), "path": py310, "version": py_ver},
            "install_root": self.install_root,
            "onedrive_warning": is_under_onedrive(self.install_root),
        }

    def tool_status(self, tool):
        rt = self.rt[tool["id"]]
        gh = self.repo_desc.get(tool["id"])
        with rt.lock:
            return {
                "id": tool["id"], "name": tool["name"], "emoji": tool["emoji"],
                "color": tool["color"],
                "description": gh or tool["description"],
                "description_from_github": bool(gh),
                "custom": tool.get("custom", False),
                "kind": tool.get("kind", ""),
                "configured": tool.get("configured", True),
                "run_cmd": " ".join(tool.get("launch_cmd", [])),
                "repo": tool["repo"],
                "url": rt.detected_url or tool.get("url", ""),
                "port": tool.get("port", 0),
                "notes": tool.get("notes", ""),
                "auto_update_on_launch": tool.get("auto_update_on_launch", False),
                "first_launch_installs": tool.get("first_launch_installs", False),
                "installed": self.is_installed(tool),
                "state": rt.state, "ready": rt.ready, "pid": rt.pid,
                "error": rt.error, "update": dict(rt.update), "commit": dict(rt.commit),
                "scanning": rt.scanning,
                "scan": {k: rt.scan[k] for k in
                         ("done", "verdict", "high", "medium", "pickles",
                          "execs", "scanned", "when")},
            }

    def status_all(self):
        return [self.tool_status(t) for t in self.tools]

    def bootstrap(self):
        # populate commit info lazily on first call
        for t in self.tools:
            if self.is_installed(t) and self.rt[t["id"]].commit["short"] is None:
                self.refresh_commit(t["id"])
        return {
            "prereqs": self.prereqs(),
            "tools": self.status_all(),
            "revision": self.revision,
            "launcher_url": f"http://{HOST}:{PORT}",
        }


# --------------------------------------------------------------------------- #
#  HTTP layer
# --------------------------------------------------------------------------- #
HOST = "127.0.0.1"
M = Manager()
PORT = int(os.environ.get("AISH_PORT") or M.registry.get("launcher_port", 8765))

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, fname, ctype):
        path = os.path.join(WEB_DIR, fname)
        try:
            with open(path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)
        if path in STATIC:
            self._send_static(*STATIC[path])
            return
        if path == "/api/bootstrap":
            self._send_json(M.bootstrap())
            return
        if path == "/api/status":
            self._send_json({"tools": M.status_all(), "revision": M.revision})
            return
        if path == "/api/version":
            self._send_json({"version": assets_version()})
            return
        if path == "/api/scan_report":
            tid = (q.get("tool") or [None])[0]
            if tid not in M.rt:
                self._send_json({"error": "unknown tool"}, 404)
                return
            with M.rt[tid].lock:
                self._send_json({"tool": tid, "scanning": M.rt[tid].scanning,
                                 "scan": M.rt[tid].scan})
            return
        if path == "/api/log":
            tid = (q.get("tool") or [None])[0]
            since = int((q.get("since") or ["0"])[0])
            if tid not in M.rt:
                self._send_json({"error": "unknown tool"}, 404)
                return
            rt = M.rt[tid]
            with rt.lock:
                lines = [x for x in rt.log if x["seq"] > since]
                self._send_json({
                    "lines": lines, "seq": rt.seq, "state": rt.state,
                    "ready": rt.ready,
                })
            return
        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        tid = body.get("tool")

        def need_tool():
            if tid not in M.by_id:
                self._send_json({"ok": False, "error": "unknown tool"}, 400)
                return False
            return True

        if path == "/api/install":
            if not need_tool():
                return
            ok, msg = M.install(tid)
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/update":
            if not need_tool():
                return
            ok, msg = M.update(tid)
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/launch":
            if not need_tool():
                return
            ok, msg = M.launch(tid)
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/stop":
            if not need_tool():
                return
            ok, msg = M.stop(tid)
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/scan":
            if not need_tool():
                return
            ok, msg = M.scan(tid)
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/add_tool":
            ok, msg, tool = M.add_tool(body.get("repo_url"), body.get("name"))
            self._send_json({"ok": ok, "message": msg,
                             "tool": tool["id"] if tool else None})
        elif path == "/api/remove_tool":
            if not need_tool():
                return
            ok, msg = M.remove_tool(tid, body.get("delete_files", True))
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/configure":
            if not need_tool():
                return
            ok, msg = M.configure(tid, run_cmd=body.get("run_cmd"),
                                  port=body.get("port"), pip=body.get("pip"),
                                  python_version=body.get("python_version"))
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/check":
            if body.get("all"):
                M.check_all()
                self._send_json({"ok": True, "message": "checking all"})
            else:
                if not need_tool():
                    return
                threading.Thread(target=M.check_updates, args=(tid,), daemon=True).start()
                self._send_json({"ok": True, "message": "checking"})
        elif path == "/api/config":
            root = (body.get("install_root") or "").strip()
            if not root:
                self._send_json({"ok": False, "error": "empty path"}, 400)
                return
            if not M.set_install_root(root):
                self._send_json({"ok": False, "error": (
                    "That folder isn't allowed (don't use a drive root like C:\\ or "
                    "a system folder). Pick something like C:\\AItools.")}, 400)
                return
            self._send_json({"ok": True, "prereqs": M.prereqs(),
                             "tools": M.status_all()})
        elif path == "/api/reveal":
            if not need_tool():
                return
            d = M.tool_dir(M.by_id[tid])
            try:
                if os.path.isdir(d):
                    os.startfile(d)  # noqa
                else:
                    os.startfile(M.install_root)  # noqa
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
        else:
            self.send_error(404)


def shutdown_all():
    for t in M.tools:
        rt = M.rt[t["id"]]
        if rt.pid:
            run_capture(["taskkill", "/PID", str(rt.pid), "/T", "/F"], timeout=10)


def make_server(port=None):
    """Create (but don't start) the HTTP server. Used by app.py to run it in a
    background thread behind the native window."""
    M.start_watcher()  # keep the UI in sync with on-disk changes
    return ThreadingHTTPServer((HOST, PORT if port is None else port), Handler)


def main():
    if not os.path.isdir(WEB_DIR):
        print(f"[fatal] web/ folder not found next to server.py ({WEB_DIR})")
        sys.exit(1)
    server = make_server(PORT)
    url = f"http://{HOST}:{PORT}"
    print("=" * 60)
    print("  Max Studio Hub is running")
    print(f"  Open:  {url}")
    print(f"  Install root: {M.install_root}")
    print(f"  git: {'OK' if GIT else 'MISSING'}   "
          f"python3.10: {'OK' if M.resolve_python('3.10') else 'MISSING'}")
    print("  (Close this window to stop the launcher. Running tools keep going.)")
    print("=" * 60)
    if os.environ.get("AISH_NO_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down launcher (leaving tools running)…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
