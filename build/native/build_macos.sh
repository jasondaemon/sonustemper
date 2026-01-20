#!/usr/bin/env bash
set -euo pipefail

# Resolve repo root from this script's location: build/native/build_macos.sh -> repo root
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Build staging under /tmp to avoid iCloud/Finder metadata/xattrs impacting the .app bundle
STAGE_BASE="/tmp/sonustemper-build"
STAGE="$(mktemp -d "${STAGE_BASE}.XXXXXX")"

cleanup() {
  # Best-effort cleanup
  rm -rf "$STAGE" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[build] Repo root: $ROOT"
echo "[build] Stage dir: $STAGE"

# Copy repo into staging, excluding things we don't want in the build context
rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv*' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'build/native/build' \
  --exclude 'build/native/dist' \
  "$ROOT/" "$STAGE/"

STAGE_ROOT="$STAGE"
STAGE_NATIVE="$STAGE_ROOT/build/native"

# Re-point checks to staged paths
VENDOR_DIR="$STAGE_ROOT/vendor/ffmpeg/macos"
HTMX_FILE="$STAGE_ROOT/sonustemper-ui/app/static/vendor/htmx.min.js"

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

# Create an isolated venv inside the staged copy
python3 -m venv "$STAGE_ROOT/.venv-native"
# shellcheck disable=SC1091
source "$STAGE_ROOT/.venv-native/bin/activate"

pip install --upgrade pip
pip install -r "$STAGE_ROOT/requirements.txt"
pip install -r "$STAGE_ROOT/requirements-native-macos.txt"
pip install pyinstaller

python -c "import AppKit, Foundation, objc; print('PyObjC OK')"

# Build
cd "$STAGE_NATIVE"
rm -rf build dist

pyinstaller "$STAGE_NATIVE/sonustemper.spec"

# Post-build: clear xattrs on the staged app (harmless if none exist)
APP_BUILT="$STAGE_NATIVE/dist/SonusTemper.app"
if [[ ! -d "$APP_BUILT" ]]; then
  echo "[build] ERROR: Expected app not found at: $APP_BUILT"
  exit 1
fi

xattr -cr "$APP_BUILT" || true

# Copy back into the repo dist (overwrite)
DEST_DIST="$ROOT/build/native/dist"
mkdir -p "$DEST_DIST"
rm -rf "$DEST_DIST/SonusTemper.app"
rsync -a "$APP_BUILT" "$DEST_DIST/"

echo "[build] Done. App copied to: $DEST_DIST/SonusTemper.app"
