#!/usr/bin/env python3
"""
Max Studio Hub - native desktop application.

Runs the control-panel server in a background thread and shows it inside a real
Windows app window (Edge WebView2) - no browser, no tabs, no address bar.
"""

import os
import socket
import sys
import threading

import webview

import server


def find_free_port(preferred):
    for p in (preferred, 8766, 8767, 8768, 8780, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            port = s.getsockname()[1]
            s.close()
            return port
        except OSError:
            s.close()
    return preferred


def main():
    if not os.path.isdir(server.WEB_DIR):
        webview.create_window("Max Studio Hub - error",
                              html="<h2>web/ assets not found</h2>")
        webview.start()
        return

    port = find_free_port(server.PORT)
    server.PORT = port  # so the UI's footer/launcher_url reflect the real bound port
    httpd = server.make_server(port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}"

    window = webview.create_window(
        "Max Studio Hub",
        url,
        width=1280,
        height=860,
        min_size=(940, 660),
        background_color="#0b0e14",
        text_select=True,
    )

    def on_closing():
        # The launcher window is closing. Leave any running tools alive (they run
        # in their own process trees); just stop the local control server.
        try:
            httpd.shutdown()
        except Exception:
            pass

    window.events.closing += on_closing

    # Set a stable AppUserModelID so the taskbar groups/labels us as our own app.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "MaxStudioHub.Launcher")
    except Exception:
        pass

    webview.start()


if __name__ == "__main__":
    main()
