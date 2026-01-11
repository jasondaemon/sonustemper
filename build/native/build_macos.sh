#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENDOR_DIR="$ROOT/vendor/ffmpeg/macos"
HTMX_FILE="$ROOT/sonustemper-ui/app/static/vendor/htmx.min.js"

if [[ ! -f "$VENDOR_DIR/ffmpeg" || ! -f "$VENDOR_DIR/ffprobe" ]]; then
  echo "Missing ffmpeg/ffprobe in $VENDOR_DIR. Place binaries there before building."
  exit 1
fi
if [[ ! -f "$HTMX_FILE" ]]; then
  echo "Missing HTMX at $HTMX_FILE. Add the official htmx.min.js before building."
  exit 1
fi
if grep -q "HTMX_PLACEHOLDER" "$HTMX_FILE" || [[ $(wc -c < "$HTMX_FILE") -lt 10000 ]]; then
  echo "HTMX file appears to be a placeholder. Replace it with the official minified build before packaging."
  exit 1
fi

python3 -m venv "$ROOT/.venv-native"
source "$ROOT/.venv-native/bin/activate"
pip install --upgrade pip
pip install -r "$ROOT/requirements.txt"
pip install pyinstaller

pyinstaller "$ROOT/build/native/sonustemper.spec"
