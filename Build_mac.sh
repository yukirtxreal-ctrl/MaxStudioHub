#!/bin/bash
# Max Studio Hub — build "Max Studio Hub.app" from source on macOS and install
# it into /Applications (the Mac equivalent of Build.ps1). Run:
#   bash Build_mac.sh
#
# Note: normal releases are built automatically by GitHub Actions
# (.github/workflows/release.yml) — this script is for local development.
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
[ -n "$PY" ] || { echo "Python 3 is required (brew install python)."; exit 1; }

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating build venv…"
  "$PY" -m venv .venv
fi
echo "Installing build tools (pywebview, pyinstaller, pillow)…"
./.venv/bin/python -m pip install --upgrade pip --quiet
./.venv/bin/python -m pip install pywebview pyinstaller pillow --quiet

if [ ! -f assets/app.icns ]; then
  echo "Generating icons…"
  ./.venv/bin/python assets/make_icon.py
fi

echo "Building Max Studio Hub.app…"
./.venv/bin/python -m PyInstaller --noconfirm --clean --windowed \
  --name "Max Studio Hub" \
  --icon assets/app.icns \
  --osx-bundle-identifier com.maxstudiohub.launcher \
  --add-data "web:web" \
  --add-data "tools.json:." \
  --distpath dist --workpath build --specpath . \
  app.py

# Install to /Applications and point the app at this source folder so it
# mirrors web/ + tools.json LIVE (same behavior as Build.ps1 on Windows).
SUPPORT="$HOME/Library/Application Support/MaxStudioHub"
mkdir -p "$SUPPORT"
printf '%s' "$(pwd)" > "$SUPPORT/live_source.txt"
rm -rf "/Applications/Max Studio Hub.app"
cp -R "dist/Max Studio Hub.app" /Applications/
echo "Done — 'Max Studio Hub' is installed in your Applications folder."
