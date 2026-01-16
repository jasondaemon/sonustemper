import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import LIBRARY_DB, ensure_data_roots, new_song_id, new_version_id
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
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
                conn.execute("PRAGMA busy_timeout = 30000")
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
                    file_mtime_utc TEXT
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


def list_library() -> dict:
    init_db()
    t0 = time.monotonic()
    conn = _connect()
    try:
        t_conn = time.monotonic()
        songs_rows = conn.execute("SELECT * FROM songs ORDER BY created_at DESC").fetchall()
        t_songs = (time.monotonic() - t_conn) * 1000
        if t_songs > 250:
            log_summary("db", "list_library query slow", query="songs", ms=round(t_songs, 1))

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
) -> dict:
    init_db()
    now = _now_iso()
    title = _clean_title(title_hint or Path(rel).stem)
    mapped_metrics = _map_metrics(metrics, prefer_output=False, duration_override=duration_sec)
    raw_metrics_json = "{}"

    with _WRITE_LOCK:
        conn = _connect()
        try:
            existing = conn.execute("SELECT song_id FROM songs WHERE source_rel = ?", (rel,)).fetchone()
            if existing:
                song_id = existing["song_id"]
                conn.execute(
                    """
                    UPDATE songs
                    SET title = ?, updated_at = ?, last_used_at = ?, source_format = ?, duration_sec = ?,
                        source_analyzed = ?, source_metrics_json = ?, file_mtime_utc = ?
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
                        song_id,
                    ),
                )
            else:
                song_id = song_id or new_song_id()
                conn.execute(
                    """
                    INSERT INTO songs (song_id, title, created_at, updated_at, last_used_at, source_rel,
                                       source_format, duration_sec, source_analyzed, source_metrics_json, file_mtime_utc)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
