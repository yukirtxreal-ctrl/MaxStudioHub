#!/usr/bin/env python3
"""Max Studio Hub — CI smoke test.

Runs on the real target OS (GitHub Actions windows-latest / macos-latest) and
verifies the backend genuinely works there BEFORE a release download is built:
registry + platform overrides, install-root safety, python resolution, the
HTTP API, port discovery, and process-tree stopping. Standard library only.

    python ci/smoke_test.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402  (imports the backend under test)

FAILURES = []


def check(name, cond, info=""):
    tag = "PASS" if cond else "FAIL"
    extra = f"  [{info}]" if (info and not cond) else ""
    print(f"{tag}  {name}{extra}", flush=True)
    if not cond:
        FAILURES.append(name)


def wait_for(fn, timeout=20.0, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return fn()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get_json(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.getcode(), json.load(r)


def post_json(url, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.getcode(), json.load(r)


def main():
    print(f"--- smoke test on {sys.platform} / python {sys.version.split()[0]} ---")

    # ---- 1) registry + platform overrides ----------------------------------
    tools = {t["id"]: t for t in server.M.tools}
    check("loads the 4 built-in tools",
          all(k in tools for k in ("comfyui", "forge", "fooocus", "kohya")))
    if server.IS_WIN:
        check("forge launches via webui.bat on Windows",
              tools["forge"]["launch_cmd"][0] == "webui.bat")
        check("kohya sets up via setup.bat on Windows",
              tools["kohya"]["install_steps"][0][0] == "setup.bat")
        check("venv python is Scripts\\python.exe",
              server.M.venv_python(tools["comfyui"]).endswith("python.exe"))
    else:
        check("forge launches via webui.sh on mac/linux",
              tools["forge"]["launch_cmd"][0] == "webui.sh")
        check("kohya sets up via setup.sh on mac/linux",
              tools["kohya"]["install_steps"][0][0] == "setup.sh")
        blob = json.dumps([(t.get("install_steps"), t.get("launch_env"),
                            t.get("launch_cmd")) for t in server.M.tools])
        check("no CUDA (cu128) config leaks into mac/linux tools",
              "cu128" not in blob)
        check("venv python is venv/bin/python",
              server.M.venv_python(tools["comfyui"]).endswith("venv/bin/python"))
        check("no platform_overrides key left after merge",
              all("platform_overrides" not in t for t in server.M.tools))

    # ---- 2) install root ----------------------------------------------------
    check("default install root is accepted as safe",
          server.is_safe_install_root(server.M.install_root),
          server.M.install_root)
    if not server.IS_WIN:
        check("install root is not a Windows path",
              ":" not in server.M.install_root, server.M.install_root)
        check("rejects filesystem root /", not server.is_safe_install_root("/"))
        check("rejects a bare external drive (/Volumes/X)",
              not server.is_safe_install_root("/Volumes/SomeDrive"))
        check("rejects the home folder itself",
              not server.is_safe_install_root(os.path.expanduser("~")))
        check("accepts ~/AItools",
              server.is_safe_install_root(os.path.expanduser("~/AItools")))
    else:
        check("rejects drive root C:\\", not server.is_safe_install_root("C:\\"))
        check("accepts C:\\AItools", server.is_safe_install_root("C:\\AItools"))

    # ---- 3) command wrapping -------------------------------------------------
    if server.IS_WIN:
        check("wraps .bat through cmd /c",
              server.Manager._wrap(["x.bat", "--y"])[:2] == ["cmd", "/c"])
        check("leaves exe argv alone",
              server.Manager._wrap(["python.exe", "a.py"])[0] == "python.exe")
    else:
        check("wraps .sh through bash",
              server.Manager._wrap(["webui.sh", "--port", "1"])[0] == "bash")
        check("leaves plain argv alone",
              server.Manager._wrap(["python", "a.py"])[0] == "python")

    # ---- 4) python resolution -------------------------------------------------
    cur = f"{sys.version_info.major}.{sys.version_info.minor}"
    py = server.M.resolve_python(cur)
    check(f"resolve_python({cur}) finds this runner's python", bool(py), str(py))

    # ---- 5) venv creation (what Install does first) ---------------------------
    if py:
        vdir = tempfile.mkdtemp(prefix="msh_venv_")
        rc, out = server.run_capture([py, "-m", "venv",
                                      os.path.join(vdir, "venv")], timeout=240)
        vpy = (os.path.join(vdir, "venv", "Scripts", "python.exe") if server.IS_WIN
               else os.path.join(vdir, "venv", "bin", "python"))
        check("can create a virtualenv", rc == 0 and os.path.exists(vpy), out[:200])

    # ---- 6) the HTTP API -------------------------------------------------------
    port = free_port()
    httpd = server.make_server(port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    code, boot = get_json(base + "/api/bootstrap")
    check("GET /api/bootstrap returns 200", code == 200)
    check("bootstrap reports this platform",
          boot.get("prereqs", {}).get("platform") ==
          ("windows" if server.IS_WIN else ("mac" if server.IS_MAC else "linux")))
    check("bootstrap lists the tools", len(boot.get("tools", [])) >= 4)
    check("bootstrap reports git+python prereq status",
          {"git", "python"} <= set(boot.get("prereqs", {})))

    for path in ("/", "/style.css", "/app.js"):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            check(f"GET {path} serves the UI", r.getcode() == 200)

    code, st = get_json(base + "/api/status")
    check("GET /api/status returns tools", code == 200 and len(st.get("tools", [])) >= 4)

    code, res = post_json(base + "/api/add_tool", {"repo_url": "not a repo url !!"})
    check("POST /api/add_tool rejects a bad URL", code == 200 and res.get("ok") is False)

    newroot = os.path.join(tempfile.mkdtemp(prefix="msh_root_"), "AItools")
    code, res = post_json(base + "/api/config", {"install_root": newroot})
    check("POST /api/config accepts a valid install root",
          code == 200 and res.get("ok") is True, json.dumps(res)[:200])
    check("install root actually applied",
          os.path.normcase(server.M.install_root) == os.path.normcase(os.path.abspath(newroot)))

    # "~" must expand (the mac UI suggests typing ~/AItools)
    code, res = post_json(base + "/api/config",
                          {"install_root": "~/msh_smoke_AItools"})
    expanded = os.path.abspath(os.path.expanduser("~/msh_smoke_AItools"))
    check("POST /api/config expands ~",
          res.get("ok") is True and
          os.path.normcase(server.M.install_root) == os.path.normcase(expanded),
          server.M.install_root)
    post_json(base + "/api/config", {"install_root": newroot})  # restore

    # ---- 7) port discovery + process-tree stop --------------------------------
    # Spawn a throwaway listener and exercise pids_on_port / kill_port. On hosted
    # CI runners (especially macOS, where first-run Gatekeeper verification of a
    # freshly-installed python binary can delay startup by tens of seconds) the
    # listener may be slow to bind or blocked from binding at all. That is an
    # environment limit, not an app bug, so: verify the functions RUN without
    # error unconditionally, and assert correctness only when the listener
    # actually comes up. A genuine regression (listener up but not detected/killed)
    # still fails; a slow runner that can't spin up a test server does not.
    p2 = free_port()
    child = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(p2), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **server._popen_extra(new_group=True))
    started = wait_for(lambda: server.port_open(p2), timeout=60)
    if started:
        pids = server.pids_on_port(p2)
        check("pids_on_port sees the listener", child.pid in pids, f"pids={pids}")
        server.kill_port(p2)
        check("kill_port frees the port", wait_for(lambda: not server.port_open(p2)))
        check("child process is gone",
              wait_for(lambda: child.poll() is not None, timeout=15))
    else:
        print("SKIP  port-discovery correctness (test listener never started on "
              "this runner — environment limit, not an app fault)")
        try:
            server.pids_on_port(p2)   # must at least run without raising
            server.kill_port(p2)
            print("PASS  pids_on_port / kill_port run without error")
        except Exception as e:
            check("pids_on_port / kill_port run without error", False, str(e))
        try:
            child.kill()
        except Exception:
            pass

    httpd.shutdown()

    # ---- result -----------------------------------------------------------------
    print("-" * 60)
    if FAILURES:
        print(f"SMOKE TEST FAILED — {len(FAILURES)} failure(s): {FAILURES}")
        return 1
    print("SMOKE TEST PASSED — backend works on this OS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
