import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from .storage import (
    LIBRARY_FILE,
    ensure_data_roots,
)

LIBRARY_VERSION = 1
_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _default_library() -> dict:
    return {"version": LIBRARY_VERSION, "songs": []}

def _clean_title(raw: str) -> str:
    cleaned = (raw or "").strip()
    if not cleaned:
        return "Untitled"
    cleaned = cleaned.replace("_", " ").replace("-", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned or "Untitled"

def _format_from_rel(rel: str | None) -> str | None:
    if not rel:
        return None
    return Path(rel).suffix.lower().lstrip(".") or None


def create_empty_library_if_missing() -> dict:
    ensure_data_roots()
    with _LOCK:
        if LIBRARY_FILE.exists():
            return load_library()
        data = _default_library()
        _write_library(data)
        return data


def load_library() -> dict:
    ensure_data_roots()
    with _LOCK:
        if not LIBRARY_FILE.exists():
            data = _default_library()
            _write_library(data)
            return data
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
            song.setdefault("chain", {})
            source = song.get("source") or {}
            source.setdefault("metrics", {})
            source.setdefault("analyzed", False)
            source.setdefault("kind", "source")
            song["source"] = source
            title = song.get("title") or "Untitled"
            song["title"] = _clean_title(title)
            for version in song.get("versions") or []:
                if "title" not in version:
                    version["title"] = _clean_title(version.get("label") or version.get("name") or version.get("kind") or "Version")
                renditions = version.get("renditions")
                if not isinstance(renditions, list):
                    renditions = []
                if not renditions:
                    rel = version.get("rel")
                    if rel:
                        fmt = _format_from_rel(rel)
                        renditions = [{"format": fmt, "rel": rel}]
                version["renditions"] = renditions
        return data


def _write_library(lib: dict) -> None:
    tmp = LIBRARY_FILE.with_suffix(".json.tmp")
    payload = json.dumps(lib, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(LIBRARY_FILE)


def save_library(lib: dict) -> None:
    ensure_data_roots()
    with _LOCK:
        _write_library(lib)


def find_song_by_source(lib: dict, rel_path: str) -> dict | None:
    rel_norm = (rel_path or "").strip()
    if not rel_norm:
        return None
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
    song_id: str | None = None,
    title_hint: str | None = None,
    duration_sec: float | None = None,
    fmt: str | None = None,
    metrics: dict | None = None,
    analyzed: bool | None = None,
) -> dict:
    rel_norm = (rel_path or "").strip()
    title = _clean_title(title_hint or Path(rel_norm).stem)
    song = find_song_by_source(lib, rel_norm)
    if song:
        source = song.get("source") or {}
        source["rel"] = rel_norm
        if fmt:
            source["format"] = fmt
        if duration_sec is not None:
            source["duration_sec"] = duration_sec
        if metrics is not None:
            source["metrics"] = metrics
        if analyzed is not None:
            source["analyzed"] = analyzed
        source.setdefault("kind", "source")
        song["source"] = source
        song["title"] = title
        song["last_used_at"] = _now_iso()
        song.setdefault("chain", {})
        return song
    song = {
        "song_id": song_id or _new_id("s"),
        "title": title,
        "created_at": _now_iso(),
        "last_used_at": _now_iso(),
        "chain": {},
        "source": {
            "kind": "source",
            "rel": rel_norm,
            "format": fmt,
            "duration_sec": duration_sec,
            "analyzed": bool(analyzed) if analyzed is not None else False,
            "metrics": metrics or {},
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
    rel: str | None,
    summary: dict | None = None,
    metrics: dict | None = None,
    tags: list | None = None,
    version_id: str | None = None,
    renditions: list | None = None,
    title: str | None = None,
) -> dict:
    song = next((s for s in lib.get("songs", []) if s.get("song_id") == song_id), None)
    if not song:
        raise ValueError("song_not_found")
    for existing in song.get("versions", []):
        if rel and existing.get("rel") == rel:
            return existing
        for rendition in existing.get("renditions") or []:
            if rel and rendition.get("rel") == rel:
                return existing
        if renditions:
            existing_rels = {r.get("rel") for r in existing.get("renditions") or []}
            for rendition in renditions:
                if rendition.get("rel") in existing_rels:
                    return existing
    title_value = _clean_title(title or label or kind or "Version")
    if renditions is None:
        renditions = []
    if not renditions and rel:
        fmt = _format_from_rel(rel)
        renditions = [{"format": fmt, "rel": rel}]
    entry = {
        "version_id": version_id or _new_id("v"),
        "kind": kind,
        "title": title_value,
        "label": label or title_value,
        "created_at": _now_iso(),
        "summary": summary or {},
        "metrics": metrics or {},
        "tags": tags or [],
        "renditions": renditions,
    }
    if rel:
        entry["rel"] = rel
    song.setdefault("versions", []).append(entry)
    song["last_used_at"] = _now_iso()
    return entry


def version_primary_rendition(version: dict) -> dict | None:
    renditions = version.get("renditions") or []
    if not renditions:
        rel = version.get("rel")
        if rel:
            fmt = _format_from_rel(rel)
            return {"format": fmt, "rel": rel}
        return None
    prefer = ["wav", "flac", "aiff", "aif", "m4a", "aac", "mp3", "ogg"]
    for fmt in prefer:
        for rendition in renditions:
            if (rendition.get("format") or "").lower() == fmt:
                return rendition
    return renditions[0]


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
