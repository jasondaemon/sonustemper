import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MASTER_IN_DIR = Path(os.getenv("IN_DIR", os.getenv("MASTER_IN_DIR", str(DATA_DIR / "mastering" / "in"))))
ANALYSIS_IN_DIR = Path(os.getenv("ANALYSIS_IN_DIR", str(DATA_DIR / "analysis" / "in")))

LIBRARY_DIR = Path(os.getenv("LIBRARY_DIR", str(DATA_DIR / "library")))
LIBRARY_FILE = LIBRARY_DIR / "library.json"
LIBRARY_VERSION = 1

_LOCK = threading.Lock()

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".aif", ".aiff"}


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _default_library() -> dict:
    return {"version": LIBRARY_VERSION, "songs": []}


def _ensure_dirs() -> None:
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)


def _scan_sources() -> list[dict]:
    sources = []
    for root, prefix in (
        (MASTER_IN_DIR, "in"),
        (ANALYSIS_IN_DIR, "analysis"),
    ):
        if not root.exists():
            continue
        for fp in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not fp.is_file() or fp.suffix.lower() not in AUDIO_EXTS:
                continue
            rel = f"{prefix}/{fp.name}"
            sources.append(
                {
                    "rel": rel,
                    "title": fp.stem,
                    "format": fp.suffix.lower().lstrip("."),
                }
            )
    return sources


def load_library() -> dict:
    _ensure_dirs()
    with _LOCK:
        if not LIBRARY_FILE.exists():
            lib = _default_library()
            for src in _scan_sources():
                lib["songs"].append(
                    {
                        "song_id": _new_id("s"),
                        "title": src["title"],
                        "created_at": _now_iso(),
                        "last_used_at": None,
                        "source": {
                            "rel": src["rel"],
                            "format": src["format"],
                            "duration_sec": None,
                        },
                        "tags": [],
                        "versions": [],
                    }
                )
            save_library(lib)
            return lib
        try:
            data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = _default_library()
        if not isinstance(data, dict):
            data = _default_library()
        data.setdefault("version", LIBRARY_VERSION)
        data.setdefault("songs", [])
        for song in data["songs"]:
            song.setdefault("versions", [])
            song.setdefault("tags", [])
        return data


def save_library(lib: dict) -> None:
    _ensure_dirs()
    tmp = LIBRARY_FILE.with_suffix(".json.tmp")
    payload = json.dumps(lib, indent=2)
    with _LOCK:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(LIBRARY_FILE)


def find_song_by_source(lib: dict, rel_path: str) -> dict | None:
    if not rel_path:
        return None
    rel_norm = rel_path.strip()
    for song in lib.get("songs", []):
        source = song.get("source") or {}
        if source.get("rel") == rel_norm:
            return song
    target_name = Path(rel_norm).name.lower()
    for song in lib.get("songs", []):
        source = song.get("source") or {}
        existing = source.get("rel") or ""
        if Path(existing).name.lower() == target_name:
            return song
    return None


def ensure_song_for_source(
    lib: dict,
    rel_path: str,
    title_hint: str | None = None,
    duration_sec: float | None = None,
    fmt: str | None = None,
) -> dict:
    rel_norm = (rel_path or "").strip()
    title = (title_hint or Path(rel_norm).stem).strip() or "Untitled"
    song = find_song_by_source(lib, rel_norm)
    if song:
        source = song.get("source") or {}
        source["rel"] = rel_norm
        if fmt:
            source["format"] = fmt
        if duration_sec is not None:
            source["duration_sec"] = duration_sec
        song["source"] = source
        song["title"] = title
        song["last_used_at"] = _now_iso()
        return song
    song = {
        "song_id": _new_id("s"),
        "title": title,
        "created_at": _now_iso(),
        "last_used_at": _now_iso(),
        "source": {
            "rel": rel_norm,
            "format": fmt,
            "duration_sec": duration_sec,
        },
        "tags": [],
        "versions": [],
    }
    lib.setdefault("songs", []).append(song)
    return song


def add_version(
    lib: dict,
    song_id: str,
    kind: str,
    label: str,
    rel: str,
    summary: dict | None = None,
    metrics: dict | None = None,
    tags: list | None = None,
) -> dict:
    song = next((s for s in lib.get("songs", []) if s.get("song_id") == song_id), None)
    if not song:
        raise ValueError("song_not_found")
    for existing in song.get("versions", []):
        if existing.get("rel") == rel:
            return existing
    entry = {
        "version_id": _new_id("v"),
        "kind": kind,
        "label": label,
        "rel": rel,
        "created_at": _now_iso(),
        "summary": summary or {},
        "metrics": metrics or {},
        "tags": tags or [],
    }
    song.setdefault("versions", []).append(entry)
    song["last_used_at"] = _now_iso()
    return entry


def update_last_used(lib: dict, song_id: str) -> None:
    for song in lib.get("songs", []):
        if song.get("song_id") == song_id:
            song["last_used_at"] = _now_iso()
            return


def rename_song(lib: dict, song_id: str, title: str) -> bool:
    clean = (title or "").strip()
    if not clean:
        return False
    for song in lib.get("songs", []):
        if song.get("song_id") == song_id:
            song["title"] = clean
            song["last_used_at"] = _now_iso()
            return True
    return False


def delete_song(lib: dict, song_id: str) -> bool:
    songs = lib.get("songs", [])
    before = len(songs)
    lib["songs"] = [s for s in songs if s.get("song_id") != song_id]
    return len(lib["songs"]) != before


def delete_version(lib: dict, song_id: str, version_id: str) -> dict | None:
    for song in lib.get("songs", []):
        if song.get("song_id") != song_id:
            continue
        versions = song.get("versions", [])
        for idx, version in enumerate(versions):
            if version.get("version_id") == version_id:
                return versions.pop(idx)
    return None


def latest_version(song: dict) -> dict | None:
    versions = song.get("versions") or []
    if not versions:
        return None
    def _key(v: dict) -> str:
        return v.get("created_at") or ""
    return sorted(versions, key=_key)[-1]
