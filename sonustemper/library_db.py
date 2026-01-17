import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import (
    DATA_ROOT,
    LIBRARY_DB,
    SONGS_DIR,
    LIBRARY_IMPORT_DIR,
    ensure_data_roots,
    new_song_id,
    new_version_id,
    detect_mount_type,
    song_source_dir,
    safe_filename,
    rel_from_path,
)
from .logging_util import log_debug, log_summary, log_error


LIBRARY_VERSION = 1
_WRITE_LOCK = threading.Lock()
_INIT_LOCK = threading.Lock()
_DB_READY = False

METRIC_FIELDS = [
    "duration_sec",
    "lufs_i",
    "lra",
    "true_peak_dbtp",
    "target_i",
    "target_tp",
    "delta_i",
    "tp_margin",
    "crest_factor",
    "dynamic_range",
    "rms_level",
    "peak_level",
    "noise_floor",
    "stereo_corr",
    "width",
]

METRIC_KEY_MAP = {
    "duration_sec": ["duration_sec", "duration", "dur"],
    "lufs_i": ["lufs_i", "I", "lufs"],
    "lra": ["lra", "LRA"],
    "true_peak_dbtp": ["true_peak_dbtp", "true_peak_db", "TP", "true_peak"],
    "target_i": ["target_i", "target_I", "target_lufs"],
    "target_tp": ["target_tp", "target_TP", "target_tp"],
    "delta_i": ["delta_i", "delta_I", "delta_lufs"],
    "tp_margin": ["tp_margin", "tp_margin_db"],
    "crest_factor": ["crest_factor", "crest_db", "crest"],
    "dynamic_range": ["dynamic_range", "dynamic_range_db", "dr", "DR"],
    "rms_level": ["rms_level", "rms_db", "rms"],
    "peak_level": ["peak_level", "peak_db", "peak"],
    "noise_floor": ["noise_floor", "noise_floor_db"],
    "stereo_corr": ["stereo_corr", "stereo_correlation"],
    "width": ["width", "stereo_width"],
}

METRIC_SUMMARY_KEYS = {
    "duration_sec",
    "lufs_i",
    "lufs",
    "I",
    "lra",
    "LRA",
    "true_peak_dbtp",
    "true_peak_db",
    "true_peak",
    "TP",
    "target_lufs",
    "target_i",
    "target_I",
    "target_tp",
    "target_TP",
    "delta_i",
    "delta_I",
    "tp_margin",
    "crest_factor",
    "crest_db",
    "dynamic_range",
    "dynamic_range_db",
    "rms_level",
    "rms_db",
    "peak_level",
    "peak_db",
    "noise_floor",
    "noise_floor_db",
    "stereo_corr",
    "width",
}

PREFERRED_RENDITION_ORDER = ["wav", "flac", "aiff", "aif", "m4a", "aac", "mp3", "ogg"]
LIBRARY_AUDIO_EXTS = {".wav", ".flac", ".aiff", ".aif", ".mp3", ".m4a", ".aac", ".ogg"}


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _clean_title(raw: str) -> str:
    cleaned = (raw or "").strip()
    if not cleaned:
        return "Untitled"
    cleaned = cleaned.replace("_", " ").replace("-", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned or "Untitled"


def _connect() -> sqlite3.Connection:
    ensure_data_roots()
    conn = sqlite3.connect(LIBRARY_DB, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db() -> None:
    global _DB_READY
    if _DB_READY:
        return
    with _INIT_LOCK:
        if _DB_READY:
            return
        ensure_data_roots()
        with _WRITE_LOCK:
            conn = _connect()
            try:
                mount_type = detect_mount_type(LIBRARY_DB)
                if mount_type in {"nfs", "cifs", "smbfs", "sshfs", "fuse.sshfs"}:
                    log_summary(
                        "db",
                        "DB is on network fs; disabling WAL",
                        mount=mount_type,
                        db=str(LIBRARY_DB),
                    )
                    conn.execute("PRAGMA journal_mode = DELETE")
                    conn.execute("PRAGMA synchronous = FULL")
                else:
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA synchronous = NORMAL")
                conn.execute("PRAGMA busy_timeout = 30000")
                jm = conn.execute("PRAGMA journal_mode").fetchone()
                log_debug("db", "journal_mode set", mode=str(jm[0]) if jm else "unknown")
                conn.executescript(
                    """
                CREATE TABLE IF NOT EXISTS songs (
                    song_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    source_rel TEXT NOT NULL UNIQUE,
                    source_format TEXT,
                    duration_sec REAL,
                    source_analyzed INTEGER NOT NULL DEFAULT 0,
                    source_metrics_json TEXT NOT NULL DEFAULT "{}",
                    file_mtime_utc TEXT,
                    is_demo INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS song_metrics (
                    song_id TEXT PRIMARY KEY REFERENCES songs(song_id) ON DELETE CASCADE,
                    duration_sec REAL,
                    lufs_i REAL,
                    lra REAL,
                    true_peak_dbtp REAL,
                    target_i REAL,
                    target_tp REAL,
                    delta_i REAL,
                    tp_margin REAL,
                    crest_factor REAL,
                    dynamic_range REAL,
                    rms_level REAL,
                    peak_level REAL,
                    noise_floor REAL,
                    stereo_corr REAL,
                    width REAL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    version_id TEXT PRIMARY KEY,
                    song_id TEXT NOT NULL REFERENCES songs(song_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    label TEXT NOT NULL,
                    utility TEXT,
                    voicing TEXT,
                    loudness_profile TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    summary_json TEXT NOT NULL DEFAULT "{}",
                    metrics_json TEXT NOT NULL DEFAULT "{}"
                );
                CREATE TABLE IF NOT EXISTS version_metrics (
                    version_id TEXT PRIMARY KEY REFERENCES versions(version_id) ON DELETE CASCADE,
                    duration_sec REAL,
                    lufs_i REAL,
                    lra REAL,
                    true_peak_dbtp REAL,
                    target_i REAL,
                    target_tp REAL,
                    delta_i REAL,
                    tp_margin REAL,
                    crest_factor REAL,
                    dynamic_range REAL,
                    rms_level REAL,
                    peak_level REAL,
                    noise_floor REAL,
                    stereo_corr REAL,
                    width REAL
                );
                CREATE TABLE IF NOT EXISTS renditions (
                    rendition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id TEXT NOT NULL REFERENCES versions(version_id) ON DELETE CASCADE,
                    format TEXT NOT NULL,
                    rel TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_versions_song_created ON versions(song_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_renditions_version ON renditions(version_id);
                CREATE INDEX IF NOT EXISTS idx_songs_last_used ON songs(last_used_at);
                CREATE INDEX IF NOT EXISTS idx_songs_title ON songs(title);
                """
                )
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(versions)").fetchall()}
                if "utility" not in columns:
                    conn.execute("ALTER TABLE versions ADD COLUMN utility TEXT")
                if "voicing" not in columns:
                    conn.execute("ALTER TABLE versions ADD COLUMN voicing TEXT")
                if "loudness_profile" not in columns:
                    conn.execute("ALTER TABLE versions ADD COLUMN loudness_profile TEXT")
                song_cols = {row["name"] for row in conn.execute("PRAGMA table_info(songs)").fetchall()}
                if "is_demo" not in song_cols:
                    conn.execute("ALTER TABLE songs ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
                conn.execute("UPDATE songs SET source_metrics_json = '{}' WHERE source_metrics_json IS NOT NULL AND source_metrics_json != '{}'")
                rows = conn.execute(
                    "SELECT version_id, summary_json, voicing, loudness_profile FROM versions"
                ).fetchall()
                for row in rows:
                    summary_raw = row["summary_json"] or ""
                    if not summary_raw or summary_raw == "{}":
                        continue
                    try:
                        summary = json.loads(summary_raw)
                    except Exception:
                        summary = {}
                    if not isinstance(summary, dict):
                        summary = {}
                    voicing = row["voicing"] or summary.get("voicing")
                    profile = row["loudness_profile"] or summary.get("loudness_profile")
                    if voicing is None and profile is None and summary_raw != "{}":
                        conn.execute(
                            "UPDATE versions SET summary_json = ? WHERE version_id = ?",
                            ("{}", row["version_id"]),
                        )
                        continue
                    conn.execute(
                        "UPDATE versions SET voicing = ?, loudness_profile = ?, summary_json = ? WHERE version_id = ?",
                        (voicing, profile, "{}", row["version_id"]),
                    )
                conn.execute("UPDATE versions SET metrics_json = '{}' WHERE metrics_json IS NOT NULL AND metrics_json != '{}'")
            finally:
                conn.close()
        _DB_READY = True


def _select_metrics(metrics: dict | None, prefer_output: bool) -> dict:
    if not isinstance(metrics, dict):
        return {}
    if "output" in metrics or "input" in metrics:
        if prefer_output and isinstance(metrics.get("output"), dict):
            return metrics["output"]
        if not prefer_output and isinstance(metrics.get("input"), dict):
            return metrics["input"]
        for key in ("output", "input"):
            if isinstance(metrics.get(key), dict):
                return metrics[key]
    return metrics


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value != value:
            return None
        return float(value)
    return None


def _map_metrics(metrics: dict | None, *, prefer_output: bool, duration_override: float | None = None) -> dict:
    selected = _select_metrics(metrics, prefer_output)
    mapped: dict[str, float | None] = {field: None for field in METRIC_FIELDS}
    for field, keys in METRIC_KEY_MAP.items():
        for key in keys:
            if key in selected:
                val = _coerce_float(selected.get(key))
                if val is not None:
                    mapped[field] = val
                    break
    if mapped["duration_sec"] is None and duration_override is not None:
        mapped["duration_sec"] = duration_override
    return mapped


def _metrics_from_row(row: sqlite3.Row | None, *, duration_override: float | None = None) -> dict:
    metrics = {field: None for field in METRIC_FIELDS}
    if row:
        for field in METRIC_FIELDS:
            metrics[field] = row[field]
    if metrics["duration_sec"] is None and duration_override is not None:
        metrics["duration_sec"] = duration_override
    if metrics["true_peak_dbtp"] is not None:
        metrics["true_peak_db"] = metrics["true_peak_dbtp"]
        metrics["TP"] = metrics["true_peak_dbtp"]
    if metrics["crest_factor"] is not None:
        metrics["crest_db"] = metrics["crest_factor"]
    if metrics["dynamic_range"] is not None:
        metrics["dynamic_range_db"] = metrics["dynamic_range"]
    if metrics["lufs_i"] is not None:
        metrics["I"] = metrics["lufs_i"]
    if metrics["lra"] is not None:
        metrics["LRA"] = metrics["lra"]
    if metrics["rms_level"] is not None:
        metrics["rms_db"] = metrics["rms_level"]
    if metrics["peak_level"] is not None:
        metrics["peak_db"] = metrics["peak_level"]
    if metrics["noise_floor"] is not None:
        metrics["noise_floor_db"] = metrics["noise_floor"]
    if metrics["target_i"] is not None:
        metrics["target_I"] = metrics["target_i"]
    if metrics["target_tp"] is not None:
        metrics["target_TP"] = metrics["target_tp"]
    if metrics["delta_i"] is not None:
        metrics["delta_I"] = metrics["delta_i"]
    return metrics


def _format_from_rel(rel: str | None) -> str | None:
    if not rel:
        return None
    return Path(rel).suffix.lower().lstrip(".") or None


def _merge_summary_metrics(metrics: dict | None, summary: dict | None) -> dict:
    merged = dict(metrics or {})
    if not isinstance(summary, dict):
        return merged
    for key, value in summary.items():
        if key not in METRIC_SUMMARY_KEYS:
            continue
        if key in merged:
            continue
        merged[key] = value
    return merged


def _strip_summary_metrics(summary: dict | None) -> dict:
    if not isinstance(summary, dict):
        return {}
    return {k: v for k, v in summary.items() if k not in METRIC_SUMMARY_KEYS}


def _utility_from_kind(kind: str | None) -> str:
    kind = (kind or "").strip().lower()
    mapping = {
        "master": "Master",
        "aitk": "AITK",
        "ai_tool": "AITK",
        "eq": "EQ",
        "noise_clean": "Noise Cleanup",
        "manual": "Manual",
    }
    return mapping.get(kind, (kind or "Utility").upper())


def _primary_rendition(renditions: list[dict]) -> dict | None:
    if not renditions:
        return None
    if len(renditions) == 1:
        return renditions[0]
    for ext in PREFERRED_RENDITION_ORDER:
        for rendition in renditions:
            if (rendition.get("format") or "").lower() == ext:
                return rendition
    return renditions[0]


def _iter_audio_files(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return [
        fp for fp in folder.iterdir()
        if fp.is_file() and fp.suffix.lower() in LIBRARY_AUDIO_EXTS
    ]


def _pick_preferred_file(files: list[Path]) -> Path | None:
    if not files:
        return None
    prefer = ["wav", "flac", "aiff", "aif", "m4a", "aac", "mp3", "ogg"]
    by_ext = {fp.suffix.lower().lstrip("."): fp for fp in files}
    for ext in prefer:
        hit = by_ext.get(ext)
        if hit:
            return hit
    return files[0]


def list_library() -> dict:
    init_db()
    t0 = time.monotonic()
    conn = _connect()
    try:
        t_conn = time.monotonic()
        songs_rows = conn.execute("SELECT * FROM songs ORDER BY created_at DESC").fetchall()
        t_songs = (time.monotonic() - t_conn) * 1000
        if t_songs > 250:
            mount = detect_mount_type(LIBRARY_DB)
            jm = conn.execute("PRAGMA journal_mode").fetchone()
            log_summary(
                "db",
                "list_library query slow",
                query="songs",
                ms=round(t_songs, 1),
                mount=mount,
                journal=(jm[0] if jm else None),
            )

        t_q = time.monotonic()
        song_metrics_rows = conn.execute("SELECT * FROM song_metrics").fetchall()
        t_song_metrics = (time.monotonic() - t_q) * 1000
        if t_song_metrics > 250:
            log_summary("db", "list_library query slow", query="song_metrics", ms=round(t_song_metrics, 1))

        t_q = time.monotonic()
        version_rows = conn.execute("SELECT * FROM versions ORDER BY created_at DESC").fetchall()
        t_versions = (time.monotonic() - t_q) * 1000
        if t_versions > 250:
            log_summary("db", "list_library query slow", query="versions", ms=round(t_versions, 1))

        t_q = time.monotonic()
        version_metrics_rows = conn.execute("SELECT * FROM version_metrics").fetchall()
        t_version_metrics = (time.monotonic() - t_q) * 1000
        if t_version_metrics > 250:
            log_summary("db", "list_library query slow", query="version_metrics", ms=round(t_version_metrics, 1))

        t_q = time.monotonic()
        rendition_rows = conn.execute("SELECT version_id, format, rel FROM renditions ORDER BY rendition_id").fetchall()
        t_renditions = (time.monotonic() - t_q) * 1000
        if t_renditions > 250:
            log_summary("db", "list_library query slow", query="renditions", ms=round(t_renditions, 1))
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        log_error("db", "list_library failed", ms=round(elapsed, 1), err=str(exc))
        raise
    finally:
        conn.close()

    song_metrics = {row["song_id"]: row for row in song_metrics_rows}
    version_metrics = {row["version_id"]: row for row in version_metrics_rows}
    renditions_map: dict[str, list[dict]] = {}
    for row in rendition_rows:
        renditions_map.setdefault(row["version_id"], []).append({
            "format": row["format"],
            "rel": row["rel"],
        })

    versions_by_song: dict[str, list[dict]] = {}
    latest_by_song: dict[str, dict] = {}
    for row in version_rows:
        renditions = renditions_map.get(row["version_id"], [])
        summary = {}
        if row["voicing"]:
            summary["voicing"] = row["voicing"]
        if row["loudness_profile"]:
            summary["loudness_profile"] = row["loudness_profile"]
        metrics = _metrics_from_row(version_metrics.get(row["version_id"]))
        utility = row["utility"] or _utility_from_kind(row["kind"])
        version = {
            "version_id": row["version_id"],
            "song_id": row["song_id"],
            "kind": row["kind"],
            "title": row["title"],
            "label": row["label"],
            "utility": utility,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "summary": summary,
            "metrics": metrics,
            "renditions": renditions,
        }
        primary = _primary_rendition(renditions)
        if primary:
            version["rel"] = primary.get("rel")
        versions_by_song.setdefault(row["song_id"], []).append(version)
        current = latest_by_song.get(row["song_id"])
        if not current or (row["created_at"] or "") > (current.get("created_at") or ""):
            latest_by_song[row["song_id"]] = version

    songs = []
    for row in songs_rows:
        metrics = _metrics_from_row(song_metrics.get(row["song_id"]), duration_override=row["duration_sec"])
        song = {
            "song_id": row["song_id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_used_at": row["last_used_at"],
            "is_demo": bool(row["is_demo"]) if "is_demo" in row.keys() else False,
            "chain": {},
            "tags": [],
            "source": {
                "kind": "source",
                "rel": row["source_rel"],
                "format": row["source_format"],
                "duration_sec": row["duration_sec"],
                "analyzed": bool(row["source_analyzed"]),
                "metrics": metrics,
            },
            "versions": versions_by_song.get(row["song_id"], []),
            "latest_version": latest_by_song.get(row["song_id"]),
        }
        songs.append(song)

    total_ms = (time.monotonic() - t0) * 1000
    if total_ms > 500:
        log_summary(
            "db",
            "list_library slow",
            ms=round(total_ms, 1),
            songs=len(songs),
            versions=len(version_rows),
        )
    else:
        log_debug("db", "list_library ok", ms=round(total_ms, 1), songs=len(songs), versions=len(version_rows))
    return {"version": LIBRARY_VERSION, "songs": songs}


def get_song(song_id: str) -> dict | None:
    init_db()
    conn = _connect()
    try:
        song_row = conn.execute("SELECT * FROM songs WHERE song_id = ?", (song_id,)).fetchone()
        if not song_row:
            return None
        song_metrics_row = conn.execute(
            "SELECT * FROM song_metrics WHERE song_id = ?",
            (song_id,),
        ).fetchone()
        version_rows = conn.execute(
            "SELECT * FROM versions WHERE song_id = ? ORDER BY created_at DESC",
            (song_id,),
        ).fetchall()
        version_metrics_rows = conn.execute(
            "SELECT * FROM version_metrics WHERE version_id IN (SELECT version_id FROM versions WHERE song_id = ?)",
            (song_id,),
        ).fetchall()
        rendition_rows = conn.execute(
            "SELECT version_id, format, rel FROM renditions WHERE version_id IN (SELECT version_id FROM versions WHERE song_id = ?) ORDER BY rendition_id",
            (song_id,),
        ).fetchall()
    finally:
        conn.close()

    version_metrics = {row["version_id"]: row for row in version_metrics_rows}
    renditions_map: dict[str, list[dict]] = {}
    for row in rendition_rows:
        renditions_map.setdefault(row["version_id"], []).append({
            "format": row["format"],
            "rel": row["rel"],
        })

    versions = []
    for row in version_rows:
        renditions = renditions_map.get(row["version_id"], [])
        summary = {}
        if row["voicing"]:
            summary["voicing"] = row["voicing"]
        if row["loudness_profile"]:
            summary["loudness_profile"] = row["loudness_profile"]
        metrics = _metrics_from_row(version_metrics.get(row["version_id"]))
        utility = row["utility"] or _utility_from_kind(row["kind"])
        version = {
            "version_id": row["version_id"],
            "song_id": row["song_id"],
            "kind": row["kind"],
            "title": row["title"],
            "label": row["label"],
            "utility": utility,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "summary": summary,
            "metrics": metrics,
            "renditions": renditions,
        }
        primary = _primary_rendition(renditions)
        if primary:
            version["rel"] = primary.get("rel")
        versions.append(version)

    metrics = _metrics_from_row(song_metrics_row, duration_override=song_row["duration_sec"])
    return {
        "song_id": song_row["song_id"],
        "title": song_row["title"],
        "created_at": song_row["created_at"],
        "updated_at": song_row["updated_at"],
        "last_used_at": song_row["last_used_at"],
        "is_demo": bool(song_row["is_demo"]) if "is_demo" in song_row.keys() else False,
        "chain": {},
        "tags": [],
        "source": {
            "kind": "source",
            "rel": song_row["source_rel"],
            "format": song_row["source_format"],
            "duration_sec": song_row["duration_sec"],
            "analyzed": bool(song_row["source_analyzed"]),
            "metrics": metrics,
        },
        "versions": versions,
    }


def latest_version(song_id: str) -> dict | None:
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT version_id FROM versions WHERE song_id = ? ORDER BY created_at DESC LIMIT 1",
            (song_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    song = get_song(song_id)
    if not song:
        return None
    for version in song.get("versions", []):
        if version.get("version_id") == row["version_id"]:
            return version
    return None


def find_song_by_source(rel: str) -> dict | None:
    init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT song_id FROM songs WHERE source_rel = ?", (rel,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return get_song(row["song_id"])


def find_by_rel(rel: str) -> tuple[dict | None, dict | None]:
    rel = (rel or "").strip()
    if not rel:
        return None, None
    init_db()
    conn = _connect()
    try:
        song_row = conn.execute("SELECT song_id FROM songs WHERE source_rel = ?", (rel,)).fetchone()
        if song_row:
            song = get_song(song_row["song_id"])
            return song, None
        join_row = conn.execute(
            """
            SELECT v.song_id, v.version_id
            FROM renditions r
            JOIN versions v ON r.version_id = v.version_id
            WHERE r.rel = ?
            LIMIT 1
            """,
            (rel,),
        ).fetchone()
    finally:
        conn.close()
    if not join_row:
        return None, None
    song = get_song(join_row["song_id"])
    if not song:
        return None, None
    for version in song.get("versions", []):
        if version.get("version_id") == join_row["version_id"]:
            return song, version
    return song, None


def upsert_song_for_source(
    rel: str,
    title_hint: str | None,
    duration_sec: float | None,
    fmt: str | None,
    metrics: dict | None,
    analyzed: bool,
    song_id: str | None = None,
    file_mtime_utc: str | None = None,
    is_demo: bool | None = None,
) -> dict:
    init_db()
    now = _now_iso()
    title = _clean_title(title_hint or Path(rel).stem)
    mapped_metrics = _map_metrics(metrics, prefer_output=False, duration_override=duration_sec)
    raw_metrics_json = "{}"

    with _WRITE_LOCK:
        conn = _connect()
        try:
            demo_value = None if is_demo is None else (1 if is_demo else 0)
            existing_by_id = None
            if song_id:
                existing_by_id = conn.execute(
                    "SELECT song_id, source_rel, is_demo FROM songs WHERE song_id = ?", (song_id,)
                ).fetchone()
            if existing_by_id:
                demo_value = existing_by_id["is_demo"] if demo_value is None else demo_value
                conn.execute(
                    """
                    UPDATE songs
                    SET title = ?, updated_at = ?, last_used_at = ?, source_rel = ?, source_format = ?, duration_sec = ?,
                        source_analyzed = ?, source_metrics_json = ?, file_mtime_utc = ?, is_demo = ?
                    WHERE song_id = ?
                    """,
                    (
                        title,
                        now,
                        now,
                        rel,
                        fmt,
                        duration_sec,
                        1 if analyzed else 0,
                        raw_metrics_json,
                        file_mtime_utc,
                        demo_value if demo_value is not None else 0,
                        song_id,
                    ),
                )
            else:
                existing = conn.execute("SELECT song_id, is_demo FROM songs WHERE source_rel = ?", (rel,)).fetchone()
                if existing:
                    song_id = existing["song_id"]
                    demo_value = existing["is_demo"] if demo_value is None else demo_value
                    conn.execute(
                        """
                        UPDATE songs
                        SET title = ?, updated_at = ?, last_used_at = ?, source_format = ?, duration_sec = ?,
                            source_analyzed = ?, source_metrics_json = ?, file_mtime_utc = ?, is_demo = ?
                        WHERE song_id = ?
                        """,
                        (
                            title,
                            now,
                            now,
                            fmt,
                            duration_sec,
                            1 if analyzed else 0,
                            raw_metrics_json,
                            file_mtime_utc,
                            demo_value if demo_value is not None else existing["is_demo"],
                            song_id,
                        ),
                    )
                else:
                    song_id = song_id or new_song_id()
                    demo_value = 0 if demo_value is None else demo_value
                    conn.execute(
                        """
                        INSERT INTO songs (song_id, title, created_at, updated_at, last_used_at, source_rel,
                                           source_format, duration_sec, source_analyzed, source_metrics_json, file_mtime_utc,
                                           is_demo)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            song_id,
                            title,
                            now,
                            now,
                            now,
                            rel,
                            fmt,
                            duration_sec,
                            1 if analyzed else 0,
                            raw_metrics_json,
                            file_mtime_utc,
                            demo_value,
                        ),
                    )
            conn.execute(
                """
                INSERT INTO song_metrics
                (song_id, duration_sec, lufs_i, lra, true_peak_dbtp, target_i, target_tp, delta_i, tp_margin,
                 crest_factor, dynamic_range, rms_level, peak_level, noise_floor, stereo_corr, width)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(song_id) DO UPDATE SET
                    duration_sec=excluded.duration_sec,
                    lufs_i=excluded.lufs_i,
                    lra=excluded.lra,
                    true_peak_dbtp=excluded.true_peak_dbtp,
                    target_i=excluded.target_i,
                    target_tp=excluded.target_tp,
                    delta_i=excluded.delta_i,
                    tp_margin=excluded.tp_margin,
                    crest_factor=excluded.crest_factor,
                    dynamic_range=excluded.dynamic_range,
                    rms_level=excluded.rms_level,
                    peak_level=excluded.peak_level,
                    noise_floor=excluded.noise_floor,
                    stereo_corr=excluded.stereo_corr,
                    width=excluded.width
                """,
                (
                    song_id,
                    mapped_metrics["duration_sec"],
                    mapped_metrics["lufs_i"],
                    mapped_metrics["lra"],
                    mapped_metrics["true_peak_dbtp"],
                    mapped_metrics["target_i"],
                    mapped_metrics["target_tp"],
                    mapped_metrics["delta_i"],
                    mapped_metrics["tp_margin"],
                    mapped_metrics["crest_factor"],
                    mapped_metrics["dynamic_range"],
                    mapped_metrics["rms_level"],
                    mapped_metrics["peak_level"],
                    mapped_metrics["noise_floor"],
                    mapped_metrics["stereo_corr"],
                    mapped_metrics["width"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
    song = get_song(song_id)
    if not song:
        raise ValueError("song_not_found")
    return song


def create_version_with_renditions(
    song_id: str,
    kind: str,
    label: str,
    title: str,
    summary: dict | None,
    metrics: dict | None,
    renditions: list[dict],
    version_id: str | None = None,
    utility: str | None = None,
) -> dict:
    init_db()
    now = _now_iso()
    summary_clean = _strip_summary_metrics(summary)
    metrics_payload = _merge_summary_metrics(metrics, summary)
    summary_json = "{}"
    raw_metrics_json = "{}"
    voicing = summary_clean.get("voicing") if isinstance(summary_clean, dict) else None
    loudness_profile = summary_clean.get("loudness_profile") if isinstance(summary_clean, dict) else None
    mapped_metrics = _map_metrics(metrics_payload, prefer_output=True)
    version_id = version_id or new_version_id(kind)
    utility_value = (utility or "").strip() or _utility_from_kind(kind)
    with _WRITE_LOCK:
        conn = _connect()
        try:
            exists = conn.execute("SELECT song_id FROM songs WHERE song_id = ?", (song_id,)).fetchone()
            if not exists:
                raise ValueError("song_not_found")
            if not renditions:
                raise ValueError("missing_renditions")
            conn.execute(
                """
                INSERT INTO versions (version_id, song_id, kind, title, label, utility, voicing, loudness_profile,
                                      created_at, updated_at, summary_json, metrics_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (version_id, song_id, kind, title, label, utility_value, voicing, loudness_profile,
                 now, now, summary_json, raw_metrics_json),
            )
            conn.execute(
                """
                INSERT INTO version_metrics
                (version_id, duration_sec, lufs_i, lra, true_peak_dbtp, target_i, target_tp, delta_i, tp_margin,
                 crest_factor, dynamic_range, rms_level, peak_level, noise_floor, stereo_corr, width)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(version_id) DO UPDATE SET
                    duration_sec=excluded.duration_sec,
                    lufs_i=excluded.lufs_i,
                    lra=excluded.lra,
                    true_peak_dbtp=excluded.true_peak_dbtp,
                    target_i=excluded.target_i,
                    target_tp=excluded.target_tp,
                    delta_i=excluded.delta_i,
                    tp_margin=excluded.tp_margin,
                    crest_factor=excluded.crest_factor,
                    dynamic_range=excluded.dynamic_range,
                    rms_level=excluded.rms_level,
                    peak_level=excluded.peak_level,
                    noise_floor=excluded.noise_floor,
                    stereo_corr=excluded.stereo_corr,
                    width=excluded.width
                """,
                (
                    version_id,
                    mapped_metrics["duration_sec"],
                    mapped_metrics["lufs_i"],
                    mapped_metrics["lra"],
                    mapped_metrics["true_peak_dbtp"],
                    mapped_metrics["target_i"],
                    mapped_metrics["target_tp"],
                    mapped_metrics["delta_i"],
                    mapped_metrics["tp_margin"],
                    mapped_metrics["crest_factor"],
                    mapped_metrics["dynamic_range"],
                    mapped_metrics["rms_level"],
                    mapped_metrics["peak_level"],
                    mapped_metrics["noise_floor"],
                    mapped_metrics["stereo_corr"],
                    mapped_metrics["width"],
                ),
            )
            for rendition in renditions:
                fmt = rendition.get("format") or _format_from_rel(rendition.get("rel"))
                rel = rendition.get("rel")
                if not fmt or not rel:
                    continue
                conn.execute(
                    "INSERT INTO renditions (version_id, format, rel) VALUES (?, ?, ?)",
                    (version_id, fmt, rel),
                )
            conn.execute(
                "UPDATE songs SET updated_at = ?, last_used_at = ? WHERE song_id = ?",
                (now, now, song_id),
            )
            conn.commit()
        finally:
            conn.close()
    song = get_song(song_id)
    if not song:
        raise ValueError("song_not_found")
    for version in song.get("versions", []):
        if version.get("version_id") == version_id:
            return version
    raise ValueError("version_not_found")


def add_version(
    song_id: str,
    kind: str,
    label: str,
    title: str,
    summary: dict | None,
    metrics: dict | None,
    renditions: list[dict],
    version_id: str | None = None,
    utility: str | None = None,
) -> dict:
    return create_version_with_renditions(
        song_id,
        kind,
        label,
        title,
        summary,
        metrics,
        renditions,
        version_id=version_id,
        utility=utility,
    )


def delete_song(song_id: str) -> tuple[bool, list[str]]:
    init_db()
    rels: list[str] = []
    with _WRITE_LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT source_rel FROM songs WHERE song_id = ?", (song_id,)).fetchone()
            if not row:
                return False, []
            rels.append(row["source_rel"])
            rendition_rows = conn.execute(
                """
                SELECT rel FROM renditions WHERE version_id IN (
                    SELECT version_id FROM versions WHERE song_id = ?
                )
                """,
                (song_id,),
            ).fetchall()
            rels.extend([r["rel"] for r in rendition_rows])
            conn.execute("DELETE FROM songs WHERE song_id = ?", (song_id,))
            conn.commit()
        finally:
            conn.close()
    return True, rels


def delete_version(song_id: str, version_id: str) -> tuple[bool, list[str]]:
    init_db()
    rels: list[str] = []
    with _WRITE_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT version_id FROM versions WHERE song_id = ? AND version_id = ?",
                (song_id, version_id),
            ).fetchone()
            if not row:
                return False, []
            rendition_rows = conn.execute(
                "SELECT rel FROM renditions WHERE version_id = ?",
                (version_id,),
            ).fetchall()
            rels.extend([r["rel"] for r in rendition_rows])
            conn.execute("DELETE FROM versions WHERE version_id = ?", (version_id,))
            conn.commit()
        finally:
            conn.close()
    return True, rels


def rename_song(song_id: str, title: str) -> bool:
    init_db()
    new_title = _clean_title(title)
    with _WRITE_LOCK:
        conn = _connect()
        try:
            cur = conn.execute("UPDATE songs SET title = ?, updated_at = ? WHERE song_id = ?", (_clean_title(new_title), _now_iso(), song_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def update_last_used(song_id: str) -> None:
    init_db()
    with _WRITE_LOCK:
        conn = _connect()
        try:
            conn.execute("UPDATE songs SET last_used_at = ? WHERE song_id = ?", (_now_iso(), song_id))
            conn.commit()
        finally:
            conn.close()


def version_primary_rendition(version: dict) -> dict | None:
    renditions = version.get("renditions") or []
    return _primary_rendition(renditions)


def _song_dir_from_rel(rel: str) -> Path:
    rel_path = Path((rel or "").lstrip("/"))
    parts = rel_path.parts
    if "songs" in parts:
        idx = parts.index("songs")
        if idx + 1 < len(parts):
            return DATA_ROOT / Path(*parts[: idx + 2])
    return (DATA_ROOT / rel_path).parent.parent


def reconcile_library_fs(max_log_examples: int = 10) -> dict:
    init_db()
    if not SONGS_DIR.exists():
        log_summary("db", "reconcile skipped: SONGS_DIR missing", path=str(SONGS_DIR))
        return {
            "removed_versions": 0,
            "removed_songs": 0,
            "kept_versions": 0,
            "kept_songs": 0,
        }
    removed_versions: list[str] = []
    removed_songs: list[str] = []
    kept_versions = 0
    kept_songs = 0
    examples: list[str] = []
    conn = _connect()
    try:
        song_rows = conn.execute("SELECT song_id, source_rel, is_demo FROM songs").fetchall()
        version_rows = conn.execute("SELECT version_id, song_id FROM versions").fetchall()
        rendition_rows = conn.execute(
            "SELECT r.version_id, v.song_id, r.format, r.rel FROM renditions r "
            "JOIN versions v ON v.version_id = r.version_id"
        ).fetchall()
        log_summary("db", "reconcile start", songs=len(song_rows), versions=len(version_rows))

        renditions_by_version: dict[str, list[dict]] = {}
        for row in rendition_rows:
            renditions_by_version.setdefault(row["version_id"], []).append(
                {
                    "format": (row["format"] or "").lower(),
                    "rel": row["rel"],
                    "song_id": row["song_id"],
                }
            )

        song_missing: set[str] = set()
        for row in song_rows:
            song_id = row["song_id"]
            source_rel = (row["source_rel"] or "").lstrip("/")
            source_path = DATA_ROOT / source_rel
            song_dir = _song_dir_from_rel(source_rel)
            is_demo = bool(row["is_demo"]) if "is_demo" in row.keys() else False
            song_dir_exists = song_dir.exists()
            source_exists = source_path.exists()
            if is_demo and (not song_dir_exists or not source_exists):
                kept_songs += 1
                if len(examples) < max_log_examples:
                    examples.append(
                        f"demo_keep:{song_id} dir={song_dir_exists} source={source_exists}"
                    )
                continue
            if not song_dir_exists or not source_exists:
                removed_songs.append(song_id)
                song_missing.add(song_id)
                if len(examples) < max_log_examples:
                    examples.append(
                        f"song:{song_id} dir={song_dir_exists} source={source_exists}"
                    )
            else:
                kept_songs += 1

        for row in version_rows:
            version_id = row["version_id"]
            song_id = row["song_id"]
            if song_id in song_missing:
                removed_versions.append(version_id)
                continue
            renditions = renditions_by_version.get(version_id, [])
            if not renditions:
                removed_versions.append(version_id)
                if len(examples) < max_log_examples:
                    examples.append(f"version:{version_id}")
                continue
            wav = next((r for r in renditions if r["format"] == "wav"), None)
            if wav:
                wav_path = DATA_ROOT / (wav["rel"] or "").lstrip("/")
                wav_exists = wav_path.exists()
                if not wav_exists:
                    removed_versions.append(version_id)
                    if len(examples) < max_log_examples:
                        examples.append(f"version:{version_id} wav={wav_exists}")
                else:
                    kept_versions += 1
            else:
                removed_versions.append(version_id)
                if len(examples) < max_log_examples:
                    examples.append(f"version:{version_id} wav=missing")

        with _WRITE_LOCK:
            if removed_versions:
                placeholders = ",".join(["?"] * len(removed_versions))
                conn.execute(f"DELETE FROM versions WHERE version_id IN ({placeholders})", removed_versions)
            if removed_songs:
                placeholders = ",".join(["?"] * len(removed_songs))
                conn.execute(f"DELETE FROM songs WHERE song_id IN ({placeholders})", removed_songs)
            conn.commit()
    except Exception as exc:
        log_error("db", "reconcile failed", err=str(exc))
    finally:
        conn.close()

    log_summary(
        "db",
        "reconcile removed",
        removed_songs=len(removed_songs),
        removed_versions=len(removed_versions),
    )
    if examples:
        log_debug("db", "reconcile examples", examples=examples)
    return {
        "removed_versions": len(removed_versions),
        "removed_songs": len(removed_songs),
        "kept_versions": kept_versions,
        "kept_songs": kept_songs,
    }


def sync_library_fs(max_log_examples: int = 10) -> dict:
    init_db()
    ensure_data_roots()
    t0 = time.monotonic()
    errors: list[str] = []
    imported_songs = 0
    imported_versions = 0
    removed_songs = 0
    removed_versions = 0
    imported_from_inbox = 0
    examples: list[str] = []

    try:
        LIBRARY_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        errors.append(f"inbox_dir:{exc!r}")

    for fp in _iter_audio_files(LIBRARY_IMPORT_DIR):
        try:
            song_id = new_song_id()
            dest_dir = song_source_dir(song_id)
            dest_dir.mkdir(parents=True, exist_ok=True)
            safe_name = safe_filename(fp.name) or fp.name
            dest = dest_dir / safe_name
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                idx = 1
                while True:
                    candidate = dest_dir / f"{stem}-{idx}{suffix}"
                    if not candidate.exists():
                        dest = candidate
                        break
                    idx += 1
            fp.replace(dest)
            rel = rel_from_path(dest)
            fmt = dest.suffix.lower().lstrip(".")
            upsert_song_for_source(
                rel,
                dest.stem,
                None,
                fmt,
                {},
                False,
                song_id=song_id,
            )
            imported_from_inbox += 1
            imported_songs += 1
        except Exception as exc:
            errors.append(f"inbox:{fp.name}:{exc!r}")

    fs_songs: dict[str, dict] = {}
    fs_versions: dict[tuple[str, str], list[dict]] = {}
    if SONGS_DIR.exists():
        for song_dir in SONGS_DIR.iterdir():
            if not song_dir.is_dir():
                continue
            song_id = song_dir.name
            source_dir = song_dir / "source"
            source_files = _iter_audio_files(source_dir)
            source_file = _pick_preferred_file(source_files)
            if not source_file:
                continue
            rel = rel_from_path(source_file)
            fs_songs[song_id] = {
                "source_rel": rel,
                "title": _clean_title(source_file.stem),
                "format": source_file.suffix.lower().lstrip("."),
            }
            versions_dir = song_dir / "versions"
            if not versions_dir.exists():
                continue
            for version_dir in versions_dir.iterdir():
                if not version_dir.is_dir():
                    continue
                version_id = version_dir.name
                audio_files = _iter_audio_files(version_dir)
                if not audio_files:
                    continue
                renditions = []
                for af in audio_files:
                    renditions.append({
                        "format": af.suffix.lower().lstrip("."),
                        "rel": rel_from_path(af),
                    })
                fs_versions[(song_id, version_id)] = renditions

    conn = _connect()
    try:
        song_rows = conn.execute("SELECT song_id, source_rel, title FROM songs").fetchall()
        version_rows = conn.execute("SELECT song_id, version_id FROM versions").fetchall()
        rendition_rows = conn.execute("SELECT version_id, format, rel FROM renditions").fetchall()
    except Exception as exc:
        conn.close()
        errors.append(f"db_read:{exc!r}")
        return {
            "imported_songs": imported_songs,
            "imported_versions": imported_versions,
            "removed_songs": removed_songs,
            "removed_versions": removed_versions,
            "imported_from_inbox": imported_from_inbox,
            "errors": errors,
        }

    db_songs = {row["song_id"]: row for row in song_rows}
    db_versions = {(row["song_id"], row["version_id"]) for row in version_rows}
    renditions_map: dict[str, list[dict]] = {}
    for row in rendition_rows:
        renditions_map.setdefault(row["version_id"], []).append(
            {"format": row["format"], "rel": row["rel"]}
        )

    for song_id, fs_entry in fs_songs.items():
        db_row = db_songs.get(song_id)
        title = fs_entry["title"]
        if db_row:
            title = db_row["title"] or title
        else:
            imported_songs += 1
        db_rel = (db_row["source_rel"] if db_row else "") or ""
        if not db_row or db_rel.lstrip("/") != fs_entry["source_rel"].lstrip("/"):
            try:
                upsert_song_for_source(
                    fs_entry["source_rel"],
                    title,
                    None,
                    fs_entry["format"],
                    {},
                    False,
                    song_id=song_id,
                )
            except Exception as exc:
                errors.append(f"song:{song_id}:{exc!r}")

    for (song_id, version_id), renditions in fs_versions.items():
        if (song_id, version_id) not in db_versions:
            song_title = (db_songs.get(song_id, {}).get("title")
                          or fs_songs.get(song_id, {}).get("title")
                          or "Version")
            kind = "master" if "master" in version_id.lower() else "version"
            label = "Master" if kind == "master" else "Version"
            try:
                create_version_with_renditions(
                    song_id,
                    kind,
                    label,
                    song_title,
                    {},
                    {},
                    renditions,
                    version_id=version_id,
                )
                imported_versions += 1
            except Exception as exc:
                errors.append(f"version:{version_id}:{exc!r}")
            continue
        existing = renditions_map.get(version_id, [])
        existing_rels = sorted([r.get("rel") for r in existing if r.get("rel")])
        new_rels = sorted([r.get("rel") for r in renditions if r.get("rel")])
        if existing_rels != new_rels:
            try:
                with _WRITE_LOCK:
                    conn.execute("DELETE FROM renditions WHERE version_id = ?", (version_id,))
                    for rendition in renditions:
                        fmt = rendition.get("format") or _format_from_rel(rendition.get("rel"))
                        rel = rendition.get("rel")
                        if not fmt or not rel:
                            continue
                        conn.execute(
                            "INSERT INTO renditions (version_id, format, rel) VALUES (?, ?, ?)",
                            (version_id, fmt, rel),
                        )
                    conn.execute(
                        "UPDATE versions SET updated_at = ? WHERE version_id = ?",
                        (_now_iso(), version_id),
                    )
                    conn.commit()
            except Exception as exc:
                errors.append(f"renditions:{version_id}:{exc!r}")

    for song_id in db_songs.keys():
        if song_id not in fs_songs:
            removed_songs += 1
            if len(examples) < max_log_examples:
                examples.append(f"song:{song_id}")
    for key in db_versions:
        if key not in fs_versions:
            removed_versions += 1
            if len(examples) < max_log_examples:
                examples.append(f"version:{key[1]}")

    if removed_versions or removed_songs:
        try:
            with _WRITE_LOCK:
                if removed_versions:
                    version_ids = [vid for (_sid, vid) in db_versions if ( _sid, vid) not in fs_versions]
                    placeholders = ",".join(["?"] * len(version_ids))
                    conn.execute(f"DELETE FROM versions WHERE version_id IN ({placeholders})", version_ids)
                if removed_songs:
                    song_ids = [sid for sid in db_songs.keys() if sid not in fs_songs]
                    placeholders = ",".join(["?"] * len(song_ids))
                    conn.execute(f"DELETE FROM songs WHERE song_id IN ({placeholders})", song_ids)
                conn.commit()
        except Exception as exc:
            errors.append(f"delete:{exc!r}")

    conn.close()
    total_ms = (time.monotonic() - t0) * 1000
    log_summary(
        "db",
        "sync result",
        imported_songs=imported_songs,
        imported_versions=imported_versions,
        removed_songs=removed_songs,
        removed_versions=removed_versions,
        imported_from_inbox=imported_from_inbox,
        ms=round(total_ms, 1),
    )
    if examples:
        log_debug("db", "sync examples", examples=examples[:max_log_examples])
    return {
        "imported_songs": imported_songs,
        "imported_versions": imported_versions,
        "removed_songs": removed_songs,
        "removed_versions": removed_versions,
        "imported_from_inbox": imported_from_inbox,
        "errors": errors,
    }
