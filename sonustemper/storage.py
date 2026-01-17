import os
import logging
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("sonustemper.storage")

def _uid_gid() -> tuple[str, str]:
    uid = str(os.geteuid()) if hasattr(os, "geteuid") else "n/a"
    gid = str(os.getegid()) if hasattr(os, "getegid") else "n/a"
    return uid, gid

_LAST_WRITE_ERR: str | None = None

def _mount_info(path: Path) -> str:
    try:
        target = path.resolve()
    except Exception:
        target = path
    best = ("", "")
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 4:
                    continue
                mountpoint = parts[1]
                opts = parts[3]
                if str(target) == mountpoint or str(target).startswith(mountpoint.rstrip("/") + "/"):
                    if len(mountpoint) > len(best[0]):
                        best = (mountpoint, opts)
    except Exception:
        return "unknown"
    if best[0]:
        return f"{best[0]} opts={best[1]}"
    return "unknown"

def _stat_summary(path: Path) -> str:
    try:
        st = path.stat()
    except Exception as exc:
        return f"stat_error={exc!r}"
    return f"mode={oct(st.st_mode)} uid={st.st_uid} gid={st.st_gid}"

def _can_write(root: Path) -> bool:
    global _LAST_WRITE_ERR
    test_dir = root / ".sonustemper_write_test"
    test_file = test_dir / "x"
    try:
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file.write_text("ok", encoding="utf-8")
        return True
    except Exception as exc:
        err_no = getattr(exc, "errno", None)
        _LAST_WRITE_ERR = f"{exc!r} errno={err_no}"
        logger.warning("[storage] write test failed at %s err=%r errno=%s", root, exc, err_no)
        return False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def _select_data_root() -> Path:
    env_root = os.getenv("DATA_DIR") or os.getenv("SONUSTEMPER_DATA_ROOT")
    require_root = os.getenv("SONUSTEMPER_REQUIRE_DATA_ROOT") == "1"
    root = Path(env_root) if env_root else Path("/data")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if _can_write(root):
        return root
    if require_root:
        uid, gid = _uid_gid()
        detail = _LAST_WRITE_ERR or "unknown"
        mount = _mount_info(root)
        stat_line = _stat_summary(root)
        raise RuntimeError(
            "DATA_ROOT not writable: "
            f"path={root} err={detail} uid={uid} gid={gid} stat={stat_line} mount={mount}"
        )
    fallback = Path.cwd() / "data"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except Exception:
        return fallback
    return fallback


DATA_ROOT = _select_data_root()
PRESETS_DIR = DATA_ROOT / "presets"
LIBRARY_DIR = DATA_ROOT / "library"
LIBRARY_IMPORT_DIR = LIBRARY_DIR / "import"
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
    failures = []
    uid, gid = _uid_gid()
    preset_dirs = [
        PRESETS_DIR,
        PRESETS_DIR / "user",
        PRESETS_DIR / "generated",
        PRESETS_DIR / "builtin",
    ]
    paths = preset_dirs + [LIBRARY_DIR, SONGS_DIR, LIBRARY_IMPORT_DIR, PREVIEWS_DIR, LIBRARY_DB.parent]
    for path in paths:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            logger.warning(
                "[storage] Permission denied creating %s (DATA_ROOT=%s uid=%s gid=%s err=%r). "
                "Fix bind mount permissions or run container with matching uid/gid.",
                path,
                DATA_ROOT,
                uid,
                gid,
                exc,
            )
            failures.append(path)
    if failures:
        detail = _LAST_WRITE_ERR or "unknown"
        mount = _mount_info(DATA_ROOT)
        stat_line = _stat_summary(DATA_ROOT)
        raise RuntimeError(
            "DATA_ROOT not writable: "
            f"path={DATA_ROOT} err={detail} uid={uid} gid={gid} stat={stat_line} mount={mount} "
            f"failed={','.join(str(p) for p in failures)}"
        )


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
