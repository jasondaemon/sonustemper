import os
import re
import uuid
from datetime import datetime
from pathlib import Path

def _select_data_root() -> Path:
    env_root = os.getenv("DATA_DIR") or os.getenv("SONUSTEMPER_DATA_ROOT")
    if env_root:
        return Path(env_root)
    root = Path("/data")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        return Path.cwd() / "data"
    return root


DATA_ROOT = _select_data_root()
PRESETS_DIR = DATA_ROOT / "presets"
LIBRARY_DIR = DATA_ROOT / "library"
PREVIEWS_DIR = DATA_ROOT / "previews"
_env_db = (os.getenv("SONUSTEMPER_LIBRARY_DB") or os.getenv("LIBRARY_DB_PATH") or "").strip()
if _env_db:
    LIBRARY_DB = Path(_env_db)
    if not LIBRARY_DB.is_absolute():
        LIBRARY_DB = DATA_ROOT / LIBRARY_DB
else:
    LIBRARY_DB = LIBRARY_DIR / "library.sqlite3"
SONGS_DIR = LIBRARY_DIR / "songs"


def ensure_data_roots() -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    SONGS_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        LIBRARY_DB.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass


def detect_mount_type(p: Path) -> str:
    try:
        p = p.resolve()
    except Exception:
        p = Path(str(p))
    best_mount = ""
    best_type = ""
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mountpoint = parts[1]
                fstype = parts[2]
                if str(p) == mountpoint or str(p).startswith(mountpoint.rstrip("/") + "/"):
                    if len(mountpoint) > len(best_mount):
                        best_mount = mountpoint
                        best_type = fstype
    except Exception:
        return "unknown"
    return best_type or "unknown"


def describe_db_location() -> dict:
    return {
        "env_db": _env_db or "",
        "LIBRARY_DB": str(LIBRARY_DB),
        "db_under_data": str(LIBRARY_DB).startswith(str(DATA_ROOT)),
        "mount_type": detect_mount_type(LIBRARY_DB),
    }


def new_song_id() -> str:
    return f"s_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def new_version_id(kind: str) -> str:
    tag = safe_filename(kind or "version") or "version"
    return f"v_{tag}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def song_root(song_id: str) -> Path:
    return SONGS_DIR / song_id


def song_source_dir(song_id: str) -> Path:
    return song_root(song_id) / "source"


def song_versions_dir(song_id: str) -> Path:
    return song_root(song_id) / "versions"

def version_dir(song_id: str, version_id: str) -> Path:
    return song_versions_dir(song_id) / version_id


def safe_filename(name: str) -> str:
    raw = Path(name or "").name
    if not raw:
        return ""
    base = re.sub(r"[^\w.\-]+", "_", raw.strip())
    base = re.sub(r"_+", "_", base).strip("._")
    return base


def allocate_source_path(song_id: str, original_filename: str) -> Path:
    source_dir = song_source_dir(song_id)
    source_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_filename(original_filename)
    if not safe:
        safe = "source.wav"
    dest = source_dir / safe
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    idx = 1
    while True:
        candidate = source_dir / f"{stem}-{idx}{suffix}"
        if not candidate.exists():
            return candidate
        idx += 1


def allocate_version_path(
    song_id: str,
    kind: str,
    ext: str,
    filename: str | None = None,
) -> tuple[str, Path]:
    suffix = ext if ext.startswith(".") else f".{ext}"
    version_id = new_version_id(kind)
    target_dir = version_dir(song_id, version_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    base = safe_filename(filename or kind or "output") or "output"
    return version_id, target_dir / f"{base}{suffix}"


def rel_from_path(path: Path) -> str:
    return str(path.relative_to(DATA_ROOT)).replace("\\", "/")


def resolve_rel(rel: str) -> Path:
    rel = (rel or "").strip().lstrip("/").replace("\\", "/")
    if not rel:
        raise ValueError("missing_path")
    allowed = ("library/", "presets/", "previews/")
    if rel not in ("library", "presets", "previews") and not rel.startswith(allowed):
        raise ValueError("invalid_path")
    target = (DATA_ROOT / rel).resolve()
    root = DATA_ROOT.resolve()
    if target != root and root not in target.parents:
        raise ValueError("invalid_path")
    return target
