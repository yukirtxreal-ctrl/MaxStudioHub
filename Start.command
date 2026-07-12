#!/bin/bash
# Max Studio Hub — run the NATIVE app window directly from source on macOS.
# Double-click this file in Finder (first time: right-click → Open), or run
#   bash Start.command
# Most users should instead download the ready-made app from the Releases page.
set -euo pipefail
cd "$(dirname "$0")"

find_py() {
  local c
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/*/bin/python3 python3; do
    if command -v "$c" >/dev/null 2>&1; then command -v "$c"; return 0; fi
  done
  return 1
}

PY="$(find_py || true)"
if [ -z "${PY}" ]; then
  echo "Python 3 is required. Install it with:  brew install python"
  echo "…or download it from https://www.python.org/downloads/"
  read -r -p "Press Enter to close"
  exit 1
fi
# Apple ships /usr/bin/python3 only as a stub until the Command Line Tools are
# installed — running it would pop an installer dialog. Say so clearly instead.
if [ "$PY" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
  echo "Apple's command line tools aren't installed yet."
  echo "Open Terminal and run:  xcode-select --install   — then try again."
  read -r -p "Press Enter to close"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Setting up launcher environment (one-time)…"
  "$PY" -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip --quiet
  ./.venv/bin/python -m pip install pywebview --quiet
fi

echo "Starting Max Studio Hub…"
exec ./.venv/bin/python app.py
