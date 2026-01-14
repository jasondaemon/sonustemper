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
LIBRARY_FILE = LIBRARY_DIR / "library.json"
SONGS_DIR = LIBRARY_DIR / "songs"


def ensure_data_roots() -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    SONGS_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)


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


def allocate_version_path(song_id: str, kind: str, ext: str) -> tuple[str, Path]:
    version_dir = song_versions_dir(song_id)
    version_dir.mkdir(parents=True, exist_ok=True)
    suffix = ext if ext.startswith(".") else f".{ext}"
    version_id = new_version_id(kind)
    return version_id, version_dir / f"{version_id}{suffix}"


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
