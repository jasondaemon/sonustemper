#!/usr/bin/env bash
set -euo pipefail

# Resolve repo root from this script's location: build/native/build_macos.sh -> repo root
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Build staging under /tmp to avoid iCloud/Finder metadata/xattrs impacting the .app bundle
STAGE_BASE="/tmp/sonustemper-build"
STAGE="$(mktemp -d "${STAGE_BASE}.XXXXXX")"
KEEP_STAGE="${KEEP_STAGE:-0}"

cleanup() {
  if [[ "$KEEP_STAGE" != "1" ]]; then
    rm -rf "$STAGE" >/dev/null 2>&1 || true
  else
    echo "[build] KEEP_STAGE=1; preserving $STAGE"
  fi
}
trap cleanup EXIT

on_error() {
  echo "[build] ERROR: build failed"
  echo "[build] stage: $STAGE"
  find "$STAGE/sonustemper/vendor/ffmpeg" -maxdepth 4 -type f -print 2>/dev/null || true
  for f in $(find "$STAGE/sonustemper/vendor/ffmpeg" -maxdepth 4 -type f 2>/dev/null); do
    case "$(basename "$f")" in
      ffmpeg|ffprobe)
        echo "[build] file: $f"
        ls -l "$f" || true
        file "$f" || true
        head -c 4 "$f" | xxd || true
        ;;
    esac
  done
}
trap on_error ERR

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
HTMX_FILE="$STAGE_ROOT/sonustemper-ui/app/static/vendor/htmx.min.js"

LOCKFILE="$ROOT/sonustemper/vendor/ffmpeg.lock.json"
CACHE_BASE="$HOME/Library/Caches/SonusTemper/vendor-ffmpeg"

DEFAULT_SOURCE="evermeet.cx"
DEFAULT_VERSION="latest"
DEFAULT_URL_FFMPEG_ARM="https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
DEFAULT_URL_FFPROBE_ARM="https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"
DEFAULT_URL_FFMPEG_X64="https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
DEFAULT_URL_FFPROBE_X64="https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"

ensure_ffmpeg_binaries() {
  local arch raw_arch lock_ok version source url_ffmpeg url_ffprobe sha_ffmpeg sha_ffprobe
  raw_arch="$(uname -m)"
  case "$raw_arch" in
    arm64) arch="arm64" ;;
    x86_64) arch="x86_64" ;;
    *)
      echo "[build] ERROR: unsupported macOS arch: $raw_arch"
      exit 1
      ;;
  esac

  lock_ok="0"
  if [[ -f "$LOCKFILE" ]]; then
    if python3 - "$LOCKFILE" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    data = json.load(open(path, "r", encoding="utf-8"))
except Exception:
    sys.exit(1)
entries = data.get("entries") or []
if not entries:
    sys.exit(1)
for entry in entries:
    for key in ("arch", "url_ffmpeg", "sha256_ffmpeg", "url_ffprobe", "sha256_ffprobe"):
        if not entry.get(key):
            sys.exit(1)
sys.exit(0)
PY
    then
      lock_ok="1"
    fi
  fi

  is_zip_payload() {
    local path magic
    path="$1"
    magic="$(xxd -p -l 4 "$path" 2>/dev/null || true)"
    if [[ "$magic" == "504b0304" ]]; then
      return 0
    fi
    file "$path" 2>/dev/null | grep -qi "zip archive"
  }

  is_macho_binary() {
    local path expect arch_ok
    path="$1"
    expect="$2"
    if ! file "$path" 2>/dev/null | grep -qi "Mach-O"; then
      return 1
    fi
    if [[ "$expect" == "arm64" ]]; then
      file "$path" 2>/dev/null | grep -qi "arm64" && return 0
      file "$path" 2>/dev/null | grep -qi "arm64e" && return 0
      return 1
    fi
    file "$path" 2>/dev/null | grep -qi "$expect"
  }

  fetch_binary() {
    local url dest name tmpdir tmpfile found
    url="$1"
    dest="$2"
    name="$3"
    tmpdir="$(mktemp -d)"
    tmpfile="$tmpdir/download"
    curl -L --fail --retry 3 -o "$tmpfile" "$url"
    if is_zip_payload "$tmpfile"; then
      unzip -q "$tmpfile" -d "$tmpdir"
      found="$(find "$tmpdir" -type f -name "$name" | head -n 1)"
      if [[ -z "$found" ]]; then
        echo "[build] ERROR: could not find $name in zip from $url"
        exit 1
      fi
      if is_zip_payload "$found"; then
        nested_dir="$(mktemp -d)"
        unzip -q "$found" -d "$nested_dir"
        found="$(find "$nested_dir" -type f -name "$name" | head -n 1)"
        if [[ -z "$found" ]]; then
          echo "[build] ERROR: could not find $name in nested zip from $url"
          exit 1
        fi
      fi
      cp "$found" "$dest"
      rm -f "$tmpfile"
    else
      cp "$tmpfile" "$dest"
    fi
    chmod +x "$dest"
  }

  bootstrap_lockfile() {
    echo "[build] ffmpeg lockfile missing/invalid; bootstrapping defaults"
    mkdir -p "$(dirname "$LOCKFILE")"
    tmpdir="$(mktemp -d)"
    for a in arm64 x86_64; do
      if [[ "$a" == "arm64" ]]; then
        url_ffmpeg="$DEFAULT_URL_FFMPEG_ARM"
        url_ffprobe="$DEFAULT_URL_FFPROBE_ARM"
      else
        url_ffmpeg="$DEFAULT_URL_FFMPEG_X64"
        url_ffprobe="$DEFAULT_URL_FFPROBE_X64"
      fi
      ffmpeg_path="$tmpdir/ffmpeg-$a"
      ffprobe_path="$tmpdir/ffprobe-$a"
      fetch_binary "$url_ffmpeg" "$ffmpeg_path" "ffmpeg"
      fetch_binary "$url_ffprobe" "$ffprobe_path" "ffprobe"
      sha_ffmpeg="$(shasum -a 256 "$ffmpeg_path" | awk '{print $1}')"
      sha_ffprobe="$(shasum -a 256 "$ffprobe_path" | awk '{print $1}')"
      export "URL_FFMPEG_$a=$url_ffmpeg"
      export "URL_FFPROBE_$a=$url_ffprobe"
      export "SHA_FFMPEG_$a=$sha_ffmpeg"
      export "SHA_FFPROBE_$a=$sha_ffprobe"
    done
    DEFAULT_VERSION="$DEFAULT_VERSION" DEFAULT_SOURCE="$DEFAULT_SOURCE" python3 - "$LOCKFILE" <<'PY'
import json
import os
import sys

lockfile = sys.argv[1]
data = {
    "version": os.environ.get("DEFAULT_VERSION", "latest"),
    "source": os.environ.get("DEFAULT_SOURCE", "evermeet.cx"),
    "entries": [
        {
            "arch": "arm64",
            "url_ffmpeg": os.environ["URL_FFMPEG_arm64"],
            "sha256_ffmpeg": os.environ["SHA_FFMPEG_arm64"],
            "url_ffprobe": os.environ["URL_FFPROBE_arm64"],
            "sha256_ffprobe": os.environ["SHA_FFPROBE_arm64"],
        },
        {
            "arch": "x86_64",
            "url_ffmpeg": os.environ["URL_FFMPEG_x86_64"],
            "sha256_ffmpeg": os.environ["SHA_FFMPEG_x86_64"],
            "url_ffprobe": os.environ["URL_FFPROBE_x86_64"],
            "sha256_ffprobe": os.environ["SHA_FFPROBE_x86_64"],
        },
    ],
}
with open(lockfile, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, sort_keys=True)
PY
  }

  read_lockfile() {
    read -r version source url_ffmpeg url_ffprobe sha_ffmpeg sha_ffprobe <<<"$(python3 - "$LOCKFILE" "$arch" <<'PY'
import json
import sys

lockfile = sys.argv[1]
arch = sys.argv[2]
data = json.load(open(lockfile, "r", encoding="utf-8"))
entry = next((e for e in data.get("entries", []) if e.get("arch") == arch), None)
if not entry:
    raise SystemExit(1)
print(
    data.get("version", "latest"),
    data.get("source", ""),
    entry["url_ffmpeg"],
    entry["url_ffprobe"],
    entry["sha256_ffmpeg"],
    entry["sha256_ffprobe"],
)
PY
)"
  }

  if [[ "$lock_ok" != "1" ]]; then
    bootstrap_lockfile
  fi

  read_lockfile

  cache_dir="$CACHE_BASE/$version/$arch"
  mkdir -p "$cache_dir"

  if [[ -f "$cache_dir/ffmpeg" ]]; then
    echo "$sha_ffmpeg  $cache_dir/ffmpeg" | shasum -a 256 -c - >/dev/null 2>&1 || rm -f "$cache_dir/ffmpeg"
  fi
  if [[ -f "$cache_dir/ffprobe" ]]; then
    echo "$sha_ffprobe  $cache_dir/ffprobe" | shasum -a 256 -c - >/dev/null 2>&1 || rm -f "$cache_dir/ffprobe"
  fi

  if [[ -f "$cache_dir/ffmpeg" ]] && ! is_macho_binary "$cache_dir/ffmpeg" "$arch"; then
    rm -f "$cache_dir/ffmpeg"
  fi
  if [[ -f "$cache_dir/ffprobe" ]] && ! is_macho_binary "$cache_dir/ffprobe" "$arch"; then
    rm -f "$cache_dir/ffprobe"
  fi

  if [[ ! -f "$cache_dir/ffmpeg" ]]; then
    fetch_binary "$url_ffmpeg" "$cache_dir/ffmpeg" "ffmpeg"
    if ! echo "$sha_ffmpeg  $cache_dir/ffmpeg" | shasum -a 256 -c -; then
      if [[ "$version" == "latest" ]]; then
        echo "[build] WARN: ffmpeg checksum mismatch for latest; refreshing lockfile"
        bootstrap_lockfile
        read_lockfile
        fetch_binary "$url_ffmpeg" "$cache_dir/ffmpeg" "ffmpeg"
        echo "$sha_ffmpeg  $cache_dir/ffmpeg" | shasum -a 256 -c -
      else
        echo "[build] ERROR: ffmpeg checksum mismatch"
        exit 1
      fi
    fi
  fi
  if [[ ! -f "$cache_dir/ffprobe" ]]; then
    fetch_binary "$url_ffprobe" "$cache_dir/ffprobe" "ffprobe"
    if ! echo "$sha_ffprobe  $cache_dir/ffprobe" | shasum -a 256 -c -; then
      if [[ "$version" == "latest" ]]; then
        echo "[build] WARN: ffprobe checksum mismatch for latest; refreshing lockfile"
        bootstrap_lockfile
        read_lockfile
        fetch_binary "$url_ffprobe" "$cache_dir/ffprobe" "ffprobe"
        echo "$sha_ffprobe  $cache_dir/ffprobe" | shasum -a 256 -c -
      else
        echo "[build] ERROR: ffprobe checksum mismatch"
        exit 1
      fi
    fi
  fi

  if ! is_macho_binary "$cache_dir/ffmpeg" "$arch"; then
    echo "[build] ERROR: cached ffmpeg has wrong architecture"
    file "$cache_dir/ffmpeg" || true
    head -c 16 "$cache_dir/ffmpeg" | xxd || true
    exit 1
  fi
  if ! is_macho_binary "$cache_dir/ffprobe" "$arch"; then
    echo "[build] ERROR: cached ffprobe has wrong architecture"
    file "$cache_dir/ffprobe" || true
    head -c 16 "$cache_dir/ffprobe" | xxd || true
    exit 1
  fi

  stage_dir="$STAGE_ROOT/sonustemper/vendor/ffmpeg/$arch"
  mkdir -p "$stage_dir"
  cp "$cache_dir/ffmpeg" "$stage_dir/ffmpeg"
  cp "$cache_dir/ffprobe" "$stage_dir/ffprobe"
  chmod +x "$stage_dir/ffmpeg" "$stage_dir/ffprobe"

  cp "$LOCKFILE" "$STAGE_ROOT/sonustemper/vendor/ffmpeg.lock.json"

  echo "[build] ffmpeg source: $source"
  echo "[build] ffmpeg arch: $arch"
  echo "[build] ffmpeg cached: $cache_dir"
  echo "[build] ffmpeg staged: $stage_dir"

  if ! is_macho_binary "$stage_dir/ffmpeg" "$arch"; then
    echo "[build] ERROR: staged ffmpeg has wrong architecture"
    file "$stage_dir/ffmpeg" || true
    head -c 16 "$stage_dir/ffmpeg" | xxd || true
    exit 1
  fi
  if ! is_macho_binary "$stage_dir/ffprobe" "$arch"; then
    echo "[build] ERROR: staged ffprobe has wrong architecture"
    file "$stage_dir/ffprobe" || true
    head -c 16 "$stage_dir/ffprobe" | xxd || true
    exit 1
  fi

  if ! "$stage_dir/ffmpeg" -version >/dev/null; then
    echo "[build] ERROR: staged ffmpeg is not executable"
    file "$stage_dir/ffmpeg" || true
    head -c 16 "$stage_dir/ffmpeg" | xxd || true
    exit 1
  fi
  if ! "$stage_dir/ffprobe" -version >/dev/null; then
    echo "[build] ERROR: staged ffprobe is not executable"
    file "$stage_dir/ffprobe" || true
    head -c 16 "$stage_dir/ffprobe" | xxd || true
    exit 1
  fi
}

ensure_ffmpeg_binaries

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
pip install -r "$STAGE_ROOT/requirements-native-macos.txt"
pip install pyinstaller

python -c "import fastapi, uvicorn, jinja2, mutagen; import multipart; import AppKit; print('native deps OK')"

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

find "$APP_BUILT/Contents/Resources/vendor/ffmpeg" -type f \( -name ffmpeg -o -name ffprobe \) -exec chmod +x {} \; 2>/dev/null || true
find "$APP_BUILT/Contents/Frameworks/vendor/ffmpeg" -type f \( -name ffmpeg -o -name ffprobe \) -exec chmod +x {} \; 2>/dev/null || true

echo "[build] bundled ffmpeg/ffprobe locations:"
find "$APP_BUILT/Contents" -type f \( -name ffmpeg -o -name ffprobe \) -print -exec file {} \; | tee "$APP_BUILT/Contents/ffmpeg_file_report.txt"

bundled_files="$(find "$APP_BUILT/Contents" -type f \( -name ffmpeg -o -name ffprobe \) -print)"
if [[ -z "$bundled_files" ]]; then
  echo "[build] ERROR: no bundled ffmpeg/ffprobe found in app"
  exit 1
fi

for f in $bundled_files; do
  if file "$f" | grep -qi "zip archive"; then
    echo "[build] ERROR: bundled $f is a zip archive"
    file "$f" || true
    head -c 16 "$f" | xxd || true
    exit 1
  fi
done

if [[ "$(uname -m)" == "arm64" ]]; then
  if echo "$bundled_files" | while read -r f; do file "$f"; done | grep -q "x86_64"; then
    echo "[build] ERROR: bundled ffmpeg/ffprobe are x86_64 on arm64 host"
    exit 1
  fi
fi

xattr -cr "$APP_BUILT" || true

# Copy back into the repo dist (overwrite)
DEST_DIST="$ROOT/build/native/dist"
mkdir -p "$DEST_DIST"
rm -rf "$DEST_DIST/SonusTemper.app"
rsync -a "$APP_BUILT" "$DEST_DIST/"

echo "[build] Done. App copied to: $DEST_DIST/SonusTemper.app"
