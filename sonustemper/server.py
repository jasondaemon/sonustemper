import json
import math
import shutil
import subprocess
import shlex
import re
import mimetypes
import threading
import sys
import tempfile
import asyncio
import logging
import time
import hashlib
import uuid
import unicodedata
from collections import deque
from pathlib import Path
from datetime import datetime
import os
import sonustemper.master_pack as mastering_pack
from urllib.parse import quote
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Body, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, Response, HTMLResponse
from sonustemper.tools import bundle_root, is_frozen, resolve_tool
from fastapi.templating import Jinja2Templates
from .tagger import TaggerService
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MASTER_IN_DIR = Path(os.getenv("IN_DIR", os.getenv("MASTER_IN_DIR", str(DATA_DIR / "mastering" / "in"))))
MASTER_OUT_DIR = Path(os.getenv("OUT_DIR", os.getenv("MASTER_OUT_DIR", str(DATA_DIR / "mastering" / "out"))))
MASTER_TMP_DIR = Path(os.getenv("MASTER_TMP_DIR", str(DATA_DIR / "mastering" / "tmp")))
PREVIEW_DIR = Path(os.getenv("PREVIEW_DIR", str(DATA_DIR / "previews")))
PREVIEW_TTL_SEC = int(os.getenv("PREVIEW_TTL_SEC", "600"))
PREVIEW_SESSION_CAP = int(os.getenv("PREVIEW_SESSION_CAP", "5"))
PREVIEW_SEGMENT_START = int(os.getenv("PREVIEW_SEGMENT_START", "30"))
PREVIEW_SEGMENT_DURATION = int(os.getenv("PREVIEW_SEGMENT_DURATION", "12"))
PREVIEW_NORMALIZE_MODE = os.getenv("PREVIEW_NORMALIZE_MODE", "limiter").lower()
PREVIEW_GUARD_MAX_WIDTH = float(os.getenv("PREVIEW_GUARD_MAX_WIDTH", "1.1"))
PREVIEW_SESSION_COOKIE = "st_preview_session"
PREVIEW_BITRATE_KBPS = int(os.getenv("PREVIEW_BITRATE_KBPS", "128"))
PREVIEW_SAMPLE_RATE = int(os.getenv("PREVIEW_SAMPLE_RATE", "44100"))
PRESET_DIR = Path(os.getenv("PRESET_DIR", os.getenv("PRESET_USER_DIR", str(DATA_DIR / "presets" / "user"))))
GEN_PRESET_DIR = Path(os.getenv("GEN_PRESET_DIR", str(DATA_DIR / "presets" / "generated")))
USER_VOICING_DIR = PRESET_DIR / "voicings"
USER_PROFILE_DIR = PRESET_DIR / "profiles"
STAGING_VOICING_DIR = GEN_PRESET_DIR / "voicings"
STAGING_PROFILE_DIR = GEN_PRESET_DIR / "profiles"
TAG_IN_DIR = Path(os.getenv("TAG_IN_DIR", str(DATA_DIR / "tagging" / "in")))
TAG_TMP_DIR = Path(os.getenv("TAG_TMP_DIR", str(DATA_DIR / "tagging" / "tmp")))
ANALYSIS_IN_DIR = Path(os.getenv("ANALYSIS_IN_DIR", str(DATA_DIR / "analysis" / "in")))
ANALYSIS_OUT_DIR = Path(os.getenv("ANALYSIS_OUT_DIR", str(DATA_DIR / "analysis" / "out")))
ANALYSIS_TMP_DIR = Path(os.getenv("ANALYSIS_TMP_DIR", str(DATA_DIR / "analysis" / "tmp")))
NOISE_PREVIEW_DIR = Path(os.getenv("NOISE_PREVIEW_DIR", str(DATA_DIR / "analysis" / "noise_preview")))
NOISE_FILTER_DIR = PRESET_DIR / "noise_filters"
STAGING_NOISE_FILTER_DIR = GEN_PRESET_DIR / "noise_filters"
AI_TOOL_PREVIEW_DIR = Path(os.getenv("AI_TOOL_PREVIEW_DIR", str(DATA_DIR / "analysis" / "ai_preview")))
AI_TOOL_PRESET_DIR = PRESET_DIR / "ai_tools"
# Alias older variable names to new mastering locations for internal use
IN_DIR = MASTER_IN_DIR
OUT_DIR = MASTER_OUT_DIR
APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
UI_APP_DIR = (bundle_root() / "sonustemper-ui" / "app") if is_frozen() else (REPO_ROOT / "sonustemper-ui" / "app")
ASSET_PRESET_DIR = (bundle_root() / "assets" / "presets") if is_frozen() else (REPO_ROOT / "assets" / "presets")
BUILTIN_PROFILE_DIR = ASSET_PRESET_DIR / "profiles"
BUILTIN_VOICING_DIR = ASSET_PRESET_DIR / "voicings"

def _asset_preset_dirs() -> list[Path]:
    candidates = []
    env_dir = (os.getenv("ASSET_PRESET_DIR") or "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(ASSET_PRESET_DIR)
    candidates.append(REPO_ROOT.parent / "assets" / "presets")
    candidates.append(Path.cwd() / "assets" / "presets")
    seen = set()
    roots = []
    for root in candidates:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            roots.append(resolved)
    return roots

def _builtin_profile_dirs() -> list[Path]:
    dirs = []
    for root in _asset_preset_dirs():
        candidate = root / "profiles"
        if candidate.exists():
            dirs.append(candidate)
    return dirs

def _builtin_voicing_dirs() -> list[Path]:
    dirs = []
    for root in _asset_preset_dirs():
        candidate = root / "voicings"
        if candidate.exists():
            dirs.append(candidate)
    return dirs

def _preset_dir(origin: str, kind: str) -> Path:
    origin = (origin or "").strip().lower()
    kind = (kind or "").strip().lower()
    if kind not in {"voicing", "profile"}:
        raise ValueError("invalid kind")
    if origin == "user":
        return USER_VOICING_DIR if kind == "voicing" else USER_PROFILE_DIR
    if origin in {"staging", "generated"}:
        return STAGING_VOICING_DIR if kind == "voicing" else STAGING_PROFILE_DIR
    raise ValueError("invalid origin")

def _preset_dirs_for_origin(origin: str, kind: str | None = None) -> list[tuple[Path, str | None]]:
    origin = (origin or "").strip().lower()
    kind = (kind or "").strip().lower() if kind else None
    roots: list[tuple[Path, str | None]] = []
    if origin == "user":
        if kind in (None, "voicing"):
            roots.append((USER_VOICING_DIR, "voicing"))
        if kind in (None, "profile"):
            roots.append((USER_PROFILE_DIR, "profile"))
        roots.append((PRESET_DIR, None))
    elif origin in {"staging", "generated"}:
        if kind in (None, "voicing"):
            roots.append((STAGING_VOICING_DIR, "voicing"))
        if kind in (None, "profile"):
            roots.append((STAGING_PROFILE_DIR, "profile"))
        roots.append((GEN_PRESET_DIR, None))
    elif origin == "builtin":
        if kind in (None, "profile"):
            for root in _builtin_profile_dirs():
                roots.append((root, "profile"))
        if kind in (None, "voicing"):
            for root in _builtin_voicing_dirs():
                roots.append((root, "voicing"))
    return roots

def _iter_preset_files_by_origin(origin: str, kind: str | None = None):
    for root, default_kind in _preset_dirs_for_origin(origin, kind):
        if not root.exists():
            continue
        for fp in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
            if fp.is_file():
                yield fp, default_kind
UI_TEMPLATES = Jinja2Templates(directory=str(UI_APP_DIR / "templates")) if UI_APP_DIR.exists() else None
if UI_TEMPLATES:
    UI_TEMPLATES.env.globals["static_url"] = lambda path: f"/static/{(path or '').lstrip('/')}"
# Security: API key protection (for CLI/scripts); set API_AUTH_DISABLED=1 to bypass explicitly.
API_KEY = os.getenv("API_KEY")
API_AUTH_DISABLED = os.getenv("API_AUTH_DISABLED") == "1"
API_ALLOW_UNAUTH = os.getenv("API_ALLOW_UNAUTH") == "1"
PROXY_SHARED_SECRET = (os.getenv("PROXY_SHARED_SECRET", "") or "").strip()
# Basic logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("sonustemper")
# Surface configured log level on startup to aid debugging
logger.info(
    "[startup] LOG_LEVEL=%s EVENT_LOG_LEVEL=%s",
    os.getenv("LOG_LEVEL", "error"),
    os.getenv("EVENT_LOG_LEVEL", os.getenv("LOG_LEVEL", "error")),
)
ui_router = None
logger.info("[startup] UI_APP_DIR=%s exists=%s", UI_APP_DIR, UI_APP_DIR.exists())
if UI_APP_DIR.exists():
    sys.path.insert(0, str(UI_APP_DIR))
    try:
        from ui import router as ui_router
        logger.info("[startup] new UI router loaded")
    except Exception as exc:
        logger.exception("[startup] new UI import failed: %s", exc)
# Ensure local modules are importable for master_pack usage.
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
# Trusted proxy check via shared secret (raw)
def is_trusted_proxy(mark: str) -> bool:
    return bool(mark) and bool(PROXY_SHARED_SECRET) and (mark == PROXY_SHARED_SECRET)

def _ui_version_label() -> str:
    ver = (os.getenv("APP_VERSION") or os.getenv("SONUSTEMPER_TAG") or "dev").strip()
    if not ver:
        ver = "dev"
    if ver.lower().startswith("v"):
        return ver
    return f"v{ver}"
# master_pack.py is the unified mastering script (handles single or multiple presets/files).
_default_pack = REPO_ROOT / "sonustemper" / "master_pack.py"
# Use master_pack.py as the unified mastering script (handles single or multiple presets/files)
MASTER_SCRIPT = Path(os.getenv("MASTER_SCRIPT", str(_default_pack)))
app = FastAPI(docs_url=None, redoc_url=None)
for p in [
    MASTER_IN_DIR,
    MASTER_OUT_DIR,
    MASTER_TMP_DIR,
    PREVIEW_DIR,
    TAG_IN_DIR,
    TAG_TMP_DIR,
    PRESET_DIR,
    GEN_PRESET_DIR,
    ANALYSIS_IN_DIR,
    ANALYSIS_OUT_DIR,
    ANALYSIS_TMP_DIR,
]:
    p.mkdir(parents=True, exist_ok=True)
app.mount("/out", StaticFiles(directory=str(MASTER_OUT_DIR), html=True), name="out")
app.mount("/analysis", StaticFiles(directory=str(ANALYSIS_IN_DIR), html=False), name="analysis")
# Mount new UI static assets if present
UI_STATIC_DIR = UI_APP_DIR / "static"
if UI_STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_STATIC_DIR)), name="ui-static")
    app.mount("/ui/static", StaticFiles(directory=str(UI_STATIC_DIR)), name="ui-static-alias")
if ui_router:
    app.include_router(ui_router)
MAIN_LOOP = None
TAGGER_MAX_UPLOAD = int(os.getenv("TAGGER_MAX_UPLOAD_BYTES", str(250 * 1024 * 1024)))
TAGGER_MAX_ARTWORK = int(os.getenv("TAGGER_MAX_ARTWORK_BYTES", str(30 * 1024 * 1024)))
TAGGER = TaggerService(
    MASTER_OUT_DIR,
    TAG_IN_DIR,
    TAG_TMP_DIR,
    max_upload_bytes=TAGGER_MAX_UPLOAD,
    max_artwork_bytes=TAGGER_MAX_ARTWORK,
)

# Preview registry (session-scoped, temp audio)
PREVIEW_REGISTRY: dict[str, dict] = {}
PREVIEW_SESSION_INDEX: dict[str, deque] = {}
PREVIEW_LOCK = threading.Lock()

def _preview_session_key(request: Request) -> str:
    cookie = request.cookies.get(PREVIEW_SESSION_COOKIE)
    if cookie:
        return cookie
    ua = request.headers.get("user-agent", "")
    ip = request.client.host if request.client else ""
    raw = f"{ip}|{ua}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]

def _preview_remove(preview_id: str, entry: dict) -> None:
    path = entry.get("file_path")
    try:
        if path:
            fp = Path(path)
            if PREVIEW_DIR.resolve() in fp.resolve().parents and fp.exists():
                fp.unlink()
    except Exception:
        pass

def _preview_cleanup(session_key: str | None = None) -> None:
    now = time.time()
    expired: list[str] = []
    with PREVIEW_LOCK:
        for pid, entry in list(PREVIEW_REGISTRY.items()):
            created = entry.get("created_at", 0)
            if created and now - created > PREVIEW_TTL_SEC:
                expired.append(pid)
        for pid in expired:
            entry = PREVIEW_REGISTRY.pop(pid, None)
            if not entry:
                continue
            sid = entry.get("session_key")
            if sid and sid in PREVIEW_SESSION_INDEX:
                try:
                    PREVIEW_SESSION_INDEX[sid].remove(pid)
                except ValueError:
                    pass
            _preview_remove(pid, entry)
        if session_key and session_key in PREVIEW_SESSION_INDEX:
            queue = PREVIEW_SESSION_INDEX[session_key]
            while len(queue) > PREVIEW_SESSION_CAP:
                old = queue.popleft()
                entry = PREVIEW_REGISTRY.pop(old, None)
                if entry:
                    _preview_remove(old, entry)

def _preview_update(preview_id: str, status: str, **kwargs) -> None:
    with PREVIEW_LOCK:
        entry = PREVIEW_REGISTRY.get(preview_id)
        if not entry:
            return
        entry["status"] = status
        for key, val in kwargs.items():
            entry[key] = val
        done_event = entry.get("event")
        if status in ("ready", "error") and isinstance(done_event, threading.Event):
            done_event.set()

def _build_preview_filter(voicing: str, strength: int, width: float | None, guardrails: bool) -> str | None:
    if not hasattr(mastering_pack, "_voicing_filters"):
        return None
    try:
        return mastering_pack._voicing_filters(voicing, strength, width, True, guardrails)
    except Exception as exc:
        logger.warning("[preview] voicing filter build failed: %s", exc)
        return None

def _build_preview_filter_from_data(voicing_data: dict, strength: int, width: float | None,
                                    guardrails: bool) -> str | None:
    if not hasattr(mastering_pack, "_voicing_filters_from_json"):
        return None
    try:
        return mastering_pack._voicing_filters_from_json(voicing_data, strength, width, True, guardrails)
    except Exception as exc:
        logger.warning("[preview] voicing data filter build failed: %s", exc)
        return None

def _sanitize_preview_voicing(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    chain = payload.get("chain") if isinstance(payload.get("chain"), dict) else {}
    eq_in = chain.get("eq") if isinstance(chain.get("eq"), list) else []
    dynamics_in = chain.get("dynamics") if isinstance(chain.get("dynamics"), dict) else {}
    stereo_in = chain.get("stereo") if isinstance(chain.get("stereo"), dict) else {}
    allowed_types = {"lowshelf", "highshelf", "peaking", "highpass", "lowpass", "bandpass", "notch"}
    cleaned_eq = []
    for band in eq_in:
        if not isinstance(band, dict):
            continue
        band_type = str(band.get("type") or "").strip().lower()
        if band_type not in allowed_types:
            continue
        try:
            freq = float(band.get("freq_hz") or band.get("freq"))
        except Exception:
            continue
        if freq < 20 or freq > 20000:
            continue
        try:
            gain = float(band.get("gain_db", band.get("gain", 0.0)))
        except Exception:
            continue
        if gain < -6 or gain > 6:
            continue
        try:
            q = float(band.get("q", 1.0))
        except Exception:
            continue
        if q < 0.3 or q > 4.0:
            continue
        cleaned_eq.append({
            "type": band_type,
            "freq_hz": freq,
            "gain_db": gain,
            "q": q,
        })
        if len(cleaned_eq) >= 12:
            break

    cleaned_dynamics = {}
    for key in ("density", "transient_focus", "smoothness"):
        if key not in dynamics_in:
            continue
        try:
            val = float(dynamics_in.get(key))
        except Exception:
            continue
        if val < 0 or val > 1:
            continue
        cleaned_dynamics[key] = val

    cleaned_stereo = {}
    if "width" in stereo_in:
        try:
            width_val = float(stereo_in.get("width"))
        except Exception:
            width_val = None
        if width_val is not None and 0.9 <= width_val <= 1.1:
            cleaned_stereo["width"] = width_val

    cleaned_chain = {}
    if cleaned_eq:
        cleaned_chain["eq"] = cleaned_eq
    if cleaned_dynamics:
        cleaned_chain["dynamics"] = cleaned_dynamics
    if cleaned_stereo:
        cleaned_chain["stereo"] = cleaned_stereo
    return {"chain": cleaned_chain} if cleaned_chain else {"chain": {}}

def _slug_key(s: str) -> str:
    return _safe_slug(str(s or "").lower())

def _preview_find_voicing_path(slug: str) -> Path | None:
    key = _slug_key(slug)
    if not key:
        return None
    roots: list[tuple[Path, str | None]] = []
    roots.extend(_preset_dirs_for_origin("user", "voicing"))
    roots.extend(_preset_dirs_for_origin("staging", "voicing"))
    roots.extend(_preset_dirs_for_origin("builtin", "voicing"))
    for root, default_kind in roots:
        if not root.exists():
            continue
        for fp in root.glob("*.json"):
            if _slug_key(fp.stem) != key:
                continue
            if default_kind and default_kind != "voicing":
                continue
            if not default_kind:
                meta = _preset_meta_from_file(fp)
                effective_kind = (meta.get("kind") or "profile").lower()
                if effective_kind != "voicing":
                    continue
            return fp
    return None

def _render_preview(preview_id: str) -> None:
    with PREVIEW_LOCK:
        entry = PREVIEW_REGISTRY.get(preview_id)
        if not entry:
            return
        input_path = entry.get("input_path")
        voicing = entry.get("voicing") or "universal"
        voicing_data = entry.get("voicing_data")
        strength = int(entry.get("strength") or 0)
        width = entry.get("width")
        guardrails = bool(entry.get("guardrails", False))
        lufs = entry.get("lufs")
        tp = entry.get("tp")
        start_s = entry.get("start_s")
    if not input_path:
        _preview_update(preview_id, "error", error_msg="missing_input")
        return

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PREVIEW_DIR / f"{preview_id}.mp3"
    target_lufs = float(lufs) if isinstance(lufs, (int, float)) else -16.0
    target_tp = float(tp) if isinstance(tp, (int, float)) else -1.0
    if width is not None and guardrails:
        width = min(width, PREVIEW_GUARD_MAX_WIDTH)
    if PREVIEW_NORMALIZE_MODE == "loudnorm":
        limiter = f"loudnorm=I={target_lufs:g}:TP={target_tp:g}:LRA=11"
    else:
        limit_linear = 10 ** (target_tp / 20.0)
        limit_linear = max(0.0625, min(1.0, limit_linear))
        limiter = f"alimiter=limit={limit_linear:.6f}"
    if voicing_data:
        chain = _build_preview_filter_from_data(voicing_data, strength, width, guardrails)
    else:
        chain = _build_preview_filter(voicing, strength, width, guardrails)
    af = f"{chain},{limiter}" if chain else limiter
    seek_start = PREVIEW_SEGMENT_START
    if isinstance(start_s, (int, float)):
        seek_start = max(0.0, float(start_s))
    cmd = [
        FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(seek_start),
        "-t", str(PREVIEW_SEGMENT_DURATION),
        "-i", str(input_path),
        "-af", af,
        "-vn", "-ac", "2", "-ar", str(PREVIEW_SAMPLE_RATE),
        "-codec:a", "libmp3lame", "-b:a", f"{PREVIEW_BITRATE_KBPS}k",
        str(out_path),
    ]
    try:
        logger.debug("[preview] start id=%s voicing=%s strength=%s", preview_id, voicing, strength)
        proc = run_cmd(cmd)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(err or "ffmpeg_failed")
        _preview_update(
            preview_id,
            "ready",
            file_path=str(out_path),
            mime="audio/mpeg",
        )
        logger.debug("[preview] ready id=%s", preview_id)
    except Exception as exc:
        try:
            if out_path.exists():
                out_path.unlink()
        except Exception:
            pass
        _preview_update(preview_id, "error", error_msg=str(exc))
        logger.debug("[preview] error id=%s err=%s", preview_id, exc)

# Utility roots for file manager
UTILITY_ROOTS = {
    ("mastering", "source"): MASTER_IN_DIR,
    ("mastering", "output"): MASTER_OUT_DIR,
    ("tagging", "library"): TAG_IN_DIR,
    ("presets", "user"): PRESET_DIR,
    ("presets", "generated"): GEN_PRESET_DIR,
}
UTILITY_AUDIO_EXTS = {".wav", ".flac", ".aiff", ".aif", ".mp3", ".m4a", ".aac", ".ogg"}
# Small in-memory cache for outlist to reduce disk scans
OUTLIST_CACHE_TTL = int(os.getenv("OUTLIST_CACHE_TTL", "30"))
OUTLIST_CACHE: dict = {}
# Startup debug for security context
logger.info(f"[startup] API_KEY set? {bool(API_KEY)} API_AUTH_DISABLED={API_AUTH_DISABLED} PROXY_SHARED_SECRET set? {bool(PROXY_SHARED_SECRET)}")
if API_KEY or PROXY_SHARED_SECRET:
    logger.info("INFO [auth] API auth enabled.")
elif API_AUTH_DISABLED or API_ALLOW_UNAUTH:
    logger.warning("WARNING [auth] API running unauthenticated (API_ALLOW_UNAUTH=1).")
else:
    logger.warning("WARNING [auth] API running unauthenticated (localhost-only).")
FFMPEG_BIN = resolve_tool("ffmpeg")
FFPROBE_BIN = resolve_tool("ffprobe")
logger.debug("[startup] ffmpeg=%s ffprobe=%s", FFMPEG_BIN, FFPROBE_BIN)
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "2"))
RUNS_IN_FLIGHT = 0

def _start_master_jobs(files, presets, strength, lufs, tp, width, mono_bass, guardrails,
                       stage_analyze, stage_master, stage_loudness, stage_stereo, stage_output,
                       out_wav, out_mp3, mp3_bitrate, mp3_vbr,
                       out_aac, aac_bitrate, aac_codec, aac_container,
                       out_ogg, ogg_quality,
                       out_flac, flac_level, flac_bit_depth, flac_sample_rate,
                       wav_bit_depth, wav_sample_rate,
                       voicing_mode, voicing_name):
    """Kick off mastering jobs and immediately seed the SSE bus so the UI reacts without polling."""
    global RUNS_IN_FLIGHT
    RUNS_IN_FLIGHT = max(0, RUNS_IN_FLIGHT) + 1
    target_loop = getattr(status_bus, "loop", None) or MAIN_LOOP
    run_ids = [Path(f).stem or f for f in files]
    def _is_enabled(val):
        if val is None:
            return False
        if isinstance(val, (int, float)):
            return bool(val)
        txt = str(val).strip().lower()
        return txt not in ("0","false","off","no","")
    def _emit(run_id: str, stage: str, detail: str = "", preset: str | None = None):
        ev = {"stage": stage, "detail": detail, "ts": datetime.utcnow().timestamp()}
        if preset:
            ev["preset"] = preset
        loop_obj = getattr(status_bus, "loop", None) or MAIN_LOOP
        if loop_obj and loop_obj.is_running():
            try:
                asyncio.run_coroutine_threadsafe(status_bus.append_events(run_id, [ev]), loop_obj)
            except Exception:
                pass
    def _mark_direct(run_id: str):
        loop_obj = getattr(status_bus, "loop", None) or MAIN_LOOP
        if loop_obj and loop_obj.is_running():
            try:
                asyncio.run_coroutine_threadsafe(status_bus.mark_direct(run_id), loop_obj)
            except Exception:
                pass
    def _make_event_cb(run_id: str):
        def _cb(event: dict):
            if not isinstance(event, dict):
                return
            _emit(run_id, event.get("stage", ""), event.get("detail", ""), event.get("preset"))
        return _cb
    def run_all():
        for f, rid in zip(files, run_ids):
            do_analyze  = _is_enabled(stage_analyze)
            do_master   = _is_enabled(stage_master)
            do_loudness = _is_enabled(stage_loudness)
            do_stereo   = _is_enabled(stage_stereo)
            do_output   = _is_enabled(stage_output)
        try:
            print(f"[master-bulk] start file={f} presets={presets}", file=sys.stderr)
            _emit(rid, "queued", f)
            _mark_direct(rid)
            mastering_pack.run_master_job(
                f,
                strength=strength,
                presets=presets,
                lufs=lufs if do_loudness else None,
                tp=tp if do_loudness else None,
                width=width if do_stereo else None,
                mono_bass=mono_bass if do_stereo else None,
                guardrails=bool(guardrails) if do_stereo else False,
                no_analyze=not do_analyze,
                no_master=not do_master,
                no_loudness=not do_loudness,
                no_stereo=not do_stereo,
                no_output=not do_output,
                out_wav=out_wav,
                out_mp3=out_mp3,
                mp3_bitrate=mp3_bitrate if mp3_bitrate is not None else 320,
                mp3_vbr=mp3_vbr if mp3_vbr is not None else "none",
                out_aac=out_aac,
                aac_bitrate=aac_bitrate if aac_bitrate is not None else 256,
                aac_codec=aac_codec if aac_codec is not None else "aac",
                aac_container=aac_container if aac_container is not None else "m4a",
                out_ogg=out_ogg,
                ogg_quality=ogg_quality if ogg_quality is not None else 5.0,
                out_flac=out_flac,
                flac_level=flac_level if flac_level is not None else 5,
                flac_bit_depth=flac_bit_depth,
                flac_sample_rate=flac_sample_rate,
                wav_bit_depth=wav_bit_depth if wav_bit_depth is not None else 24,
                wav_sample_rate=wav_sample_rate if wav_sample_rate is not None else 48000,
                voicing_mode=voicing_mode or "presets",
                voicing_name=voicing_name,
                event_cb=_make_event_cb(rid),
            )
            print(f"[master-bulk] done file={f}", file=sys.stderr)
        except Exception as e:
            print(f"[master-bulk] failed file={f}: {e}", file=sys.stderr)
    def _run_wrapper():
        global RUNS_IN_FLIGHT
        try:
            run_all()
        finally:
            # Drop the counter when this batch thread ends
            RUNS_IN_FLIGHT = max(0, RUNS_IN_FLIGHT - 1)

    threading.Thread(target=_run_wrapper, daemon=True).start()
    return run_ids
# --- SSE status stream with in-memory ring buffer + file watcher ---
class StatusBus:
    def __init__(self, ttl_sec: int = 600, max_events: int = 256):
        self.ttl = ttl_sec
        self.max_events = max_events
        self.state = {}  # run_id -> {"events": deque, "waiters": set(queue), "task": task, "cleanup": handle, "last_id": int, "terminal": bool}
        self.lock = asyncio.Lock()
        self.loop = None

    async def _ensure_state(self, run_id: str):
        if run_id in self.state:
            return self.state[run_id]
        self.state[run_id] = {
            "events": deque(maxlen=self.max_events),
            "waiters": set(),
            "task": None,
            "cleanup": None,
            "last_id": 0,
            "terminal": False,
            "direct": False,
        }
        return self.state[run_id]

    async def _schedule_cleanup(self, run_id: str):
        st = self.state.get(run_id)
        if not st:
            return
        if st["cleanup"]:
            st["cleanup"].cancel()
        async def cleanup_task():
            await asyncio.sleep(self.ttl)
            async with self.lock:
                st = self.state.get(run_id)
                if st:
                    if st["task"]:
                        st["task"].cancel()
                    self.state.pop(run_id, None)
        st["cleanup"] = asyncio.create_task(cleanup_task())

    async def append_events(self, run_id: str, events: list[dict]):
        async with self.lock:
            st = await self._ensure_state(run_id)
            for e in events:
                if st["terminal"] and e.get("stage") in ("complete", "error"):
                    continue
                # attach terminal payload (outlist/metrics) when available
                if e.get("stage") in ("complete", "error"):
                    try:
                        payload = outlist(run_id)
                        e = dict(e)
                        e["result"] = payload
                    except Exception:
                        pass
                st["last_id"] += 1
                ev = dict(e)
                ev["_id"] = st["last_id"]
                st["events"].append(ev)
                for q in list(st["waiters"]):
                    await q.put(ev)
                if ev.get("stage") in ("complete", "error"):
                    st["terminal"] = True
            if events and (events[-1].get("stage") in ("complete", "error")):
                await self._schedule_cleanup(run_id)
    async def snapshot(self, run_id: str):
        st = await self._ensure_state(run_id)
        return {
            "events": list(st["events"]),
            "terminal": bool(st.get("terminal")),
            "last_id": st.get("last_id", 0),
        }

    async def subscribe(self, run_id: str, last_event_id: int | None = None):
        st = await self._ensure_state(run_id)
        q = asyncio.Queue()
        async with self.lock:
            st["waiters"].add(q)
            for e in st["events"]:
                if last_event_id is None or e.get("_id", 0) > last_event_id:
                    await q.put(e)
        return q

    async def unsubscribe(self, run_id: str, q: asyncio.Queue):
        async with self.lock:
            st = self.state.get(run_id)
            if st and q in st["waiters"]:
                st["waiters"].remove(q)

    async def ensure_watcher(self, run_id: str):
        st = await self._ensure_state(run_id)
        if st.get("direct"):
            return
        if st["task"]:
            return
        st["task"] = asyncio.create_task(self._watch_file(run_id))

    async def mark_direct(self, run_id: str):
        async with self.lock:
            st = await self._ensure_state(run_id)
            st["direct"] = True
            if st["task"]:
                st["task"].cancel()
                st["task"] = None

    async def _watch_file(self, run_id: str):
        path = OUT_DIR / run_id / ".status.json"
        last_len = 0
        # seed existing
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                existing = data.get("entries") or []
                last_len = len(existing)
                if existing:
                    await self.append_events(run_id, existing)
            except Exception:
                pass
        try:
            while True:
                st = self.state.get(run_id) or {}
                if st.get("terminal"):
                    break
                entries = []
                if path.exists():
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        entries = data.get("entries") or []
                    except Exception:
                        entries = []
                if last_len < len(entries):
                    new_entries = entries[last_len:]
                    last_len = len(entries)
                    await self.append_events(run_id, new_entries)
                    if new_entries and new_entries[-1].get("stage") in ("complete", "error"):
                        break
                if last_len > 0 and not path.exists():
                    break
                await asyncio.sleep(1)
        finally:
            await self._schedule_cleanup(run_id)

status_bus = StatusBus()

async def api_key_guard(request: Request, call_next):
    # Only guard API routes
    if request.url.path.startswith("/api/"):
        if API_AUTH_DISABLED:
            return await call_next(request)
        if not API_KEY and not PROXY_SHARED_SECRET:
            if API_ALLOW_UNAUTH:
                return await call_next(request)
            client_host = request.client.host if request.client else ""
            if client_host in {"127.0.0.1", "::1"}:
                return await call_next(request)
            return JSONResponse(
                {"detail": "API auth not configured. Set API_KEY or PROXY_SHARED_SECRET, or set API_ALLOW_UNAUTH=1 for local dev."},
                status_code=401,
            )
        proxy_mark = request.headers.get("X-SonusTemper-Proxy") or ""
        key = request.headers.get("X-API-Key") or ""
        if PROXY_SHARED_SECRET:
            if proxy_mark and is_trusted_proxy(proxy_mark):
                return await call_next(request)
            if proxy_mark and not is_trusted_proxy(proxy_mark):
                logger.warning(f"[auth] proxy mark mismatch len={len(proxy_mark)} path={request.url.path} mark={repr(proxy_mark)} expected={repr(PROXY_SHARED_SECRET)}")
            if API_KEY and key == API_KEY:
                return await call_next(request)
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if API_KEY:
            if key == API_KEY:
                return await call_next(request)
            print(f"[auth] reject: bad api key from {request.client.host if request.client else 'unknown'} path={request.url.path}", file=sys.stderr)
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)
    return await call_next(request)

app.add_middleware(BaseHTTPMiddleware, dispatch=api_key_guard)

@app.on_event("startup")
async def _capture_loop():
    global MAIN_LOOP
    try:
        MAIN_LOOP = asyncio.get_running_loop()
        status_bus.loop = MAIN_LOOP
    except Exception:
        MAIN_LOOP = None

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    accept = request.headers.get("accept", "")
    if UI_TEMPLATES and "text/html" in accept:
        return UI_TEMPLATES.TemplateResponse(
            "pages/starter.html",
            {
                "request": request,
                "current_page": "",
                "app_version_label": _ui_version_label(),
                "not_found": True,
            },
            status_code=404,
        )
    return JSONResponse({"detail": "not found"}, status_code=404)

@app.get("/api/status-stream")
async def status_stream(song: str, request: Request):
    run_id = song
    await status_bus.ensure_watcher(run_id)
    last_event_id = None
    try:
        lei = request.headers.get("last-event-id")
        if lei:
            last_event_id = int(lei)
    except Exception:
        last_event_id = None
    q = await status_bus.subscribe(run_id, last_event_id)
    async def event_gen():
        try:
            last_keepalive = datetime.utcnow().timestamp()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    e = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"id: {e.get('_id','')}\n"
                    yield f"data: {json.dumps(e)}\n\n"
                    if e.get("stage") in ("complete", "error"):
                        break
                except asyncio.TimeoutError:
                    now = datetime.utcnow().timestamp()
                    if now - last_keepalive > 10:
                        yield ": keepalive\n\n"
                        last_keepalive = now
        finally:
            await status_bus.unsubscribe(run_id, q)
    return StreamingResponse(event_gen(), media_type="text/event-stream")
def read_metrics_for_wav(wav: Path) -> dict | None:
    mp = wav.with_suffix(".metrics.json")
    if not mp.exists():
        return None
def _preset_paths():
    paths = []
    if USER_VOICING_DIR.exists():
        paths.extend(sorted(USER_VOICING_DIR.glob("*.json")))
    if USER_PROFILE_DIR.exists():
        paths.extend(sorted(USER_PROFILE_DIR.glob("*.json")))
    if PRESET_DIR.exists():
        paths.extend(sorted(PRESET_DIR.glob("*.json")))
    if STAGING_VOICING_DIR.exists():
        paths.extend(sorted(STAGING_VOICING_DIR.glob("*.json")))
    if STAGING_PROFILE_DIR.exists():
        paths.extend(sorted(STAGING_PROFILE_DIR.glob("*.json")))
    if GEN_PRESET_DIR.exists():
        paths.extend(sorted(GEN_PRESET_DIR.glob("*.json")))
    for root in _builtin_profile_dirs():
        paths.extend(sorted(root.glob("*.json")))
    for root in _builtin_voicing_dirs():
        paths.extend(sorted(root.glob("*.json")))
    return paths
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "metrics_read_failed"}
def read_metrics_file(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
def read_run_metrics(folder: Path) -> dict | None:
    mp = folder / "metrics.json"
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None
def read_first_wav_metrics(folder: Path) -> dict | None:
    wavs = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".wav"])
    if not wavs:
        return None
    m = read_metrics_for_wav(wavs[0])
    if not m:
        try:
            m = basic_metrics(wavs[0])
        except Exception:
            m = None
    return m
def wrap_metrics(song: str, metrics: dict | None) -> dict | None:
    """Normalize metrics to always have .output/.input keys for UI consumption."""
    if not metrics:
        return None
    if isinstance(metrics, dict) and ("input" in metrics or "output" in metrics):
        return metrics
    # Assume flat metrics (per-wav) -> treat as output-only
    return {
        "version": 1,
        "run_id": song,
        "created_at": None,
        "preset": None,
        "strength": None,
        "overrides": {},
        "input": None,
        "output": metrics,
    }
def _assert_safe_cmd(cmd: list[str]) -> None:
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        raise ValueError("invalid command")
    if any("\x00" in c for c in cmd):
        raise ValueError("invalid null in command")
    exe_name = Path(cmd[0]).name.lower()
    if exe_name not in {"python3", "python", "ffprobe", "ffmpeg"}:
        raise ValueError("unexpected executable")
    if Path(cmd[0]).is_absolute() and exe_name in {"ffprobe", "ffmpeg"}:
        if not Path(cmd[0]).exists():
            raise ValueError("missing executable")

def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    _assert_safe_cmd(cmd)
    # CodeQL [py/command-line-injection]: argv is validated, shell=False, fixed binaries; user input does not control executed program
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

def run_cmd_passthrough(cmd: list[str]) -> None:
    """Run a command streaming stdout/stderr to the container logs."""
    _assert_safe_cmd(cmd)
    # CodeQL [py/command-line-injection]: argv is validated, shell=False, fixed binaries; user input does not control executed program
    res = subprocess.run(cmd, text=True)
    if res.returncode != 0:
        raise subprocess.CalledProcessError(res.returncode, cmd)

def check_output_cmd(cmd: list[str]) -> str:
    _assert_safe_cmd(cmd)
    # CodeQL [py/command-line-injection]: argv is validated, shell=False, fixed binaries; user input does not control executed program
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)

def docker_ffprobe_json(path: Path) -> dict:
    r = run_cmd([
        FFPROBE_BIN, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path)
    ])
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout)
    except Exception:
        try:
            return json.loads(r.stderr)
        except Exception:
            return {}

ANALYZE_ST_HOP_SEC = float(os.getenv("ANALYZE_ST_HOP_SEC", "1.0"))
ANALYZE_SERIES_MAX_POINTS = int(os.getenv("ANALYZE_SERIES_MAX_POINTS", "600"))
ANALYZE_TP_MERGE_SEC = float(os.getenv("ANALYZE_TP_MERGE_SEC", "0.25"))
ANALYZE_TP_MAX_MARKERS = int(os.getenv("ANALYZE_TP_MAX_MARKERS", "500"))

_EBUR_T_RE = re.compile(r"\bt:\s*([0-9\.]+)")
_EBUR_S_RE = re.compile(r"\bS:\s*([\-0-9\.]+)")
_EBUR_TPK_RE = re.compile(r"\bTPK:\s*([\-0-9\.]+)")
_EBUR_PEAK_RE = re.compile(r"\bPeak:\s*([\-0-9\.]+)")
_EBUR_TP_RE = re.compile(r"\bTP:\s*([\-0-9\.]+)")

def _parse_ebur_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    val = raw.strip()
    if not val or "inf" in val.lower():
        return None
    try:
        return float(val)
    except Exception:
        return None

def _duration_seconds(path: Path) -> float | None:
    info = docker_ffprobe_json(path)
    try:
        return float(info.get("format", {}).get("duration"))
    except Exception:
        return None

def _run_ebur128_framelog(path: Path) -> str | None:
    r = run_cmd([
        FFMPEG_BIN, "-hide_banner", "-nostats", "-loglevel", "verbose", "-i", str(path),
        "-filter_complex", "ebur128=peak=true:framelog=verbose", "-f", "null", "-"
    ])
    if r.returncode != 0:
        return None
    return (r.stderr or "") + "\n" + (r.stdout or "")

def _append_tp_marker(markers: list[tuple[float, float]], t: float, value: float) -> None:
    if markers and (t - markers[-1][0]) <= ANALYZE_TP_MERGE_SEC:
        if value > markers[-1][1]:
            markers[-1] = (t, value)
        return
    markers.append((t, value))

def _finalize_tp_markers(markers: list[tuple[float, float]]) -> list[dict]:
    if not markers:
        return []
    if len(markers) > ANALYZE_TP_MAX_MARKERS:
        stride = max(1, math.ceil(len(markers) / ANALYZE_TP_MAX_MARKERS))
        reduced = []
        for i in range(0, len(markers), stride):
            chunk = markers[i : i + stride]
            if not chunk:
                continue
            reduced.append(max(chunk, key=lambda item: item[1]))
        markers = reduced
    out = []
    for t, value in markers:
        out.append({
            "t": round(t, 3),
            "value": round(value, 2),
            "severity": "clip" if value >= 0.0 else "warn",
        })
    return out

def _ebur128_series(path: Path, *, duration_s: float | None, hop_s: float) -> dict | None:
    txt = _run_ebur128_framelog(path)
    if not txt:
        return None
    step = hop_s
    if duration_s and duration_s > 0:
        step = max(step, duration_s / max(1, ANALYZE_SERIES_MAX_POINTS))
    t_vals: list[float] = []
    s_vals: list[float] = []
    tp_vals: list[float | None] = []
    markers: list[tuple[float, float]] = []
    next_t = 0.0
    for raw in txt.splitlines():
        if "t:" not in raw:
            continue
        t_match = _EBUR_T_RE.search(raw)
        if not t_match:
            continue
        t = _parse_ebur_float(t_match.group(1))
        if t is None:
            continue
        if duration_s and t > duration_s + 0.05:
            continue
        s_match = _EBUR_S_RE.search(raw)
        s_val = _parse_ebur_float(s_match.group(1)) if s_match else None
        tp_val = None
        tp_match = _EBUR_TPK_RE.search(raw) or _EBUR_TP_RE.search(raw) or _EBUR_PEAK_RE.search(raw)
        if tp_match:
            tp_val = _parse_ebur_float(tp_match.group(1))
        if tp_val is not None and tp_val > -1.0:
            _append_tp_marker(markers, t, tp_val)
        if s_val is not None and t >= next_t:
            t_vals.append(round(t, 3))
            s_vals.append(s_val)
            tp_vals.append(tp_val)
            next_t += step
    if not t_vals:
        return None
    out = {
        "t": t_vals,
        "lufs": s_vals,
        "markers": markers,
    }
    if any(v is not None for v in tp_vals):
        out["tp"] = tp_vals
    return out

def _analysis_overlay_data(source_path: Path | None, processed_path: Path | None) -> dict:
    source_duration = _duration_seconds(source_path) if source_path else None
    processed_duration = _duration_seconds(processed_path) if processed_path else None
    duration_s = source_duration or processed_duration
    if source_duration and processed_duration:
        duration_s = min(source_duration, processed_duration)
    hop_s = ANALYZE_ST_HOP_SEC
    series: dict = {}
    markers = {"true_peak": {"source": [], "processed": []}}
    if source_path:
        src = _ebur128_series(source_path, duration_s=duration_s, hop_s=hop_s)
        if src:
            series["t"] = src["t"]
            series["lufs_st_source"] = src["lufs"]
            if "tp" in src:
                series["tp_source"] = src["tp"]
            markers["true_peak"]["source"] = _finalize_tp_markers(src.get("markers", []))
    if processed_path:
        proc = _ebur128_series(processed_path, duration_s=duration_s, hop_s=hop_s)
        if proc:
            if "t" not in series:
                series["t"] = proc["t"]
            series["lufs_st_processed"] = proc["lufs"]
            if "tp" in proc:
                series["tp_processed"] = proc["tp"]
            markers["true_peak"]["processed"] = _finalize_tp_markers(proc.get("markers", []))
    if series.get("lufs_st_source") and series.get("lufs_st_processed"):
        min_len = min(
            len(series.get("t", [])),
            len(series["lufs_st_source"]),
            len(series["lufs_st_processed"]),
        )
        if min_len > 0:
            series["t"] = series["t"][:min_len]
            series["lufs_st_source"] = series["lufs_st_source"][:min_len]
            series["lufs_st_processed"] = series["lufs_st_processed"][:min_len]
            if "tp_source" in series:
                series["tp_source"] = series["tp_source"][:min_len]
            if "tp_processed" in series:
                series["tp_processed"] = series["tp_processed"][:min_len]
            deltas = []
            for src_val, proc_val in zip(series["lufs_st_source"], series["lufs_st_processed"]):
                if isinstance(src_val, (int, float)) and isinstance(proc_val, (int, float)):
                    deltas.append(proc_val - src_val)
                else:
                    deltas.append(None)
            series["lufs_st_delta"] = deltas
    payload = {}
    if duration_s is not None:
        payload["duration_s"] = duration_s
    if series:
        payload["series"] = series
    if markers["true_peak"]["source"] or markers["true_peak"]["processed"]:
        payload["markers"] = markers
    return payload

DEMO_SEED_MARKER = DATA_DIR / ".demo_seeded"

def _demo_asset_dir() -> Path:
    if is_frozen():
        return bundle_root() / "assets" / "demo"
    return REPO_ROOT / "assets" / "demo"

def _seed_demo_inputs() -> None:
    if os.getenv("DEMO_SEED_DISABLED") == "1":
        return
    if DEMO_SEED_MARKER.exists():
        return
    try:
        MASTER_IN_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    demo_dir = _demo_asset_dir()
    if not demo_dir.exists():
        return
    copied = 0
    found_existing = False
    for fp in demo_dir.iterdir():
        if not fp.is_file() or fp.suffix.lower() not in ANALYZE_AUDIO_EXTS:
            continue
        dest = MASTER_IN_DIR / fp.name
        if dest.exists():
            found_existing = True
            continue
        try:
            shutil.copy2(fp, dest)
            copied += 1
        except Exception:
            continue
    if copied or found_existing:
        try:
            DEMO_SEED_MARKER.write_text("seeded", encoding="utf-8")
        except Exception:
            pass

app.add_event_handler("startup", _seed_demo_inputs)
def measure_loudness(path: Path) -> dict:
    r = run_cmd([
        FFMPEG_BIN, "-hide_banner", "-nostats", "-i", str(path),
        "-filter_complex", "ebur128=peak=true", "-f", "null", "-"
    ])
    if r.returncode != 0:
        return {}
    txt = (r.stderr or "") + "\n" + (r.stdout or "")
    flags = re.IGNORECASE
    mI   = re.findall(r"\bI:\s*([\-0-9\.]+)\s*LUFS\b", txt, flags)
    mLRA = re.findall(r"\bLRA:\s*([\-0-9\.]+)\s*LU\b", txt, flags)
    mTPK = re.findall(r"\bTPK:\s*([\-0-9\.]+)\s*dBFS\b", txt, flags) or re.findall(r"\bTPK:\s*([\-0-9\.]+)\b", txt, flags)
    mPeak = re.findall(r"\bPeak:\s*([\-0-9\.]+)\s*dBFS\b", txt, flags)
    I   = float(mI[-1]) if mI else None
    LRA = float(mLRA[-1]) if mLRA else None
    TP  = float((mTPK[-1] if mTPK else (mPeak[-1] if mPeak else None))) if (mTPK or mPeak) else None
    return {"I": I, "LRA": LRA, "TP": TP}
def calc_cf_corr(path: Path) -> dict:
    """Extract crest factor and other useful overall stats via ffmpeg astats (mirrors master_pack)."""
    want = "Peak_level+RMS_level+RMS_peak+Noise_floor+Crest_factor"
    r = run_cmd([
        FFMPEG_BIN, "-hide_banner", "-v", "verbose", "-nostats", "-i", str(path),
        "-af", f"astats=measure_overall={want}:measure_perchannel=none:reset=0",
        "-f", "null", "-"
    ])
    txt = (r.stderr or "") + "\n" + (r.stdout or "")
    out = {
        "crest_factor": None,
        "stereo_corr": None,
        "peak_level": None,
        "rms_level": None,
        "dynamic_range": None,
        "noise_floor": None,
    }
    rms_peak = None
    section = None
    for raw in txt.splitlines():
        line = raw.strip()
        if "]" in line and line.startswith("["):
            line = line.split("]", 1)[1].strip()
        if not line:
            continue
        low = line.lower()
        if low == "overall":
            section = "overall"
            continue
        if low.startswith("channel:") or low.startswith("channel "):
            section = "channel"
            continue
        if section != "overall":
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower().replace(" ", "_")
        if k.endswith("_db"):
            k = k[:-3]
        if k == "noise_floor" and v.lower().startswith("-inf"):
            out["noise_floor"] = -120.0
            continue
        m = re.match(r"^([-0-9\\.]+)", v.strip())
        if not m:
            continue
        try:
            num = float(m.group(1))
        except Exception:
            continue
        if k == "rms_peak":
            rms_peak = num
            continue
        if k in ("peak_level","rms_level","dynamic_range","noise_floor","crest_factor"):
            if out.get(k) is None:
                out[k] = num
    if out["dynamic_range"] is None and rms_peak is not None and out["rms_level"] is not None:
        out["dynamic_range"] = rms_peak - out["rms_level"]
    if out["crest_factor"] is None and out["peak_level"] is not None and out["rms_level"] is not None:
        out["crest_factor"] = out["peak_level"] - out["rms_level"]
    return out
def basic_metrics(path: Path) -> dict:
    info = docker_ffprobe_json(path)
    duration = None
    try:
        duration = float(info.get("format", {}).get("duration"))
    except Exception:
        duration = None
    loud = measure_loudness(path)
    cf_corr = calc_cf_corr(path)
    m = {
        "I": loud.get("I"),
        "TP": loud.get("TP"),
        "LRA": loud.get("LRA"),
        "short_term_max": None,
        "crest_factor": cf_corr.get("crest_factor"),
        "stereo_corr": cf_corr.get("stereo_corr"),
        "peak_level": cf_corr.get("peak_level"),
        "rms_level": cf_corr.get("rms_level"),
        "dynamic_range": cf_corr.get("dynamic_range"),
        "noise_floor": cf_corr.get("noise_floor"),
        "duration_sec": duration,
    }
    return m

def analyze_reference(path: Path) -> dict:
    """Extract basic spectral/loudness cues from a reference file to seed a preset."""
    info = docker_ffprobe_json(path)
    duration = None
    try:
        duration = float(info.get("format", {}).get("duration"))
    except Exception:
        duration = None
    loud = measure_loudness(path)
    cf_corr = calc_cf_corr(path)
    return {
        "duration_sec": duration,
        "I": loud.get("I"),
        "TP": loud.get("TP"),
        "LRA": loud.get("LRA"),
        "peak_level": cf_corr.get("peak_level"),
        "rms_level": cf_corr.get("rms_level"),
        "dynamic_range": cf_corr.get("dynamic_range"),
        "noise_floor": cf_corr.get("noise_floor"),
        "crest_factor": cf_corr.get("crest_factor"),
    }

def _build_profile_from_reference(metrics: dict, name: str, source_file: str | None = None) -> dict:
    target_lufs = metrics.get("I")
    if target_lufs is None:
        target_lufs = -14.0
    tp = metrics.get("TP")
    if tp is None:
        tp = -1.0
    return {
        "name": name,
        "lufs": float(target_lufs),
        "tpp": float(tp),
        "category": "Generated",
        "order": 999,
        "meta": {
            "title": name,
            "kind": "profile",
            "source_file": source_file,
            "source": "generated",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        },
    }

def _build_voicing_from_reference(metrics: dict, name: str, source_file: str | None = None) -> dict:
    eq = []
    crest = metrics.get("crest_factor")
    if crest is not None and crest < 10:
        eq.append({"type": "peaking", "freq_hz": 250, "gain_db": -1.5, "q": 1.0})
    eq.append({"type": "highshelf", "freq_hz": 9500, "gain_db": 1.0, "q": 0.8})
    return {
        "id": name,
        "meta": {
            "title": name,
            "kind": "voicing",
            "tags": ["Generated from reference audio."],
            "source_file": source_file,
            "source": "generated",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        },
        "chain": {
            "eq": eq,
            "dynamics": {
                "density": 0.4,
                "transient_focus": 0.5,
                "smoothness": 0.4,
            },
            "stereo": {"width": 1.0},
        },
    }

def _safe_slug(s: str, max_len: int = 64) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] if max_len and len(s) > max_len else s

def _detect_preset_kind(data: dict | None) -> str | None:
    if not isinstance(data, dict):
        return None
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta_kind = meta.get("kind")
    if isinstance(meta_kind, str) and meta_kind.strip():
        return meta_kind.strip().lower()
    keys = set(data.keys())
    profile_hints = {"lufs", "tp", "limiter", "compressor", "loudness", "target_lufs", "target_tp"}
    if keys & profile_hints:
        return "profile"
    if "eq" in keys or "width" in keys or "stereo" in keys:
        return "voicing"
    return None

def _preset_meta_from_file(fp: Path, default_kind: str | None = None) -> dict:
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        kind = _detect_preset_kind(data) or (default_kind.lower() if default_kind else None)
        tags = meta.get("tags")
        if not isinstance(tags, list):
            tags = []
        tags = [_sanitize_label(tag, 60) for tag in tags if tag is not None and str(tag).strip()]
        chain = data.get("chain") if isinstance(data, dict) else None
        stereo = chain.get("stereo") if isinstance(chain, dict) else None
        dynamics = chain.get("dynamics") if isinstance(chain, dict) else None
        width = None
        eq = None
        if isinstance(stereo, dict) and "width" in stereo:
            width = stereo.get("width")
        if isinstance(chain, dict) and isinstance(chain.get("eq"), list):
            eq = chain.get("eq")
        elif isinstance(data.get("eq"), list):
            eq = data.get("eq")
        name = data.get("name")
        voicing_id = data.get("id")
        lufs = data.get("lufs")
        if lufs is None:
            lufs = data.get("target_lufs")
        tp = data.get("tp")
        if tp is None:
            tp = data.get("tpp")
        if tp is None:
            tp = data.get("target_tp")
        if tp is None and isinstance(data.get("limiter"), dict):
            tp = data.get("limiter", {}).get("ceiling")
        category = data.get("category")
        order = data.get("order")
        manual = meta.get("manual")
        if manual is None:
            manual = data.get("manual")
        return {
            "title": _sanitize_label(meta.get("title") or name or voicing_id or fp.stem, 80),
            "name": name,
            "id": voicing_id,
            "source_file": meta.get("source_file"),
            "created_at": meta.get("created_at"),
            "source": meta.get("source"),
            "kind": kind,
            "lufs": lufs,
            "tp": tp,
            "manual": bool(manual) if manual is not None else False,
            "tags": tags,
            "category": category,
            "order": order,
            "width": width,
            "eq": eq,
            "dynamics": dynamics if isinstance(dynamics, dict) else None,
            "stereo": stereo if isinstance(stereo, dict) else None,
        }
    except Exception:
        return {"title": fp.stem, "kind": default_kind.lower() if default_kind else None}

def _library_item_from_file(fp: Path, origin: str, default_kind: str | None = None) -> dict:
    meta = _preset_meta_from_file(fp, default_kind=default_kind)
    effective_kind = (meta.get("kind") or default_kind or "profile").lower()
    if effective_kind == "voicing":
        item_id = meta.get("id") or meta.get("name") or fp.stem
    else:
        item_id = meta.get("name") or meta.get("id") or fp.stem
    if not meta.get("source"):
        meta["source"] = origin if origin != "staging" else "generated"
    return {
        "id": item_id,
        "title": meta.get("title") or item_id,
        "origin": origin,
        "readonly": origin == "builtin",
        "kind": effective_kind,
        "filename": fp.name,
        "meta": meta,
    }

def _library_items(origin: str, kind: str | None = None) -> list[dict]:
    items: list[dict] = []
    for fp, default_kind in _iter_preset_files_by_origin(origin, kind):
        item = _library_item_from_file(fp, origin, default_kind=default_kind)
        if kind and item.get("kind") != kind:
            continue
        items.append(item)
    return items

def _preset_reserved_names_for(kind: str, include_user: bool = True, include_staging: bool = True, include_builtin: bool = True) -> set[str]:
    names: set[str] = set()
    kind = (kind or "").strip().lower()
    if kind not in {"voicing", "profile"}:
        return names
    if include_user:
        for fp, default_kind in _iter_preset_files_by_origin("user", kind):
            meta = _preset_meta_from_file(fp, default_kind=default_kind)
            effective_kind = (meta.get("kind") or default_kind or "profile").lower()
            if effective_kind == kind:
                names.add(fp.stem)
    if include_staging:
        for fp, default_kind in _iter_preset_files_by_origin("staging", kind):
            meta = _preset_meta_from_file(fp, default_kind=default_kind)
            effective_kind = (meta.get("kind") or default_kind or "profile").lower()
            if effective_kind == kind:
                names.add(fp.stem)
    if include_builtin:
        for fp, default_kind in _iter_preset_files_by_origin("builtin", kind):
            meta = _preset_meta_from_file(fp, default_kind=default_kind)
            effective_kind = (meta.get("kind") or default_kind or "profile").lower()
            if effective_kind == kind:
                names.add(fp.stem)
    return names

def _find_preset_file(origin: str, kind: str, preset_id: str) -> Path | None:
    origin = (origin or "").strip().lower()
    kind = (kind or "").strip().lower()
    safe = _safe_slug(preset_id or "")
    if not safe or kind not in {"voicing", "profile"}:
        return None
    for root, default_kind in _preset_dirs_for_origin(origin, kind):
        candidate = root / f"{safe}.json"
        if not candidate.exists():
            continue
        if default_kind and default_kind != kind:
            continue
        if not default_kind:
            meta = _preset_meta_from_file(candidate)
            effective_kind = (meta.get("kind") or "profile").lower()
            if effective_kind != kind:
                continue
        return candidate
    return None

def _sanitize_label(value: str, max_len: int = 80) -> str:
    raw = str(value or "").replace("\u00a0", " ")
    raw = "".join(ch for ch in raw if unicodedata.category(ch)[0] != "C")
    cleaned = re.sub(r"[\r\n\t]+", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].strip()
    return cleaned
def find_input_file(song: str) -> Path | None:
    candidates: list[Path] = []
    try:
        candidates.extend([p for p in IN_DIR.iterdir() if p.is_file() and p.stem == song])
    except Exception:
        pass
    try:
        out_folder = OUT_DIR / song
        candidates.extend([p for p in out_folder.iterdir() if p.is_file() and p.stem == song])
    except Exception:
        pass
    candidates = sorted(candidates)
    return candidates[0] if candidates else None
ANALYZE_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".aif", ".aiff"}
ANALYZE_PREF_ORDER = [".wav", ".flac", ".m4a", ".aac", ".mp3", ".ogg"]
def _list_audio_files(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted(
        [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ANALYZE_AUDIO_EXTS],
        key=lambda p: p.name.lower(),
    )
def _choose_preferred(files: list[Path]) -> Path | None:
    if not files:
        return None
    if len(files) == 1:
        return files[0]
    for ext in ANALYZE_PREF_ORDER:
        for p in files:
            if p.suffix.lower() == ext:
                return p
    return sorted(files, key=lambda p: p.name.lower())[0]
def _resolve_processed_file(folder: Path, out: str | None) -> tuple[Path | None, list[Path]]:
    files = [p for p in _list_audio_files(folder) if not (p.stem == folder.name and "__" not in p.stem)]
    if not files:
        return None, []
    out = (out or "").strip()
    if out:
        for p in files:
            if p.name.lower() == out.lower():
                return p, files
        out_path = Path(out)
        if out_path.suffix:
            candidate = folder / out_path.name
            if candidate.exists():
                return candidate, files
        fmt = out.lower().lstrip(".")
        if fmt in {"wav", "mp3", "m4a", "aac", "flac", "ogg"}:
            for p in files:
                if p.suffix.lower() == f".{fmt}":
                    return p, files
        stem_files = [p for p in files if p.stem == out]
        if stem_files:
            return _choose_preferred(stem_files), files
    return _choose_preferred(files), files
def _available_outputs(song: str, files: list[Path], stem: str) -> list[dict]:
    outputs = [p for p in files if p.stem == stem]
    outputs.sort(
        key=lambda p: ANALYZE_PREF_ORDER.index(p.suffix.lower())
        if p.suffix.lower() in ANALYZE_PREF_ORDER else 99
    )
    items = []
    for p in outputs:
        ext = p.suffix.lower().lstrip(".")
        items.append({
            "label": ext.upper(),
            "format": ext,
            "filename": p.name,
            "url": f"/out/{song}/{p.name}",
        })
    return items
def _load_output_metrics(folder: Path, processed: Path) -> dict | None:
    metrics_path = folder / f"{processed.stem}.metrics.json"
    metrics = read_metrics_file(metrics_path) if metrics_path.exists() else None
    if metrics is None:
        try:
            metrics = basic_metrics(processed)
        except Exception:
            metrics = None
        if metrics:
            try:
                metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            except Exception:
                pass
    return metrics
def fill_input_metrics(song: str, m: dict, folder: Path) -> dict:
    """Ensure input metrics are populated (and deltas) for comparison."""
    if not m:
        return m
    needs_input = True
    if isinstance(m.get("input"), dict):
        keys = ["I", "TP", "LRA", "crest_factor", "peak_level", "rms_level", "dynamic_range", "noise_floor", "duration_sec"]
        needs_input = any(m["input"].get(k) is None for k in keys)
    inp = find_input_file(song)
    if not inp:
        return m
    if needs_input:
        try:
            m["input"] = basic_metrics(inp)
        except Exception:
            return m
    out = m.get("output") or {}
    deltas = {}
    if isinstance(m.get("input"), dict):
        if isinstance(m["input"].get("I"), (int, float)) and isinstance(out.get("I"), (int, float)):
            deltas["I"] = out["I"] - m["input"]["I"]
        if isinstance(m["input"].get("TP"), (int, float)) and isinstance(out.get("TP"), (int, float)):
            deltas["TP"] = out["TP"] - m["input"]["TP"]
        if deltas:
            m["deltas"] = deltas
    # Persist back so future loads are fast
    try:
        (folder / "metrics.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    except Exception:
        pass
    return m
def _writable(dir_path: Path) -> bool:
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dir_path)
        os.close(fd)
        Path(tmp).unlink(missing_ok=True)
        return True
    except Exception:
        return False
def fmt_metrics(m: dict | None) -> str:
    if not m:
        return ""
    if "error" in m:
        return "metrics: (error)"
    I = m.get("I"); TP = m.get("TP"); LRA = m.get("LRA")
    dI = m.get("delta_I")
    margin = m.get("tp_margin")
    w = m.get("width")
    parts = []
    if I is not None:
        parts.append(f"I={I} LUFS" + (f" (Δ {dI:+.1f})" if isinstance(dI,(int,float)) else ""))
    if TP is not None:
        # measured TP is dBFS in our ebur128 output
        parts.append(f"TP={TP} dBFS" + (f" (m {margin:+.1f})" if isinstance(margin,(int,float)) else ""))
    if LRA is not None:
        parts.append(f"LRA={LRA}")
    if isinstance(w,(int,float)) and abs(w-1.0) > 1e-6:
        parts.append(f"W={w:.2f}")
    return " ".join(parts) if parts else "metrics: (unavailable)"
def bust_url(song: str, filename: str) -> str:
    fp = OUT_DIR / song / filename
    try:
        v = int(fp.stat().st_mtime)
    except Exception:
        v = 0
    return f"/out/{song}/{filename}?v={v}"
PRESET_META_FALLBACK = {
    "acoustic": {
        "title": "Acoustic",
        "intent": "Natural, open master that preserves transients and room while gently controlling peaks.",
        "dsp": [
            "Gentle wideband EQ for clarity: subtle low-mid cleanup; airy top lift only if needed.",
            "Light compression (low ratio) to smooth macro-dynamics without ‘pinning’ the mix.",
            "Transient-friendly limiting; avoids aggressive clipping.",
            "Stereo kept natural; avoids over-widening; bass stability prioritized."
        ],
        "bestFor": ["singer-songwriter", "folk", "acoustic pop", "live room recordings"],
        "watchOut": ["If the mix is already bright, extra air can accentuate pick/cymbal edge."],
        "abNotes": ["Listen for: vocal realism, room tone, and transient snap without harshness."]
    },
    "blues_country": {
        "title": "Blues / Country",
        "intent": "Warm, forward midrange with controlled low end and ‘glued’ dynamics.",
        "dsp": [
            "Low-end tightening to keep kick/bass defined without modern hyper-sub emphasis.",
            "Midrange presence shaping for vocal/guitar forwardness (classic ‘radio’ focus).",
            "Bus-style compression ‘glue’ with slower timing to keep groove breathing.",
            "Limiter set for musical level, not maximum loudness."
        ],
        "bestFor": ["blues rock", "country", "americana", "roots"],
        "watchOut": ["Too much glue can soften snare crack if the mix is already compressed."],
        "abNotes": ["Listen for: vocal/guitar forwardness and groove ‘bounce’ staying intact."]
    },
    "clean": {
        "title": "Clean",
        "intent": "Transparent mastering: minimal coloration, just correction + safe loudness.",
        "dsp": [
            "Corrective EQ only (narrow-ish cuts over boosts).",
            "Conservative dynamics: little-to-no saturation; no intentional grit.",
            "Limiter focused on peak safety and translation, not character.",
            "Stereo integrity prioritized; avoids artificial width."
        ],
        "bestFor": ["already-great mixes", "pop", "modern worship", "anything needing transparency"],
        "watchOut": ["May feel ‘too polite’ on aggressive genres unless paired with a character preset."],
        "abNotes": ["Use as a reference: compare others against Clean to hear coloration choices."]
    },
    "foe_acoustic": {
        "title": "FOE – Acoustic",
        "intent": "FOE acoustic identity: cinematic clarity, controlled lows, and slightly enhanced emotional lift.",
        "dsp": [
            "Low-mid contour to reduce boxiness and keep intimacy (voice/guitar separation).",
            "Presence shaping tuned to FOE vocal clarity without harshness.",
            "Slight harmonic enhancement for perceived richness (very subtle saturation).",
            "Limiter set for consistency; preserves transient feel."
        ],
        "bestFor": ["FOE acoustic releases", "hybrid acoustic-rock ballads"],
        "watchOut": ["If the mix has edgy sibilance, presence shaping can expose it—de-ess in mix first."],
        "abNotes": ["Listen for: FOE-style vocal clarity and cinematic lift without sounding hyped."]
    },
    "foe_metal": {
        "title": "FOE – Metal",
        "intent": "FOE metal identity: aggressive but controlled loudness, tight low end, and forward bite without collapse.",
        "dsp": [
            "Sub/low tightening: controls boom; stabilizes palm-mute energy.",
            "Low-mid management to reduce mud under dense guitars.",
            "Presence/attack emphasis (upper mids) to keep riffs articulate.",
            "More assertive limiting (optionally clip-safe), tuned to keep impact.",
            "Stereo discipline: avoids ‘phasey’ width; keeps low end mono-stable."
        ],
        "bestFor": ["FOE metalcore/industrial", "dense guitars", "big drums"],
        "watchOut": ["Can exaggerate harsh cymbals/upper-mids if mix is already hot—tame in mix."],
        "abNotes": ["Listen for: guitar articulation + drum punch staying intact at higher density."]
    },
    "loud": {
        "title": "Loud",
        "intent": "Level-forward master for a denser, hotter option while still respecting true-peak safety.",
        "dsp": [
            "More assertive limiting with careful release to avoid pumping.",
            "Optional mild clipping/soft saturation for density (keep subtle).",
            "Maintains target true-peak ceiling; prioritizes punch over raw LUFS."
        ],
        "bestFor": ["when you want a ‘hotter’ option", "rock/metal/pop if the mix can handle it"],
        "watchOut": ["Will reduce dynamic range; can flatten transients on already-limited mixes."],
        "abNotes": ["Compare against Clean/Modern: does it feel louder without getting smaller?"]
    },
    "modern": {
        "title": "Modern",
        "intent": "Contemporary tonal balance with tighter low end, clean top, and controlled density.",
        "dsp": [
            "Low-end shaping to match modern translation (phones, earbuds, cars).",
            "Slight top clarity lift and low-mid cleanup for ‘hi-fi’ feel.",
            "Moderate bus compression for density without vintage sag.",
            "Limiter tuned for clean loudness, not grit."
        ],
        "bestFor": ["modern pop/rock", "EDM-adjacent mixes", "modern worship"],
        "watchOut": ["Can feel clinical if you wanted vintage warmth—compare with Warm/Blues-Country."],
        "abNotes": ["Listen for: tight low end + clean top without harshness or thinness."]
    },
    "rock": {
        "title": "Rock",
        "intent": "Punch-forward rock option with snare impact, controlled lows, and energetic mids.",
        "dsp": [
            "Low-end tightening + midrange energy to keep guitars/vocals forward.",
            "Bus compression with medium timing to enhance punch + cohesion.",
            "Limiter tuned to keep drum transients alive.",
            "Stereo kept solid; avoids extreme width."
        ],
        "bestFor": ["alt rock", "hard rock", "classic-leaning modern rock"],
        "watchOut": ["If the mix is mid-heavy, may need less mid push; avoid stacking with Warm too strongly."],
        "abNotes": ["Listen for: snare ‘crack’, vocal presence, and guitar bite without fatigue."]
    },
    "warm": {
        "title": "Warm",
        "intent": "Thicker, smoother option: rounds edges, reduces brittleness, and enhances body.",
        "dsp": [
            "Gentle top smoothing (tames brittle highs).",
            "Low-mid/body enhancement (broad strokes; careful).",
            "Soft saturation for warmth and perceived loudness without harshness.",
            "Dynamics tuned to feel relaxed, not aggressively pinned."
        ],
        "bestFor": ["bright mixes", "thin sources", "vintage-leaning material", "acoustic that needs body"],
        "watchOut": ["Can get muddy if the mix already has low-mid buildup—watch 200–400 Hz."],
        "abNotes": ["Compare with Modern/Clean: does it add body without losing clarity?"]
    }
}
VOICING_META = {
    "universal": {
        "title": "Universal",
        "what": ["Balanced tonal tweak; minimal coloration; gentle control."],
        "best": ["general purpose", "first pass reference", "mixed genres"],
        "watch": ["Won't fix heavy mix issues; keep expectations modest."],
        "intensity": ["Low: almost transparent polish", "Med: light sweetening + cohesion", "High: firmer glue and brightness"],
    },
    "airlift": {
        "title": "Airlift",
        "what": ["Opens the top end, adds presence, trims low-mid fog."],
        "best": ["vocals-forward pop", "acoustic clarity", "airy mixes needing lift"],
        "watch": ["Harsh sources can get edgy; tame sibilance upstream."],
        "intensity": ["Low: gentle sheen", "Med: noticeable presence", "High: bright/top-forward—monitor hiss/ess"],
    },
    "ember": {
        "title": "Ember",
        "what": ["Warmth + density; subtle saturation feel."],
        "best": ["thin mixes", "bright guitars", "intimate/acoustic needing body"],
        "watch": ["Can add low-mid weight; mind mud build-up."],
        "intensity": ["Low: mild warmth", "Med: cozy thickness", "High: dense/rounded—watch for cloudiness"],
    },
    "detail": {
        "title": "Detail",
        "what": ["De-muds low-mids, adds articulation without harshness."],
        "best": ["crowded mids", "spoken word", "busy guitars/keys"],
        "watch": ["Overuse can thin body; verify on small speakers."],
        "intensity": ["Low: subtle cleanup", "Med: clear articulation", "High: pronounced clarity—check sibilance"],
    },
    "glue": {
        "title": "Glue",
        "what": ["Cohesion via mild compression and smoothing."],
        "best": ["bus-style cohesion", "live bands", "softening peaks"],
        "watch": ["Too much can dull transients; keep snare crack in mind."],
        "intensity": ["Low: gentle hold", "Med: tighter mix feel", "High: smooth/compact—watch punch"],
    },
    "wide": {
        "title": "Wide",
        "what": ["Subtle spaciousness with mono-aware safety."],
        "best": ["stereo ambience", "pads", "chorus sections needing spread"],
        "watch": ["Low-end remains centered; avoid over-widening critical mono content."],
        "intensity": ["Low: barely wider", "Med: tasteful spread", "High: obvious width—check mono collapse"],
    },
    "cinematic": {
        "title": "Cinematic",
        "what": ["Fuller lows, smooth highs, larger sense of space."],
        "best": ["scores", "ballads", "post-rock", "atmospheric builds"],
        "watch": ["Can add weight; ensure low-end headroom."],
        "intensity": ["Low: gentle size", "Med: big but controlled", "High: expansive—watch pumping/boom"],
    },
    "punch": {
        "title": "Punch",
        "what": ["Tightens lows, emphasizes attack for energy."],
        "best": ["drums", "rock/EDM drops", "rhythmic focus"],
        "watch": ["High settings can feel aggressive; monitor harshness."],
        "intensity": ["Low: subtle focus", "Med: lively punch", "High: aggressive bite—check fatigue"],
    },
}
LOUDNESS_PROFILES = {
    "apple": {
        "title": "Apple Music",
        "targetLUFS": -16,
        "truePeakDBTP": -1.0,
        "notes": ["Streaming normalization oriented; preserves dynamics."],
        "rationale": ["Commonly cited reference level for Apple Music normalization."],
        "typicalUse": ["Apple ecosystem releases"],
        "caution": ["Master for sound first; LUFS is not the goal by itself."]
    },
    "spotify": {
        "title": "Spotify",
        "targetLUFS": -14,
        "truePeakDBTP": -1.0,
        "notes": ["Common normalization reference; keep encoding headroom."],
        "rationale": ["Frequently cited Spotify reference target."],
        "typicalUse": ["General streaming releases"],
        "caution": ["Listener normalization settings can change outcomes."]
    },
    "youtube": {
        "title": "YouTube",
        "targetLUFS": -14,
        "truePeakDBTP": -1.0,
        "notes": ["Normalization typical; aim for clean encode headroom."],
        "rationale": ["Common reference level for YouTube loudness normalization."],
        "typicalUse": ["YouTube uploads / lyric videos"],
        "caution": ["Behavior varies; master for sound first."]
    },
    "custom": {
        "title": "Custom",
        "targetLUFS": None,
        "truePeakDBTP": None,
        "notes": ["Reflects your active override values if enabled."],
        "rationale": ["Shows effective loudness currently applied by the UI."],
        "typicalUse": ["Manual or experimental targeting"],
        "caution": ["Extreme targets can reduce dynamics or cause distortion."]
    }
}
def load_preset_meta() -> dict:
    """Load preset metadata from preset JSON files; fall back to baked-in copy."""
    meta = {}
    files = []
    if PRESET_DIR.exists():
        files.extend(PRESET_DIR.glob("*.json"))
    if GEN_PRESET_DIR.exists():
        files.extend(GEN_PRESET_DIR.glob("*.json"))
    if BUILTIN_PROFILE_DIR.exists():
        files.extend(BUILTIN_PROFILE_DIR.glob("*.json"))
    if BUILTIN_VOICING_DIR.exists():
        files.extend(BUILTIN_VOICING_DIR.glob("*.json"))
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        pid = fp.stem
        entry = {}
        # allow metadata either top-level or nested under "meta"
        src = data.get("meta", {}) if isinstance(data, dict) else {}
        # pull top-level descriptive fields too
        for key in ["intent", "dsp", "bestFor", "watchOut", "abNotes", "title"]:
            if key in data and data.get(key):
                src.setdefault(key, data.get(key))
        entry["title"] = src.get("title") or data.get("name") or pid
        for key in ["intent", "dsp", "bestFor", "watchOut", "abNotes"]:
            if src.get(key) is not None:
                entry[key] = src.get(key)
        if entry:
            meta[pid] = entry
    # Merge with fallback where fields are missing
    for pid, fb in PRESET_META_FALLBACK.items():
        cur = meta.get(pid, {})
        merged = {**fb, **cur}
        meta[pid] = merged
    return meta
BUILD_STAMP = os.getenv("MASTERING_BUILD")
VERSION = os.getenv("APP_VERSION", os.getenv("SONUSTEMPER_TAG", "dev"))
git_rev = None
try:
    git_rev = check_output_cmd(["git", "rev-parse", "--short", "HEAD"]).strip()
except Exception:
    git_rev = os.getenv("GIT_REV")
if BUILD_STAMP:
    BUILD_STAMP = f"{BUILD_STAMP}-{git_rev}" if git_rev else BUILD_STAMP
else:
    if git_rev:
        BUILD_STAMP = f"dev-{git_rev}"
    else:
        try:
            BUILD_STAMP = datetime.fromtimestamp(Path(__file__).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            BUILD_STAMP = "dev"
# --- Utility file manager API ---
def _util_root(utility: str, section: str) -> Path:
    root = UTILITY_ROOTS.get((utility, section))
    if not root:
        raise HTTPException(status_code=400, detail="invalid_utility")
    return root.resolve()

def _safe_rel(root: Path, rel: str) -> Path:
    rel = (rel or "").strip().lstrip("/").replace("\\", "/")
    root_resolved = root.resolve()
    candidate = (root_resolved / rel).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise HTTPException(status_code=400, detail="invalid_path")
    return candidate

def _list_dir_filtered(root: Path, allow_audio: bool = True, allow_json: bool = False, prefix: str = "") -> list[dict]:
    base = _safe_rel(root, prefix) if prefix else root
    items = []
    if not base.exists():
        return items
    for entry in base.iterdir():
        try:
            is_dir = entry.is_dir()
            ext = entry.suffix.lower()
            if is_dir:
                items.append({
                    "name": entry.name,
                    "rel": str(entry.relative_to(root)),
                    "is_dir": True,
                    "size": None,
                    "mtime": entry.stat().st_mtime,
                })
                continue
            ok = False
            if allow_audio and ext in UTILITY_AUDIO_EXTS:
                ok = True
            if allow_json and ext == ".json":
                ok = True
            if not ok:
                continue
            st = entry.stat()
            items.append({
                "name": entry.name,
                "rel": str(entry.relative_to(root)),
                "is_dir": False,
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
        except Exception:
            continue
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return items

@app.get("/api/utility-files")
def list_utility_files(utility: str, section: str, prefix: str = ""):
    root = _util_root(utility, section)
    allow_audio = utility in ("mastering", "tagging")
    allow_json = utility == "presets"
    items = _list_dir_filtered(root, allow_audio=allow_audio, allow_json=allow_json, prefix=prefix)
    return {"items": items}

@app.get("/api/utility-download")
def download_utility_file(utility: str, section: str, rel: str):
    root = _util_root(utility, section)
    target = _safe_rel(root, rel)
    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=404, detail="not_found")
    return FileResponse(target)

@app.post("/api/utility-delete")
async def delete_utility_files(payload: dict):
    utility = payload.get("utility")
    section = payload.get("section")
    rels = payload.get("rels") or []
    if not isinstance(rels, list) or not rels:
        raise HTTPException(status_code=400, detail="no_targets")
    root = _util_root(utility, section)
    deleted = []
    for rel in rels:
        try:
            target = _safe_rel(root, rel)
            if not target.exists() or target.is_dir():
                continue
            parent = target.parent
            stem = target.stem
            if utility == "mastering" and section == "output":
                # delete sidecars sharing the stem in the same dir
                for f in parent.glob(f"{stem}.*"):
                    try:
                        f.unlink()
                        deleted.append(str(f.relative_to(root)))
                    except Exception:
                        pass
                # clean up empty folder
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                except Exception:
                    pass
            else:
                try:
                    target.unlink()
                    deleted.append(str(target.relative_to(root)))
                except Exception:
                    pass
        except HTTPException:
            raise
        except Exception:
            continue
    return {"deleted": deleted}

@app.get("/api/tagger/mp3s")
def tagger_list(scope: str = "all"):
    return {"items": TAGGER.list_mp3s(scope)}

@app.get("/api/tagger/file/{file_id}")
def tagger_get(file_id: str):
    return TAGGER.get_file_payload(file_id)

@app.post("/api/tagger/file/{file_id}")
def tagger_update(file_id: str, body: dict = Body(...)):
    tags = body.get("tags") if isinstance(body, dict) else None
    if tags is None:
        raise HTTPException(status_code=400, detail="missing_tags")
    return TAGGER.update_file_tags(file_id, tags)

@app.post("/api/tagger/import")
async def tagger_import(file: UploadFile = File(...)):
    entry = await TAGGER.import_mp3(file)
    return entry

@app.get("/api/tagger/file/{file_id}/download")
def tagger_download(file_id: str):
    path, filename = TAGGER.download_file(file_id)
    return FileResponse(
        path, media_type="audio/mpeg", filename=filename, content_disposition_type="attachment"
    )

@app.get("/api/tagger/file/{file_id}/artwork")
def tagger_artwork(file_id: str):
    data, mime = TAGGER.get_artwork(file_id)
    return Response(content=data, media_type=mime)

@app.get("/api/tagger/file/{file_id}/artwork-info")
def tagger_artwork_info(file_id: str):
    info = TAGGER.get_artwork_info(file_id)
    return info

@app.post("/api/tagger/artwork")
async def tagger_artwork_upload(file: UploadFile = File(...)):
    return await TAGGER.upload_artwork(file)

@app.post("/api/tagger/album/apply")
def tagger_album_apply(body: dict = Body(...)):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")
    file_ids = body.get("file_ids") or []
    shared = body.get("shared") or {}
    tracks = body.get("tracks") or []
    artwork = body.get("artwork") or {}
    mode = (artwork.get("mode") or "keep").lower()
    upload_id = artwork.get("upload_id")
    if mode not in {"keep", "apply", "clear"}:
        raise HTTPException(status_code=400, detail="invalid_artwork_mode")
    return TAGGER.apply_album(file_ids, shared, tracks, artwork_mode=mode, artwork_upload_id=upload_id)

@app.get("/api/tagger/album/download")
def tagger_album_download(ids: str, name: str = "album", background_tasks: BackgroundTasks = None):
    file_ids = [i for i in (ids or "").split(",") if i]
    zip_path = TAGGER.album_download(file_ids, name)
    safe = (name or "album").strip() or "album"
    safe = re.sub(r"[^A-Za-z0-9 _.-]+", "", safe)[:100]
    def _cleanup(path: Path):
        try:
            path.unlink()
        except Exception:
            pass
    if background_tasks is None:
        background_tasks = BackgroundTasks()
    background_tasks.add_task(_cleanup, zip_path)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{safe}.zip",
        background=background_tasks,
    )

@app.get("/api/files")
def list_files():
    IN_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted([p.name for p in IN_DIR.iterdir()
                    if p.is_file() and p.suffix.lower() in [".wav",".mp3",".flac",".aiff",".aif"]])
    presets = _preset_name_list()
    return {"files": files, "presets": presets}
@app.get("/api/presets")
def presets():
    # Return list of preset names derived from preset files on disk
    return _preset_name_list()
@app.get("/api/recent")
def recent(limit: int = 30):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    folders = [d for d in OUT_DIR.iterdir() if d.is_dir()]
    folders.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    items = []
    for d in folders[:limit]:
        audio_exts = [".mp3",".m4a",".aac",".ogg",".flac",".wav"]
        preview = None
        for ext in audio_exts:
            found = sorted([f.name for f in d.iterdir() if f.is_file() and f.suffix.lower()==ext])
            if found:
                preview = f"/out/{d.name}/{found[0]}"
                if ext == ".mp3":
                    break
        metrics = wrap_metrics(d.name, read_run_metrics(d) or read_first_wav_metrics(d))
        items.append({
            "song": d.name,
            "folder": f"/out/{d.name}/",
            "ab": f"/out/{d.name}/index.html",
            "mp3": preview,  # legacy key; may be other formats
            "metrics": metrics,
        })
    return {"items": items}
@app.delete("/api/song/{song}")
def delete_song(song: str):
    # Safety: only delete direct child folders in OUT_DIR
    target = (OUT_DIR / song).resolve()
    if OUT_DIR.resolve() not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists():
        return {"message": f"Nothing to delete for {song}."}
    shutil.rmtree(target)
    return {"message": f"Deleted outputs for {song}."}
@app.delete("/api/output/{song}/{name}")
def delete_output(song: str, name: str):
    """Delete an individual mastered output (WAV/MP3/metrics) within a run folder."""
    folder = (OUT_DIR / song).resolve()
    if OUT_DIR.resolve() not in folder.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="run_not_found")

    # Only allow deletion of direct children; treat name as stem
    stem = Path(name).stem
    if not stem:
        raise HTTPException(status_code=400, detail="invalid_name")

    removed = []
    candidates = []
    # direct filename targets
    for suffix in [".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".metrics.json", ".run.json"]:
        candidates.append(folder / f"{stem}{suffix}")
    # any file in folder whose stem matches or name startswith stem (safer for minor differences)
    try:
        for f in folder.iterdir():
            if not f.is_file():
                continue
            if f.stem == stem or f.name.startswith(stem):
                candidates.append(f)
    except Exception:
        pass
    seen = set()
    for fp in candidates:
        fp = fp.resolve()
        if fp in seen:
            continue
        seen.add(fp)
        if folder not in fp.parents or fp == folder:
            continue
        if fp.exists():
            fp.unlink()
            removed.append(fp.name)

    if not removed:
        return {"message": f"Nothing to delete for {stem}"}
    return {"message": f"Deleted {', '.join(removed)}"}
@app.delete("/api/upload/{name}")
def delete_upload(name: str):
    target = (IN_DIR / name).resolve()
    if IN_DIR.resolve() not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists():
        return {"message": f"Nothing to delete for {name}."}
    target.unlink()
    return {"message": f"Deleted upload {name}."}
@app.get("/api/outlist")
def outlist(song: str):
    now = time.time()
    cached = OUTLIST_CACHE.get(song)
    if cached and (now - cached["ts"] < OUTLIST_CACHE_TTL):
        return cached["data"]

    folder = OUT_DIR / song
    items: list[dict] = []
    input_m = None

    # Prefer existing metrics; avoid expensive recomputation
    try:
        m_full = wrap_metrics(song, read_run_metrics(folder))
        if not m_full:
            m_full = wrap_metrics(song, read_first_wav_metrics(folder))
        if m_full:
            m_full = fill_input_metrics(song, m_full, folder)
            input_m = m_full.get("input")
    except Exception:
        pass

    if folder.exists() and folder.is_dir():
        audio_exts = {".wav": "WAV", ".mp3": "MP3", ".m4a": "M4A", ".aac": "AAC", ".ogg": "OGG", ".flac": "FLAC"}
        audio_files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in audio_exts]
        stems = sorted(set(p.stem for p in audio_files))
        pref = [".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav"]
        for stem in stems:
            try:
                display_title, badges = TAGGER._parse_badges(stem, "out")
            except Exception:
                display_title, badges = (stem, [])

            links = []
            primary = None
            wav_url = None
            mp3_url = None
            for ext in pref:
                fp = folder / f"{stem}{ext}"
                if not fp.exists():
                    continue
                url = f"/out/{song}/{fp.name}"
                links.append({"label": audio_exts[ext], "url": url, "ext": ext})
                if not primary:
                    primary = url
                if ext == ".wav":
                    wav_url = url
                if ext == ".mp3":
                    mp3_url = url

            m = read_metrics_for_wav(folder / f"{stem}.wav")
            if not m:
                m = read_metrics_file(folder / f"{stem}.metrics.json")

            items.append({
                "name": stem,
                "display_title": display_title,
                "badges": badges,
                "wav": wav_url,
                "mp3": mp3_url,
                "audio": primary,
                "downloads": links,
                "ab": f"/out/{song}/index.html",
                "metrics": fmt_metrics(m),
                "metrics_obj": m,
            })

    resp = {"items": items, "input": input_m}
    OUTLIST_CACHE[song] = {"ts": now, "data": resp}
    return resp

def _preset_reserved_names() -> set[str]:
    names = set()
    for fp in _preset_paths():
        names.add(fp.stem)
    return names

def _unique_preset_name(base: str, reserved: set[str]) -> str:
    base = _safe_slug(base)
    if base and base not in reserved:
        return base
    suffix = "user"
    candidate = f"{base}_{suffix}" if base else suffix
    if candidate not in reserved:
        return candidate
    idx = 2
    while True:
        candidate = f"{base}_{suffix}_{idx}" if base else f"{suffix}_{idx}"
        if candidate not in reserved:
            return candidate
        idx += 1

def _iter_preset_files():
    roots = [root for root, _ in _preset_dirs_for_origin("user")]
    roots.extend([root for root, _ in _preset_dirs_for_origin("staging")])
    roots.extend([root for root, _ in _preset_dirs_for_origin("builtin")])
    for root in roots:
        if not root.exists():
            continue
        for fp in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
            yield fp

def _find_preset_path(name: str) -> Path | None:
    if not name:
        return None
    safe = _safe_slug(name)
    if not safe:
        return None
    roots = [root for root, _ in _preset_dirs_for_origin("user")]
    roots.extend([root for root, _ in _preset_dirs_for_origin("staging")])
    roots.extend([root for root, _ in _preset_dirs_for_origin("builtin")])
    for root in roots:
        candidate = root / f"{safe}.json"
        if candidate.exists():
            return candidate
    return None

def _preset_items(kind: str | None = None, include_staging: bool = True, include_builtin: bool = True, include_user: bool = True) -> list[dict]:
    items: list[dict] = []
    origins: list[str] = []
    if include_user:
        origins.append("user")
    if include_staging:
        origins.append("staging")
    if include_builtin:
        origins.append("builtin")
    for origin in origins:
        for fp, default_kind in _iter_preset_files_by_origin(origin):
            meta = _preset_meta_from_file(fp, default_kind=default_kind)
            effective_kind = (meta.get("kind") or "profile").lower()
            if kind and effective_kind != kind:
                continue
            if effective_kind == "voicing":
                item_name = meta.get("id") or meta.get("name") or fp.stem
            else:
                item_name = meta.get("name") or meta.get("id") or fp.stem
            items.append({
                "name": item_name,
                "filename": fp.name,
                "origin": origin,
                "readonly": origin == "builtin",
                "kind": effective_kind,
                "meta": meta,
            })
    return items

def _preset_name_list() -> list[str]:
    seen = set()
    names = []
    for fp in _iter_preset_files():
        if fp.stem in seen:
            continue
        seen.add(fp.stem)
        names.append(fp.stem)
    return sorted(names)

@app.get("/api/preset/list")
def preset_list():
    return {"items": _preset_items()}

@app.get("/api/voicings")
def voicing_list():
    return {"items": _preset_items("voicing", include_staging=False)}

@app.get("/api/profiles")
def profile_list():
    return {"items": _preset_items("profile", include_staging=False)}

@app.get("/api/library/voicings")
def library_voicings(origin: str = "user"):
    origin = (origin or "user").strip().lower()
    if origin == "generated":
        origin = "staging"
    if origin not in {"user", "staging"}:
        raise HTTPException(status_code=400, detail="invalid_origin")
    return {"items": _library_items(origin, "voicing")}

@app.get("/api/library/profiles")
def library_profiles(origin: str = "user"):
    origin = (origin or "user").strip().lower()
    if origin == "generated":
        origin = "staging"
    if origin not in {"user", "staging"}:
        raise HTTPException(status_code=400, detail="invalid_origin")
    return {"items": _library_items(origin, "profile")}


@app.get("/api/library/noise_filters")
def library_noise_filters(origin: str = "user"):
    origin = (origin or "user").strip().lower()
    if origin == "generated":
        origin = "staging"
    root = _noise_filter_dir(origin)
    items = []
    if root.exists():
        for fp in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
            if not fp.is_file():
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            meta = data.get("meta") if isinstance(data, dict) else {}
            title = _sanitize_label((meta or {}).get("title") or fp.stem, 80)
            item_id = data.get("id") or fp.stem
            items.append({
                "id": item_id,
                "name": item_id,
                "origin": origin,
                "readonly": origin != "user",
                "kind": "noise_filter",
                "meta": {
                    "title": title,
                    "kind": "noise_filter",
                    "tags": (meta or {}).get("tags") or [],
                    "created_at": (meta or {}).get("created_at"),
                },
                "noise": (data or {}).get("noise") if isinstance(data, dict) else None,
            })
    return {"items": items}

@app.get("/api/library/staging")
def library_staging():
    return {"items": _library_items("staging")}

@app.get("/api/library/builtins")
def library_builtins(kind: str | None = None):
    kind = (kind or "").strip().lower() if kind else None
    if kind and kind not in {"voicing", "profile"}:
        raise HTTPException(status_code=400, detail="invalid_kind")
    return {"items": _library_items("builtin", kind)}

@app.get("/api/library/item/download")
def library_item_download(id: str, kind: str, origin: str):
    origin = (origin or "").strip().lower()
    if origin == "generated":
        origin = "staging"
    kind = (kind or "").strip().lower()
    if origin not in {"user", "staging", "builtin"}:
        raise HTTPException(status_code=400, detail="invalid_origin")
    if kind not in {"voicing", "profile"}:
        raise HTTPException(status_code=400, detail="invalid_kind")
    target = _find_preset_file(origin, kind, id)
    if not target:
        raise HTTPException(status_code=404, detail="preset_not_found")
    return FileResponse(str(target), media_type="application/json", filename=target.name)

@app.delete("/api/library/item")
def library_item_delete(payload: dict = Body(...)):
    origin = (payload.get("origin") or "").strip().lower()
    kind = (payload.get("kind") or "").strip().lower()
    preset_id = payload.get("id") or ""
    if origin == "generated":
        origin = "staging"
    if origin == "builtin":
        raise HTTPException(status_code=403, detail="readonly_preset")
    if origin not in {"user", "staging"}:
        raise HTTPException(status_code=400, detail="invalid_origin")
    if kind not in {"voicing", "profile"}:
        raise HTTPException(status_code=400, detail="invalid_kind")
    target = _find_preset_file(origin, kind, preset_id)
    if not target:
        raise HTTPException(status_code=404, detail="preset_not_found")
    target.unlink()
    return {"message": f"Deleted {kind} {preset_id}"}

@app.post("/api/library/duplicate")
def library_duplicate(payload: dict = Body(...)):
    origin = (payload.get("origin") or "").strip().lower()
    kind = (payload.get("kind") or "").strip().lower()
    preset_id = payload.get("id") or ""
    new_name = payload.get("name") or ""
    if origin not in {"builtin", "user"}:
        raise HTTPException(status_code=400, detail="invalid_origin")
    if kind not in {"voicing", "profile"}:
        raise HTTPException(status_code=400, detail="invalid_kind")
    source = _find_preset_file(origin, kind, preset_id)
    if not source:
        raise HTTPException(status_code=404, detail="preset_not_found")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_preset") from exc
    reserved = _preset_reserved_names_for(kind, include_user=True, include_staging=False, include_builtin=True)
    base_name = new_name or preset_id
    safe_name = _unique_preset_name(base_name, reserved)
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta["kind"] = kind
    if new_name:
        meta["title"] = new_name
    elif not meta.get("title"):
        meta["title"] = base_name
    if not meta.get("created_at"):
        meta["created_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    if not meta.get("source"):
        meta["source"] = "builtin" if origin == "builtin" else "user"
    data["meta"] = meta
    if kind == "voicing":
        data["id"] = safe_name
    else:
        data["name"] = safe_name
    dest_dir = _preset_dir("user", kind)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{safe_name}.json"
    dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"item": _library_item_from_file(dest, "user", default_kind=kind)}

@app.post("/api/generate_from_reference")
async def generate_from_reference(
    file: UploadFile = File(...),
    base_name: str = Form(""),
    generate_voicing: str = Form("true"),
    generate_profile: str = Form("true"),
):
    allowed_ext = {".wav", ".mp3", ".flac", ".aiff", ".aif"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed_ext:
        raise HTTPException(status_code=400, detail="unsupported_type")
    contents = await file.read()
    if len(contents) > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="file_too_large")
    wants_voicing = str(generate_voicing).lower() in {"1", "true", "yes", "on"}
    wants_profile = str(generate_profile).lower() in {"1", "true", "yes", "on"}
    if not wants_voicing and not wants_profile:
        raise HTTPException(status_code=400, detail="nothing_to_generate")
    tmpdir = tempfile.mkdtemp(dir=str(DATA_DIR))
    tmp_path = Path(tmpdir) / Path(file.filename).name
    tmp_path.write_bytes(contents)
    created: list[dict] = []
    try:
        display_title = (base_name or "").strip() or Path(file.filename).stem or "Reference"
        display_title = _sanitize_label(display_title, 80) or "Reference"
        base = _safe_slug(display_title) or "reference"
        metrics = analyze_reference(tmp_path)
        if wants_voicing:
            reserved = _preset_reserved_names_for("voicing", include_user=True, include_staging=True, include_builtin=True)
            voicing_name = _unique_preset_name(base, reserved)
            voicing = _build_voicing_from_reference(metrics, voicing_name, file.filename)
            if voicing.get("meta") and display_title:
                voicing["meta"]["title"] = display_title
            voicing_dir = _preset_dir("staging", "voicing")
            voicing_dir.mkdir(parents=True, exist_ok=True)
            voicing_path = voicing_dir / f"{voicing_name}.json"
            voicing_path.write_text(json.dumps(voicing, indent=2), encoding="utf-8")
            created.append(_library_item_from_file(voicing_path, "staging", default_kind="voicing"))
        if wants_profile:
            reserved = _preset_reserved_names_for("profile", include_user=True, include_staging=True, include_builtin=True)
            profile_name = _unique_preset_name(base, reserved)
            profile = _build_profile_from_reference(metrics, profile_name, file.filename)
            if profile.get("meta") and display_title:
                profile["meta"]["title"] = display_title
            profile_dir = _preset_dir("staging", "profile")
            profile_dir.mkdir(parents=True, exist_ok=True)
            profile_path = profile_dir / f"{profile_name}.json"
            profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
            created.append(_library_item_from_file(profile_path, "staging", default_kind="profile"))
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
            Path(tmpdir).rmdir()
        except Exception:
            pass
    return {"items": created}

@app.post("/api/import_json_to_staging")
async def import_json_to_staging(file: UploadFile = File(...), name: str = Form("")):
    suffix = Path(file.filename).suffix.lower()
    if suffix != ".json":
        raise HTTPException(status_code=400, detail="unsupported_type")
    raw = await file.read()
    if len(raw) > 1 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file_too_large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_json") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="invalid_preset")
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    kind = _detect_preset_kind(data) or meta.get("kind") or "profile"
    kind = str(kind).strip().lower()
    if kind not in {"voicing", "profile"}:
        raise HTTPException(status_code=400, detail="invalid_kind")
    override = (name or "").strip()
    if kind == "voicing":
        base = override or data.get("id") or meta.get("title") or data.get("name") or Path(file.filename).stem
    else:
        base = override or data.get("name") or meta.get("title") or data.get("id") or Path(file.filename).stem
    reserved = _preset_reserved_names_for(kind, include_user=True, include_staging=True, include_builtin=True)
    safe_name = _unique_preset_name(base, reserved)
    meta["kind"] = kind
    if override:
        meta["title"] = override
    if not meta.get("title"):
        meta["title"] = base
    if not meta.get("created_at"):
        meta["created_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    meta["source"] = "upload"
    meta["source_file"] = meta.get("source_file") or file.filename
    data["meta"] = meta
    if kind == "voicing":
        data["id"] = safe_name
    else:
        data["name"] = safe_name
    dest_dir = _preset_dir("staging", kind)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{safe_name}.json"
    dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"item": _library_item_from_file(dest, "staging", default_kind=kind)}

@app.post("/api/staging/move_to_user")
def staging_move_to_user(payload: dict = Body(...)):
    kind = (payload.get("kind") or "").strip().lower()
    preset_id = payload.get("id") or ""
    if kind not in {"voicing", "profile"}:
        raise HTTPException(status_code=400, detail="invalid_kind")
    source = _find_preset_file("staging", kind, preset_id)
    if not source:
        raise HTTPException(status_code=404, detail="preset_not_found")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_preset") from exc
    reserved = _preset_reserved_names_for(kind, include_user=True, include_staging=False, include_builtin=True)
    safe_name = _unique_preset_name(preset_id, reserved)
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta["kind"] = kind
    if not meta.get("created_at"):
        meta["created_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    if not meta.get("source"):
        meta["source"] = "generated"
    data["meta"] = meta
    if kind == "voicing":
        data["id"] = safe_name
    else:
        data["name"] = safe_name
    dest_dir = _preset_dir("user", kind)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{safe_name}.json"
    dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    source.unlink()
    return {"item": _library_item_from_file(dest, "user", default_kind=kind)}

@app.post("/api/library/item/update")
def library_item_update(payload: dict = Body(...)):
    origin = (payload.get("origin") or "").strip().lower()
    kind = (payload.get("kind") or "").strip().lower()
    preset_id = payload.get("id") or ""
    fields = payload.get("fields") or {}
    if origin == "generated":
        origin = "staging"
    if origin == "builtin":
        raise HTTPException(status_code=403, detail="readonly_preset")
    if origin not in {"user", "staging"}:
        raise HTTPException(status_code=403, detail="readonly_preset")
    if kind not in {"profile", "voicing"}:
        raise HTTPException(status_code=400, detail="invalid_kind")
    target = _find_preset_file(origin, kind, preset_id)
    if not target:
        raise HTTPException(status_code=404, detail="preset_not_found")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_preset") from exc
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    if kind == "profile":
        title = fields.get("title")
        if title is not None:
            title = _sanitize_label(title, 80)
            if not title:
                raise HTTPException(status_code=400, detail="invalid_title")
            meta["title"] = title
        if "lufs" in fields:
            try:
                lufs = float(fields.get("lufs"))
            except Exception as exc:
                raise HTTPException(status_code=400, detail="invalid_lufs") from exc
            if lufs < -60 or lufs > 0:
                raise HTTPException(status_code=400, detail="invalid_lufs")
            data["lufs"] = lufs
        if "tpp" in fields or "tp" in fields:
            raw_tp = fields.get("tpp") if "tpp" in fields else fields.get("tp")
            try:
                tpp = float(raw_tp)
            except Exception as exc:
                raise HTTPException(status_code=400, detail="invalid_tpp") from exc
            if tpp < -20 or tpp > 2:
                raise HTTPException(status_code=400, detail="invalid_tpp")
            data["tpp"] = tpp
        if "category" in fields:
            category = _sanitize_label(fields.get("category"), 60)
            if category:
                data["category"] = category
            else:
                data.pop("category", None)
        if "order" in fields:
            order = fields.get("order")
            if order is None or order == "":
                data.pop("order", None)
            else:
                try:
                    order_val = int(order)
                except Exception as exc:
                    raise HTTPException(status_code=400, detail="invalid_order") from exc
                if order_val < 0 or order_val > 9999:
                    raise HTTPException(status_code=400, detail="invalid_order")
                data["order"] = order_val
        if "manual" in fields:
            meta["manual"] = bool(fields.get("manual"))
    else:
        chain = data.get("chain") if isinstance(data.get("chain"), dict) else {}
        stereo = chain.get("stereo") if isinstance(chain.get("stereo"), dict) else {}
        dynamics = chain.get("dynamics") if isinstance(chain.get("dynamics"), dict) else {}
        if "title" in fields:
            title = _sanitize_label(fields.get("title"), 80)
            if title:
                meta["title"] = title
        if "tags" in fields:
            raw_tags = fields.get("tags")
            if raw_tags is None:
                meta.pop("tags", None)
            elif isinstance(raw_tags, list):
                cleaned_tags = []
                for tag in raw_tags:
                    cleaned = _sanitize_label(tag, 40)
                    if cleaned:
                        cleaned_tags.append(cleaned)
                if cleaned_tags:
                    meta["tags"] = cleaned_tags[:20]
                else:
                    meta.pop("tags", None)
        if "eq" in fields:
            eq = fields.get("eq")
            if not isinstance(eq, list):
                raise HTTPException(status_code=400, detail="invalid_eq")
            allowed_types = {"lowshelf", "highshelf", "peaking", "highpass", "lowpass", "bandpass", "notch"}
            cleaned = []
            for band in eq:
                if not isinstance(band, dict):
                    raise HTTPException(status_code=400, detail="invalid_eq")
                band_type = str(band.get("type") or "").strip().lower()
                if band_type not in allowed_types:
                    raise HTTPException(status_code=400, detail="invalid_eq_type")
                try:
                    freq = float(band.get("freq_hz"))
                except Exception as exc:
                    raise HTTPException(status_code=400, detail="invalid_eq_freq") from exc
                if freq < 20 or freq > 20000:
                    raise HTTPException(status_code=400, detail="invalid_eq_freq")
                gain_raw = band.get("gain_db", 0.0)
                try:
                    gain = float(gain_raw)
                except Exception as exc:
                    raise HTTPException(status_code=400, detail="invalid_eq_gain") from exc
                if gain < -6 or gain > 6:
                    raise HTTPException(status_code=400, detail="invalid_eq_gain")
                q_raw = band.get("q", 1.0)
                try:
                    q = float(q_raw)
                except Exception as exc:
                    raise HTTPException(status_code=400, detail="invalid_eq_q") from exc
                if q < 0.3 or q > 4.0:
                    raise HTTPException(status_code=400, detail="invalid_eq_q")
                cleaned.append({
                    "type": band_type,
                    "freq_hz": freq,
                    "gain_db": gain,
                    "q": q,
                })
            if cleaned:
                chain["eq"] = cleaned
            else:
                chain.pop("eq", None)
        if "width" in fields:
            width = fields.get("width")
            if width is None or width == "":
                stereo.pop("width", None)
            else:
                try:
                    width_val = float(width)
                except Exception as exc:
                    raise HTTPException(status_code=400, detail="invalid_width") from exc
                if width_val < 0.9 or width_val > 1.1:
                    raise HTTPException(status_code=400, detail="invalid_width")
                stereo["width"] = width_val
        for key, detail in {
            "density": "invalid_density",
            "transient_focus": "invalid_transient",
            "smoothness": "invalid_smoothness",
        }.items():
            if key not in fields:
                continue
            raw = fields.get(key)
            if raw is None or raw == "":
                dynamics.pop(key, None)
                continue
            try:
                val = float(raw)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=detail) from exc
            if val < 0 or val > 1:
                raise HTTPException(status_code=400, detail=detail)
            dynamics[key] = val
        if stereo:
            chain["stereo"] = stereo
        else:
            chain.pop("stereo", None)
        if dynamics:
            chain["dynamics"] = dynamics
        else:
            chain.pop("dynamics", None)
        if chain:
            data["chain"] = chain
    meta["kind"] = kind
    meta["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    data["meta"] = meta
    try:
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail="write_failed") from exc
    return {"item": _library_item_from_file(target, origin, default_kind=kind)}
@app.get("/api/preset/download/{name}")
def preset_download(name: str):
    target = _find_preset_path(name)
    if not target:
        raise HTTPException(status_code=404, detail="preset_not_found")
    return FileResponse(str(target), media_type="application/json", filename=target.name)
@app.delete("/api/preset/{name}")
def preset_delete(name: str):
    target = None
    for root in (PRESET_DIR, GEN_PRESET_DIR):
        for fp in root.glob("*.json"):
            if fp.stem == name:
                target = fp
                break
        if target:
            break
    if not target:
        if _find_preset_path(name):
            raise HTTPException(status_code=403, detail="preset_forbidden")
        raise HTTPException(status_code=404, detail="preset_not_found")
    target.unlink()
    return {"message": f"Deleted preset {name}"}
@app.post("/api/preset/upload")
async def preset_upload(file: UploadFile = File(...)):
    # Accept only JSON presets, size capped to 1MB
    suffix = Path(file.filename).suffix.lower()
    if suffix != ".json":
        raise HTTPException(status_code=400, detail="unsupported_type")
    raw = await file.read()
    if len(raw) > 1 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file_too_large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="invalid_preset")
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    kind = _detect_preset_kind(data) or meta.get("kind") or "profile"
    kind = str(kind).strip().lower()
    if kind not in {"profile", "voicing"}:
        kind = "profile"
    meta["kind"] = kind
    data["meta"] = meta
    # Minimal sanity check
    if kind == "voicing":
        name = data.get("id") or data.get("name") or Path(file.filename).stem
    else:
        name = data.get("name") or data.get("id") or Path(file.filename).stem
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="invalid_name")
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip())
    if not safe_name:
        raise HTTPException(status_code=400, detail="invalid_name")
    reserved = _preset_reserved_names_for(kind, include_user=True, include_staging=True, include_builtin=True)
    safe_name = _unique_preset_name(safe_name, reserved)
    if kind == "voicing":
        data["id"] = safe_name
    else:
        data["name"] = safe_name
    if not meta.get("title"):
        meta["title"] = name.strip() or safe_name
    if not meta.get("created_at"):
        meta["created_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    if not meta.get("source"):
        meta["source"] = "upload"
    data["meta"] = meta
    dest_dir = _preset_dir("user", kind)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{safe_name}.json"
    try:
        dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail="write_failed") from exc
    return {"message": f"Preset uploaded as {dest.name}", "filename": dest.name}
@app.post("/api/preset/generate")
async def preset_generate(file: UploadFile = File(...), kind: str = Form("profile")):
    # Limit to audio extensions already supported
    allowed_ext = {".wav",".mp3",".flac",".aiff",".aif"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed_ext:
        raise HTTPException(status_code=400, detail="unsupported_type")
    # Size cap 100MB
    contents = await file.read()
    if len(contents) > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="file_too_large")
    tmpdir = tempfile.mkdtemp(dir=str(DATA_DIR))
    tmp_path = Path(tmpdir) / Path(file.filename).name
    tmp_path.write_bytes(contents)
    try:
        name_slug = _safe_slug(Path(file.filename).stem)
        kind = (kind or "profile").strip().lower()
        if kind not in {"profile", "voicing"}:
            kind = "profile"
        reserved = _preset_reserved_names_for(kind, include_user=True, include_staging=True, include_builtin=True)
        name_slug = _unique_preset_name(name_slug, reserved)
        target_origin = "user" if PRESET_DIR.exists() and _writable(PRESET_DIR) else "staging"
        target_dir = _preset_dir(target_origin, kind)
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / f"{name_slug}.json"
        metrics = analyze_reference(tmp_path)
        if kind == "voicing":
            preset = _build_voicing_from_reference(metrics, name_slug, file.filename)
        else:
            preset = _build_profile_from_reference(metrics, name_slug, file.filename)
        dest.write_text(json.dumps(preset, indent=2), encoding="utf-8")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
            Path(tmpdir).rmdir()
        except Exception:
            pass
    return {"message": f"Preset created: {name_slug}", "name": name_slug}
@app.get("/favicon.ico")
def favicon():
    # Minimal inline SVG placeholder to avoid 404 spam
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#1f2937"/><text x="32" y="42" font-size="28" text-anchor="middle" fill="#38bdf8" font-family="Arial, sans-serif">S</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")
@app.get("/health")
def health():
    ffmpeg_ok = Path(FFMPEG_BIN).exists() if Path(FFMPEG_BIN).is_absolute() else (shutil.which(FFMPEG_BIN) is not None)
    ffprobe_ok = Path(FFPROBE_BIN).exists() if Path(FFPROBE_BIN).is_absolute() else (shutil.which(FFPROBE_BIN) is not None)
    preset_exists = PRESET_DIR.exists()
    preset_count = len(list(PRESET_DIR.glob("*.json"))) if preset_exists else 0
    in_w = _writable(IN_DIR)
    out_w = _writable(OUT_DIR)
    ok = ffmpeg_ok and ffprobe_ok and preset_exists and preset_count >= 0
    payload = {
        "ffmpeg_ok": ffmpeg_ok,
        "ffprobe_ok": ffprobe_ok,
        "in_dir_writable": in_w,
        "out_dir_writable": out_w,
        "preset_dir_exists": preset_exists,
        "preset_files_count": preset_count,
        "build_stamp": BUILD_STAMP,
        "app": "SonusTemper",
    }
    return JSONResponse(payload, status_code=200 if ok else 503)
@app.get("/api/metrics")
def run_metrics(song: str):
    folder = OUT_DIR / song
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="run_not_found")
    m = wrap_metrics(song, read_run_metrics(folder))
    if not m:
        m = wrap_metrics(song, read_first_wav_metrics(folder))
    if not m:
        raise HTTPException(status_code=404, detail="metrics_not_found")
    m = fill_input_metrics(song, m, folder)
    return m
def _analysis_path_roots() -> list[tuple[str, Path]]:
    return [
        ("in", IN_DIR),
        ("analysis", ANALYSIS_IN_DIR),
        ("out", OUT_DIR),
        ("analysis_out", ANALYSIS_OUT_DIR),
    ]

def _resolve_analysis_path(rel: str) -> Path:
    rel = (rel or "").strip().lstrip("/").replace("\\", "/")
    if not rel:
        raise HTTPException(status_code=400, detail="missing_path")
    parts = rel.split("/", 1)
    prefix_map = {key: root for key, root in _analysis_path_roots()}
    if parts[0] in prefix_map:
        root = prefix_map[parts[0]]
        sub = parts[1] if len(parts) > 1 else ""
        candidate = _safe_rel(root, sub)
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="file_not_found")
        return candidate
    for _key, root in _analysis_path_roots():
        try:
            candidate = _safe_rel(root, rel)
        except HTTPException:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail="file_not_found")

def _analysis_rel_for_path(path: Path) -> str | None:
    for key, root in _analysis_path_roots():
        try:
            rel = path.relative_to(root)
        except Exception:
            continue
        return f"{key}/{rel.as_posix()}"
    return None

def _noise_filter_dir(origin: str) -> Path:
    origin = (origin or "user").strip().lower()
    if origin in {"staging", "generated"}:
        return STAGING_NOISE_FILTER_DIR
    if origin == "user":
        return NOISE_FILTER_DIR
    raise HTTPException(status_code=400, detail="invalid_origin")

def _noise_filter_chain(payload: dict) -> str:
    mode = (payload.get("mode") or "remove").strip().lower()
    f_low = payload.get("f_low")
    f_high = payload.get("f_high")
    if not isinstance(f_low, (int, float)) or not isinstance(f_high, (int, float)):
        raise HTTPException(status_code=400, detail="missing_band")
    f_low = max(20.0, min(20000.0, float(f_low)))
    f_high = max(20.0, min(20000.0, float(f_high)))
    if f_high <= f_low:
        raise HTTPException(status_code=400, detail="invalid_band")
    filters: list[str] = []
    if mode == "solo":
        filters.append(f"highpass=f={f_low:g}")
        filters.append(f"lowpass=f={f_high:g}")
    else:
        center = (f_low + f_high) * 0.5
        bandwidth = max(f_high - f_low, 1.0)
        q_val = center / bandwidth
        q_val = max(0.3, min(10.0, q_val))
        depth = payload.get("band_depth_db")
        if not isinstance(depth, (int, float)):
            depth = -18.0
        depth = -abs(float(depth))
        filters.append(f"equalizer=f={center:g}:t=q:w={q_val:.3f}:g={depth:.1f}")
    hp = payload.get("hp_hz")
    if isinstance(hp, (int, float)) and hp > 0:
        hp = max(20.0, min(20000.0, float(hp)))
        filters.append(f"highpass=f={hp:g}")
    lp = payload.get("lp_hz")
    if isinstance(lp, (int, float)) and lp > 0:
        lp = max(20.0, min(20000.0, float(lp)))
        filters.append(f"lowpass=f={lp:g}")
    strength = payload.get("afftdn_strength")
    if isinstance(strength, (int, float)):
        strength = max(0.0, min(1.0, float(strength)))
        if strength > 0.01:
            nr = 24.0 * strength
            filters.append(f"afftdn=nr={nr:.2f}:nf=-25")
    return ",".join(filters)

def _unique_output_path(directory: Path, stem: str, suffix: str) -> Path:
    safe_stem = _safe_slug(stem) or "cleaned"
    candidate = directory / f"{safe_stem}{suffix}"
    if not candidate.exists():
        return candidate
    idx = 1
    while True:
        candidate = directory / f"{safe_stem}-{idx}{suffix}"
        if not candidate.exists():
            return candidate
        idx += 1

AI_TOOL_IDS = {
    "ai_deglass",
    "ai_vocal_smooth",
    "ai_bass_tight",
    "ai_transient_soften",
    "ai_platform_safe",
}

def _ai_tool_strength(raw: object, default: int = 30) -> int:
    try:
        value = int(float(raw))
    except Exception:
        return default
    return max(0, min(100, value))

def _ai_opt_bool(opts: dict, key: str, default: bool = False) -> bool:
    raw = opts.get(key)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return default

def _ai_opt_float(opts: dict, key: str, default: float | None = None) -> float | None:
    raw = opts.get(key)
    if isinstance(raw, (int, float)):
        return float(raw)
    return default

def _ai_tool_filter_chain(tool_id: str, strength: int, options: dict | None) -> str:
    tool = (tool_id or "").strip().lower()
    if tool in {"original", "none", ""}:
        return ""
    if tool not in AI_TOOL_IDS:
        raise HTTPException(status_code=400, detail="invalid_tool")
    opts = options if isinstance(options, dict) else {}
    filters: list[str] = []

    if tool == "ai_deglass":
        lp_enabled = _ai_opt_bool(opts, "lp_enabled", True)
        preserve_air = _ai_opt_bool(opts, "preserve_air", False)
        lp_hz = _ai_opt_float(opts, "lp_hz")
        if lp_hz is None:
            lp_hz = 18000.0 - (20.0 * strength)
        lp_hz = max(14000.0, min(20000.0, float(lp_hz)))
        if lp_enabled:
            filters.append(f"lowpass=f={lp_hz:.0f}")
        shelf_freq = _ai_opt_float(opts, "shelf_hz", 11000.0) or 11000.0
        shelf_freq = max(6000.0, min(18000.0, shelf_freq))
        cut_db = -0.5 - (3.5 * (strength / 100.0))
        if preserve_air:
            cut_db *= 0.6
        filters.append(f"treble=g={cut_db:.2f}:f={shelf_freq:.0f}:w=0.7")
        afftdn = _ai_opt_float(opts, "afftdn_strength")
        if afftdn is None:
            afftdn = min(0.6, 0.1 + (strength / 100.0) * 0.5)
        afftdn = max(0.0, min(1.0, afftdn))
        if afftdn > 0.01:
            nr = 24.0 * afftdn
            filters.append(f"afftdn=nr={nr:.2f}:nf=-25")

    elif tool == "ai_vocal_smooth":
        center = _ai_opt_float(opts, "center_hz", 4500.0) or 4500.0
        center = max(2500.0, min(8000.0, center))
        main_gain = -0.5 - (2.5 * (strength / 100.0))
        filters.append(f"equalizer=f={center:.0f}:t=q:w=1.2:g={main_gain:.2f}")
        s_cut = _ai_opt_bool(opts, "s_cut", False)
        if s_cut:
            s_freq = _ai_opt_float(opts, "s_hz", 7500.0) or 7500.0
            s_freq = max(5500.0, min(11000.0, s_freq))
            s_gain = -0.5 - (1.5 * (strength / 100.0))
            filters.append(f"equalizer=f={s_freq:.0f}:t=q:w=2.0:g={s_gain:.2f}")
        afftdn = _ai_opt_float(opts, "afftdn_strength", 0.0) or 0.0
        afftdn = max(0.0, min(1.0, afftdn))
        if afftdn > 0.01:
            nr = 20.0 * afftdn
            filters.append(f"afftdn=nr={nr:.2f}:nf=-30")

    elif tool == "ai_bass_tight":
        hp_hz = _ai_opt_float(opts, "hp_hz")
        if hp_hz is None:
            hp_hz = 30.0 + (20.0 * (strength / 100.0))
        hp_hz = max(20.0, min(80.0, hp_hz))
        filters.append(f"highpass=f={hp_hz:.0f}")
        mud_freq = _ai_opt_float(opts, "mud_hz", 220.0) or 220.0
        mud_freq = max(120.0, min(400.0, mud_freq))
        mud_gain = -0.5 - (2.0 * (strength / 100.0))
        filters.append(f"equalizer=f={mud_freq:.0f}:t=q:w=1.0:g={mud_gain:.2f}")
        punch = _ai_opt_bool(opts, "punch", False)
        if punch:
            punch_gain = 0.5 + max(0.0, (45.0 - strength) / 45.0) * 1.0
            punch_gain = max(0.5, min(1.5, punch_gain))
            filters.append(f"bass=g={punch_gain:.2f}:f=90:w=0.7")

    elif tool == "ai_transient_soften":
        keep_punch = _ai_opt_bool(opts, "keep_punch", False)
        presence_gain = -0.3 - (1.2 * (strength / 100.0))
        shelf_gain = -0.3 - (1.5 * (strength / 100.0))
        filters.append(f"equalizer=f=3200:t=q:w=1.0:g={presence_gain:.2f}")
        filters.append(f"treble=g={shelf_gain:.2f}:f=8000:w=0.7")
        if not keep_punch:
            ratio = 1.4 + (1.6 * (strength / 100.0))
            threshold = -18.0 - (6.0 * (strength / 100.0))
            filters.append(f"acompressor=threshold={threshold:.1f}dB:ratio={ratio:.2f}:attack=20:release=250:makeup=0")

    elif tool == "ai_platform_safe":
        preset = (opts.get("preset") or "streaming").strip().lower()
        if preset in {"dynamic", "dynamic_preserve", "preserve"}:
            target_i = -16.0
            tp = -1.2
        elif preset in {"youtube", "yt"}:
            target_i = -14.0
            tp = -1.0
        else:
            target_i = -14.0
            tp = -1.2
        tp = max(-2.0, tp - (0.4 * (strength / 100.0)))
        filters.append(f"loudnorm=I={target_i:.1f}:TP={tp:.1f}:LRA=11")
        limit = math.pow(10.0, tp / 20.0)
        limit = max(0.0625, min(1.0, limit))
        filters.append(f"alimiter=limit={limit:.3f}")

    return ",".join(filters)

def _ai_db_ratio(band_db: float | None, full_db: float | None) -> float | None:
    if band_db is None or full_db is None:
        return None
    if not math.isfinite(band_db) or not math.isfinite(full_db):
        return None
    return math.pow(10.0, (band_db - full_db) / 20.0)

def _ai_severity_from_ratio(ratio: float | None, low: float, high: float) -> float:
    if ratio is None:
        return 0.0
    if ratio <= low:
        return 0.0
    if ratio >= high:
        return 1.0
    return (ratio - low) / max(0.0001, (high - low))

def _ai_confidence(severity: float) -> str:
    if severity >= 0.75:
        return "high"
    if severity >= 0.45:
        return "med"
    return "low"

def _ai_astats_segment(path: Path, start: float, duration: float, pre_filters: list[str] | None = None) -> dict:
    want = "Peak_level+RMS_level+RMS_peak+Number_of_clipped_samples+Number_of_samples"
    filters = list(pre_filters or [])
    filters.append(f"astats=measure_overall={want}:measure_perchannel=none:reset=0")
    filt = ",".join(filters)
    cmd = [
        FFMPEG_BIN, "-hide_banner", "-v", "verbose", "-nostats",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(path),
        "-af", filt,
        "-f", "null", "-",
    ]
    r = run_cmd(cmd)
    if r.returncode != 0:
        return {}
    txt = (r.stderr or "") + "\n" + (r.stdout or "")
    out = {
        "peak_level": None,
        "rms_level": None,
        "rms_peak": None,
        "clipped_samples": 0,
        "samples": None,
    }
    section = None
    for raw in txt.splitlines():
        line = raw.strip()
        if "]" in line and line.startswith("["):
            line = line.split("]", 1)[1].strip()
        if not line:
            continue
        low = line.lower()
        if low == "overall":
            section = "overall"
            continue
        if low.startswith("channel:") or low.startswith("channel "):
            section = "channel"
            continue
        if section != "overall":
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip().lower().replace(" ", "_")
        if key.endswith("_db"):
            key = key[:-3]
        m = re.match(r"^([-0-9\\.]+)", v.strip())
        if not m:
            continue
        try:
            num = float(m.group(1))
        except Exception:
            continue
        if key == "rms_peak":
            out["rms_peak"] = num
            continue
        if key in ("peak_level", "rms_level") and out.get(key) is None:
            out[key] = num
            continue
        if key in {"number_of_clipped_samples", "clipped_samples", "number_of_clips"}:
            try:
                out["clipped_samples"] = int(num)
            except Exception:
                out["clipped_samples"] = 0
            continue
        if key in {"number_of_samples", "samples"}:
            try:
                out["samples"] = int(num)
            except Exception:
                out["samples"] = None
    if out.get("peak_level") is not None and out.get("rms_level") is not None:
        out["crest_factor"] = out["peak_level"] - out["rms_level"]
    return out

@app.get("/api/analyze-source")
def analyze_source(song: str):
    song = (song or "").strip()
    if not song:
        raise HTTPException(status_code=400, detail="missing_song")
    path = find_input_file(song)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="source_not_found")
    mime, _ = mimetypes.guess_type(path.name)
    return FileResponse(path, media_type=mime or "application/octet-stream", filename=path.name)
@app.get("/api/analyze-file")
def analyze_file(kind: str, name: str):
    kind = (kind or "").strip().lower()
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="missing_name")
    if kind == "source":
        root = IN_DIR
    elif kind in {"import", "imported"}:
        root = ANALYSIS_IN_DIR
    else:
        raise HTTPException(status_code=400, detail="invalid_kind")
    path = _safe_rel(root, name)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file_not_found")
    mime, _ = mimetypes.guess_type(path.name)
    return FileResponse(path, media_type=mime or "application/octet-stream", filename=path.name)
@app.get("/api/analyze/path")
def analyze_path(path: str):
    target = _resolve_analysis_path(path)
    mime, _ = mimetypes.guess_type(target.name)
    return FileResponse(target, media_type=mime or "application/octet-stream", filename=target.name)
@app.get("/api/analyze-resolve")
def analyze_resolve(song: str, out: str = "", solo: bool = False):
    song = (song or "").strip()
    if not song:
        raise HTTPException(status_code=400, detail="missing_song")
    folder = (OUT_DIR / song).resolve()
    if OUT_DIR.resolve() not in folder.parents:
        raise HTTPException(status_code=400, detail="invalid_path")
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="run_not_found")
    processed, files = _resolve_processed_file(folder, out)
    if not processed:
        raise HTTPException(status_code=404, detail="output_not_found")
    if solo:
        metrics = None
        output_metrics = _load_output_metrics(folder, processed)
        if output_metrics:
            metrics = {"input": output_metrics, "output": None, "version": 1, "run_id": song}
        else:
            try:
                metrics = {"input": basic_metrics(processed), "output": None, "version": 1, "run_id": song}
            except Exception:
                metrics = None
        payload = {
            "run_id": song,
            "source_url": f"/out/{quote(song)}/{quote(processed.name)}",
            "processed_url": None,
            "source_name": processed.name,
            "processed_name": "",
            "processed_label": None,
            "source_rel": f"out/{song}/{processed.name}",
            "processed_rel": None,
            "available_outputs": [],
            "metrics": metrics,
        }
        payload.update(_analysis_overlay_data(processed, None))
        return payload

    source_path = find_input_file(song)
    source_name = source_path.name if source_path else song
    source_url = f"/api/analyze-source?song={quote(song)}" if source_path else ""
    processed_url = f"/out/{quote(song)}/{quote(processed.name)}"
    processed_rel = f"out/{song}/{processed.name}"
    metrics = None
    output_metrics = _load_output_metrics(folder, processed)
    if output_metrics:
        metrics = wrap_metrics(song, output_metrics)
        if metrics:
            metrics = fill_input_metrics(song, metrics, folder)
    elif source_path:
        try:
            metrics = {
                "version": 1,
                "run_id": song,
                "input": basic_metrics(source_path),
                "output": None,
            }
        except Exception:
            metrics = None
    payload = {
        "run_id": song,
        "source_url": source_url,
        "processed_url": processed_url,
        "source_name": source_name,
        "processed_name": processed.name,
        "processed_label": processed.suffix.lower().lstrip("."),
        "source_rel": _analysis_rel_for_path(source_path) if source_path else None,
        "processed_rel": processed_rel,
        "available_outputs": _available_outputs(song, files, processed.stem),
        "metrics": metrics,
    }
    payload.update(_analysis_overlay_data(source_path, processed))
    return payload
@app.get("/api/analyze-resolve-pair")
def analyze_resolve_pair(src: str, proc: str):
    src = (src or "").strip()
    proc = (proc or "").strip()
    if not src or not proc:
        raise HTTPException(status_code=400, detail="missing_target")
    source_path = _resolve_analysis_path(src)
    processed_path = _resolve_analysis_path(proc)
    metrics = None
    try:
        metrics = {
            "input": basic_metrics(source_path),
            "output": basic_metrics(processed_path),
        }
    except Exception:
        metrics = None
    payload = {
        "run_id": None,
        "source_url": f"/api/analyze/path?path={quote(src)}",
        "processed_url": f"/api/analyze/path?path={quote(proc)}",
        "source_name": source_path.name,
        "processed_name": processed_path.name,
        "processed_label": processed_path.suffix.lower().lstrip("."),
        "source_rel": src,
        "processed_rel": proc,
        "available_outputs": [],
        "metrics": metrics,
    }
    payload.update(_analysis_overlay_data(source_path, processed_path))
    return payload
@app.get("/api/analyze-resolve-file")
def analyze_resolve_file(src: str = "", imp: str = ""):
    src = (src or "").strip()
    imp = (imp or "").strip()
    if src and imp:
        raise HTTPException(status_code=400, detail="ambiguous_target")
    if not src and not imp:
        raise HTTPException(status_code=400, detail="missing_target")
    kind = "source" if src else "import"
    rel = src if src else imp
    root = IN_DIR if src else ANALYSIS_IN_DIR
    path = _safe_rel(root, rel)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file_not_found")
    metrics = None
    metrics_path = None
    if kind == "import":
        metrics_path = ANALYSIS_OUT_DIR / f"{path.stem}.metrics.json"
    if metrics_path and metrics_path.exists():
        metrics = read_metrics_file(metrics_path)
    if metrics is None:
        try:
            metrics = basic_metrics(path)
        except Exception:
            metrics = None
    payload = {
        "run_id": None,
        "source_url": f"/api/analyze-file?kind={kind}&name={quote(rel)}",
        "processed_url": None,
        "source_name": path.name,
        "processed_name": "",
        "processed_label": None,
        "source_rel": f"{'in' if kind == 'source' else 'analysis'}/{rel}",
        "processed_rel": None,
        "available_outputs": [],
        "metrics": {"input": metrics, "output": None} if metrics else None,
    }
    payload.update(_analysis_overlay_data(path, None))
    return payload
@app.post("/api/analyze-upload")
async def analyze_upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing_filename")
    suffix = Path(file.filename).suffix.lower()
    allowed = {".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a", ".aac", ".ogg"}
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail="unsupported_type")
    safe_stem = _safe_slug(Path(file.filename).stem) or "analysis"
    stamp = int(time.time())
    dest = ANALYSIS_IN_DIR / f"{safe_stem}-{stamp}{suffix}"
    size = 0
    with dest.open("wb") as fout:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="file_too_large")
            fout.write(chunk)
    metrics = None
    try:
        metrics = basic_metrics(dest)
    except Exception:
        metrics = None
    if metrics:
        try:
            ANALYSIS_OUT_DIR.mkdir(parents=True, exist_ok=True)
            (ANALYSIS_OUT_DIR / f"{dest.stem}.metrics.json").write_text(
                json.dumps(metrics, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
    payload = {
        "id": dest.stem,
        "source_url": f"/analysis/{quote(dest.name)}",
        "metrics": metrics,
        "source_name": file.filename,
        "rel": dest.name,
    }
    payload.update(_analysis_overlay_data(dest, None))
    return payload


@app.get("/api/analyze/spectrogram")
def analyze_spectrogram(path: str, w: int = 1200, h: int = 256, mode: str = "log", drange: int = 120, scale: str | None = None):
    target = _resolve_analysis_path(path)
    width = max(320, min(2000, int(w)))
    height = max(128, min(1024, int(h)))
    drange = max(40, min(160, int(drange)))
    requested = scale if scale is not None else mode
    scale = "log" if str(requested).strip().lower() == "log" else "lin"
    cache_dir = ANALYSIS_TMP_DIR / "spectrograms"
    cache_dir.mkdir(parents=True, exist_ok=True)
    stat = target.stat()
    key_raw = f"{target.resolve()}::{stat.st_mtime}::{width}::{height}::{scale}::{drange}"
    key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
    out_path = cache_dir / f"{key}.png"
    if not out_path.exists():
        filt = (
            f"showspectrumpic=s={width}x{height}:mode=separate:"
            f"scale={scale}:legend=disabled:color=viridis:drange={drange}"
        )
        cmd = [
            FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(target), "-lavfi", filt, "-frames:v", "1",
            str(out_path),
        ]
        proc = run_cmd(cmd)
        if proc.returncode != 0 or not out_path.exists():
            err = (proc.stderr or proc.stdout or "").strip()
            raise HTTPException(status_code=500, detail=err or "spectrogram_failed")
    return FileResponse(out_path, media_type="image/png")


@app.post("/api/analyze/noise/preview")
def analyze_noise_preview(payload: dict = Body(...)):
    path = payload.get("path")
    target = _resolve_analysis_path(path)
    start = payload.get("start_sec")
    end = payload.get("end_sec")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        raise HTTPException(status_code=400, detail="missing_range")
    start = float(start)
    end = float(end)
    if end <= start:
        raise HTTPException(status_code=400, detail="invalid_range")
    preview_len = payload.get("preview_len_sec")
    if not isinstance(preview_len, (int, float)):
        preview_len = 10.0
    preview_len = max(4.0, min(20.0, float(preview_len)))
    mid = (start + end) * 0.5
    preview_start = max(0.0, mid - preview_len * 0.5)
    af = _noise_filter_chain(payload)
    NOISE_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    preview_id = uuid.uuid4().hex
    out_path = NOISE_PREVIEW_DIR / f"{preview_id}.mp3"
    cmd = [
        FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{preview_start:.3f}",
        "-t", f"{preview_len:.3f}",
        "-i", str(target),
        "-af", af,
        "-vn", "-ac", "2", "-ar", str(PREVIEW_SAMPLE_RATE),
        "-codec:a", "libmp3lame", "-b:a", f"{PREVIEW_BITRATE_KBPS}k",
        str(out_path),
    ]
    proc = run_cmd(cmd)
    if proc.returncode != 0 or not out_path.exists():
        err = (proc.stderr or proc.stdout or "").strip()
        raise HTTPException(status_code=500, detail=err or "preview_failed")
    return {
        "url": f"/api/analyze/noise/preview_audio?token={quote(preview_id)}",
        "preview_start": preview_start,
        "duration": preview_len,
    }


@app.get("/api/analyze/noise/preview_audio")
def analyze_noise_preview_audio(token: str):
    token = (token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="missing_token")
    fp = NOISE_PREVIEW_DIR / f"{token}.mp3"
    if NOISE_PREVIEW_DIR.resolve() not in fp.resolve().parents or not fp.exists():
        raise HTTPException(status_code=404, detail="preview_not_found")
    return FileResponse(fp, media_type="audio/mpeg", filename=f"noise-preview-{token}.mp3")


@app.post("/api/analyze/noise/render")
def analyze_noise_render(payload: dict = Body(...)):
    path = payload.get("path")
    target = _resolve_analysis_path(path)
    af = _noise_filter_chain(payload)
    ANALYSIS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower() if target.suffix else ".wav"
    codec_map = {
        ".wav": "pcm_s16le",
        ".aiff": "pcm_s16be",
        ".aif": "pcm_s16be",
        ".flac": "flac",
        ".mp3": "libmp3lame",
        ".m4a": "aac",
        ".aac": "aac",
        ".ogg": "libvorbis",
    }
    codec = codec_map.get(suffix, "pcm_s16le")
    out_path = _unique_output_path(ANALYSIS_OUT_DIR, f"{target.stem}.cleaned", suffix)
    cmd = [
        FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(target),
        "-af", af,
        "-vn", "-ac", "2",
        "-codec:a", codec,
        str(out_path),
    ]
    proc = run_cmd(cmd)
    if proc.returncode != 0 or not out_path.exists():
        err = (proc.stderr or proc.stdout or "").strip()
        raise HTTPException(status_code=500, detail=err or "render_failed")
    return {
        "output_rel": out_path.name,
        "output_name": out_path.name,
        "url": f"/api/analyze/noise/output?rel={quote(out_path.name)}",
    }


@app.get("/api/analyze/noise/output")
def analyze_noise_output(rel: str):
    rel = (rel or "").strip()
    if not rel:
        raise HTTPException(status_code=400, detail="missing_rel")
    target = _safe_rel(ANALYSIS_OUT_DIR, rel)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not_found")
    return FileResponse(target)


@app.post("/api/analyze/noise/preset/save")
def analyze_noise_preset_save(payload: dict = Body(...)):
    title = _sanitize_label(payload.get("title") or "Noise Filter", 80)
    settings = payload.get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    f_low = settings.get("f_low")
    f_high = settings.get("f_high")
    if not isinstance(f_low, (int, float)) or not isinstance(f_high, (int, float)):
        raise HTTPException(status_code=400, detail="missing_band")
    f_low = max(20.0, min(20000.0, float(f_low)))
    f_high = max(20.0, min(20000.0, float(f_high)))
    if f_high <= f_low:
        raise HTTPException(status_code=400, detail="invalid_band")
    origin_dir = _noise_filter_dir("user")
    origin_dir.mkdir(parents=True, exist_ok=True)
    slug = _safe_slug(title) or "noise_filter"
    out_path = _unique_output_path(origin_dir, slug, ".json")
    mode = (settings.get("mode") or "remove").strip().lower()
    band_depth = settings.get("band_depth_db")
    if not isinstance(band_depth, (int, float)):
        band_depth = -18.0
    band_depth = -abs(float(band_depth))
    afftdn_strength = settings.get("afftdn_strength")
    if not isinstance(afftdn_strength, (int, float)):
        afftdn_strength = 0.35
    afftdn_strength = max(0.0, min(1.0, float(afftdn_strength)))
    hp_hz = settings.get("hp_hz")
    lp_hz = settings.get("lp_hz")
    preset = {
        "id": out_path.stem,
        "meta": {
            "title": title,
            "kind": "noise_filter",
            "tags": [
                "Generated from spectrogram selection.",
                f"band: {f_low:.0f}-{f_high:.0f}Hz",
                f"mode: {mode}",
                f"afftdn: {afftdn_strength:.2f}",
            ],
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        },
        "noise": {
            "f_low": f_low,
            "f_high": f_high,
            "band_depth_db": band_depth,
            "afftdn_strength": afftdn_strength,
            "hp_hz": hp_hz,
            "lp_hz": lp_hz,
        },
    }
    out_path.write_text(json.dumps(preset, indent=2), encoding="utf-8")
    return {
        "item": {
            "id": out_path.stem,
            "name": out_path.stem,
            "origin": "user",
            "readonly": False,
            "kind": "noise_filter",
            "meta": preset.get("meta", {}),
        }
    }

def _ai_tool_audio_info(target: Path) -> dict:
    info = docker_ffprobe_json(target)
    duration = None
    try:
        duration = float(info.get("format", {}).get("duration"))
    except Exception:
        duration = None
    sample_rate = None
    channels = None
    for stream in info.get("streams", []) if isinstance(info, dict) else []:
        if stream.get("codec_type") != "audio":
            continue
        raw_sr = stream.get("sample_rate")
        try:
            sample_rate = int(raw_sr)
        except Exception:
            sample_rate = None
        try:
            channels = int(stream.get("channels"))
        except Exception:
            channels = None
        break
    return {
        "duration_s": duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "mtime": target.stat().st_mtime if target.exists() else None,
    }

@app.get("/api/ai-tool/info")
def ai_tool_info(path: str):
    target = _resolve_analysis_path(path)
    payload = {
        "name": target.name,
        "path": _analysis_rel_for_path(target) or path,
    }
    payload.update(_ai_tool_audio_info(target))
    return payload

@app.post("/api/ai-tool/preview")
def ai_tool_preview(payload: dict = Body(...)):
    path = payload.get("path")
    tool_id = payload.get("tool_id")
    strength = _ai_tool_strength(payload.get("strength"), 30)
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    target = _resolve_analysis_path(path)
    preview_len = payload.get("preview_len_sec")
    if not isinstance(preview_len, (int, float)):
        preview_len = 10.0
    preview_len = max(4.0, min(20.0, float(preview_len)))
    focus = payload.get("preview_focus_sec")
    if isinstance(focus, (int, float)):
        preview_start = max(0.0, float(focus) - preview_len * 0.5)
    else:
        preview_start = 0.0
    chain = _ai_tool_filter_chain(tool_id, strength, options)
    AI_TOOL_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    preview_id = uuid.uuid4().hex
    out_path = AI_TOOL_PREVIEW_DIR / f"{preview_id}.mp3"
    cmd = [
        FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{preview_start:.3f}",
        "-t", f"{preview_len:.3f}",
        "-i", str(target),
    ]
    if chain:
        cmd += ["-af", chain]
    cmd += [
        "-vn", "-ac", "2", "-ar", str(PREVIEW_SAMPLE_RATE),
        "-codec:a", "libmp3lame", "-b:a", f"{PREVIEW_BITRATE_KBPS}k",
        str(out_path),
    ]
    proc = run_cmd(cmd)
    if proc.returncode != 0 or not out_path.exists():
        err = (proc.stderr or proc.stdout or "").strip()
        raise HTTPException(status_code=500, detail=err or "preview_failed")
    return {
        "url": f"/api/ai-tool/preview_audio?token={quote(preview_id)}",
        "preview_start": preview_start,
        "duration": preview_len,
        "tool_id": tool_id,
    }

@app.get("/api/ai-tool/preview_audio")
def ai_tool_preview_audio(token: str):
    token = (token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="missing_token")
    fp = AI_TOOL_PREVIEW_DIR / f"{token}.mp3"
    if AI_TOOL_PREVIEW_DIR.resolve() not in fp.resolve().parents or not fp.exists():
        raise HTTPException(status_code=404, detail="preview_not_found")
    return FileResponse(fp, media_type="audio/mpeg", filename=f"ai-preview-{token}.mp3")

@app.post("/api/ai-tool/render")
def ai_tool_render(payload: dict = Body(...)):
    path = payload.get("path")
    tool_id = (payload.get("tool_id") or "").strip().lower()
    strength = _ai_tool_strength(payload.get("strength"), 30)
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    target = _resolve_analysis_path(path)
    chain = _ai_tool_filter_chain(tool_id, strength, options)
    ANALYSIS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower() if target.suffix else ".wav"
    codec_map = {
        ".wav": "pcm_s16le",
        ".aiff": "pcm_s16be",
        ".aif": "pcm_s16be",
        ".flac": "flac",
        ".mp3": "libmp3lame",
        ".m4a": "aac",
        ".aac": "aac",
        ".ogg": "libvorbis",
    }
    codec = codec_map.get(suffix, "pcm_s16le")
    tool_suffix = _safe_slug(tool_id.replace("ai_", "ai-")) or _safe_slug(tool_id) or "ai"
    out_path = _unique_output_path(ANALYSIS_OUT_DIR, f"{target.stem}.ai-{tool_suffix}", suffix)
    cmd = [
        FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(target),
    ]
    if chain:
        cmd += ["-af", chain]
    cmd += [
        "-vn", "-ac", "2",
        "-codec:a", codec,
        str(out_path),
    ]
    proc = run_cmd(cmd)
    if proc.returncode != 0 or not out_path.exists():
        err = (proc.stderr or proc.stdout or "").strip()
        raise HTTPException(status_code=500, detail=err or "render_failed")
    rel = _analysis_rel_for_path(out_path) or f"analysis_out/{out_path.name}"
    return {
        "output_rel": rel,
        "output_name": out_path.name,
        "url": f"/api/analyze/path?path={quote(rel)}",
        "tool_id": tool_id,
    }

@app.get("/api/ai-tool/preset/list")
def ai_tool_preset_list():
    items = []
    try:
        AI_TOOL_PRESET_DIR.mkdir(parents=True, exist_ok=True)
        for fp in sorted(AI_TOOL_PRESET_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not fp.is_file() or fp.suffix.lower() != ".json":
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            meta = data.get("meta") or {}
            items.append({
                "id": fp.stem,
                "title": meta.get("title") or fp.stem,
                "tool_id": data.get("tool_id"),
                "strength": data.get("strength"),
                "options": data.get("options") or {},
                "meta": meta,
            })
    except Exception:
        items = []
    return {"items": items}

@app.post("/api/ai-tool/preset/save")
def ai_tool_preset_save(payload: dict = Body(...)):
    title = _sanitize_label(payload.get("title") or "AI Tool Preset", 80)
    tool_id = (payload.get("tool_id") or "").strip().lower()
    if tool_id not in AI_TOOL_IDS:
        raise HTTPException(status_code=400, detail="invalid_tool")
    strength = _ai_tool_strength(payload.get("strength"), 30)
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    AI_TOOL_PRESET_DIR.mkdir(parents=True, exist_ok=True)
    slug = _safe_slug(title) or "ai_tool"
    out_path = _unique_output_path(AI_TOOL_PRESET_DIR, slug, ".json")
    preset = {
        "id": out_path.stem,
        "meta": {
            "title": title,
            "kind": "ai_tool",
            "tags": [f"tool: {tool_id}", f"strength: {strength}"],
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        },
        "tool_id": tool_id,
        "strength": strength,
        "options": options,
    }
    out_path.write_text(json.dumps(preset, indent=2), encoding="utf-8")
    return {"item": preset}

@app.delete("/api/ai-tool/preset/delete")
def ai_tool_preset_delete(preset_id: str):
    preset_id = (preset_id or "").strip()
    if not preset_id:
        raise HTTPException(status_code=400, detail="missing_id")
    target = _safe_rel(AI_TOOL_PRESET_DIR, f"{preset_id}.json")
    if not target.exists():
        raise HTTPException(status_code=404, detail="preset_not_found")
    target.unlink(missing_ok=True)
    return {"deleted": preset_id}

@app.get("/api/ai-tool/detect")
def ai_tool_detect(path: str, mode: str = "fast"):
    target = _resolve_analysis_path(path)
    mode = (mode or "fast").strip().lower()
    mode = "full" if mode == "full" else "fast"
    duration = _duration_seconds(target) or 0.0
    seg = 30.0 if mode == "fast" else 60.0
    if duration and duration < seg:
        seg = max(5.0, duration)
    start = 0.0
    if duration and duration > seg:
        start = min(30.0, duration / 3.0)
        if start + seg > duration:
            start = max(0.0, duration - seg)

    full = _ai_astats_segment(target, start, seg, [])
    hf = _ai_astats_segment(target, start, seg, ["highpass=f=8000"])
    lf = _ai_astats_segment(target, start, seg, ["lowpass=f=80"])
    lowmid = _ai_astats_segment(target, start, seg, ["highpass=f=150", "lowpass=f=350"])
    presence = _ai_astats_segment(target, start, seg, ["highpass=f=2500", "lowpass=f=6000"])

    full_rms = full.get("rms_level") if isinstance(full, dict) else None
    peak = full.get("peak_level") if isinstance(full, dict) else None
    crest = full.get("crest_factor") if isinstance(full, dict) else None
    clipped = full.get("clipped_samples") if isinstance(full, dict) else 0

    hf_ratio = _ai_db_ratio(hf.get("rms_level") if isinstance(hf, dict) else None, full_rms)
    lf_ratio = _ai_db_ratio(lf.get("rms_level") if isinstance(lf, dict) else None, full_rms)
    lowmid_ratio = _ai_db_ratio(lowmid.get("rms_level") if isinstance(lowmid, dict) else None, full_rms)
    presence_ratio = _ai_db_ratio(presence.get("rms_level") if isinstance(presence, dict) else None, full_rms)

    metrics = {
        "segment_start": start,
        "segment_duration": seg,
        "fullband": full,
        "ratios": {
            "hf": hf_ratio,
            "low": lf_ratio,
            "lowmid": lowmid_ratio,
            "presence": presence_ratio,
        },
    }

    findings: list[dict] = []

    hiss_sev = _ai_severity_from_ratio(hf_ratio, 0.12, 0.3)
    if hiss_sev > 0:
        findings.append({
            "id": "hiss_glass",
            "severity": round(hiss_sev, 3),
            "confidence": _ai_confidence(hiss_sev),
            "summary": "High-end hiss/glass likely (8–16 kHz energy elevated).",
            "suggested_tool_id": "ai_deglass",
        })

    rumble_sev = max(
        _ai_severity_from_ratio(lf_ratio, 0.12, 0.3),
        _ai_severity_from_ratio(lowmid_ratio, 0.14, 0.28),
    )
    if rumble_sev > 0:
        findings.append({
            "id": "rumble_mud",
            "severity": round(rumble_sev, 3),
            "confidence": _ai_confidence(rumble_sev),
            "summary": "Low-end rumble or mud likely (sub/low-mid energy elevated).",
            "suggested_tool_id": "ai_bass_tight",
        })

    presence_sev = _ai_severity_from_ratio(presence_ratio, 0.16, 0.28)
    if presence_sev > 0:
        findings.append({
            "id": "harsh_presence",
            "severity": round(presence_sev, 3),
            "confidence": _ai_confidence(presence_sev),
            "summary": "Harsh presence region elevated (2.5–6 kHz).",
            "suggested_tool_id": "ai_vocal_smooth",
        })

    clip_sev = 0.0
    if isinstance(clipped, int) and clipped > 0:
        clip_sev = 0.95
    if isinstance(peak, (int, float)):
        if peak > -0.2:
            clip_sev = max(clip_sev, 0.7)
        elif peak > -0.8:
            clip_sev = max(clip_sev, 0.4)
    if isinstance(crest, (int, float)):
        if crest < 6.0:
            clip_sev = max(clip_sev, 0.85)
        elif crest < 7.5:
            clip_sev = max(clip_sev, 0.65)
        elif crest < 9.0:
            clip_sev = max(clip_sev, 0.4)
    if clip_sev > 0:
        findings.append({
            "id": "limited_clipping",
            "severity": round(clip_sev, 3),
            "confidence": _ai_confidence(clip_sev),
            "summary": "Likely clipping or over-limited dynamics.",
            "suggested_tool_id": "ai_platform_safe",
        })

    findings.sort(key=lambda f: f.get("severity", 0), reverse=True)
    findings = findings[:3]

    info = _ai_tool_audio_info(target)
    track = {
        "duration": info.get("duration_s"),
        "sr": info.get("sample_rate"),
        "channels": info.get("channels"),
    }
    return {"track": track, "metrics": metrics, "findings": findings}
@app.get("/api/analyze-sources")
def analyze_sources():
    items = []
    try:
        for fp in sorted(IN_DIR.iterdir(), key=lambda p: p.name.lower()):
            if not fp.is_file() or fp.suffix.lower() not in ANALYZE_AUDIO_EXTS:
                continue
            items.append({
                "kind": "source",
                "label": fp.name,
                "rel": fp.name,
                "meta": {"size": fp.stat().st_size},
            })
    except Exception:
        pass
    return {"items": items}
@app.get("/api/analyze-imports")
def analyze_imports():
    items = []
    try:
        for fp in sorted(ANALYSIS_IN_DIR.iterdir(), key=lambda p: p.name.lower()):
            if not fp.is_file() or fp.suffix.lower() not in ANALYZE_AUDIO_EXTS:
                continue
            items.append({
                "kind": "import",
                "label": fp.name,
                "rel": fp.name,
                "meta": {"size": fp.stat().st_size},
            })
    except Exception:
        pass
    return {"items": items}
@app.get("/api/analyze-runs")
def analyze_runs(limit: int = 30):
    items = []
    try:
        runs = [d for d in OUT_DIR.iterdir() if d.is_dir()]
        runs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        for run in runs[:limit]:
            files = [p for p in _list_audio_files(run) if not (p.stem == run.name and "__" not in p.stem)]
            if not files:
                continue
            stems = {}
            for fp in files:
                stems.setdefault(fp.stem, []).append(fp)
            for stem, stem_files in stems.items():
                formats = sorted({p.suffix.lower().lstrip(".") for p in stem_files})
                base = stem.split("__", 1)[0] if "__" in stem else stem
                label = f"{base} ({', '.join([f.upper() for f in formats])})" if formats else base
                items.append({
                    "kind": "run",
                    "label": label,
                    "rel": stem,
                    "runId": run.name,
                    "out": stem,
                    "meta": {"formats": formats, "run": run.name, "detail": run.name},
                })
    except Exception:
        pass
    return {"items": items}
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(250 * 1024 * 1024)))  # default 250MB
ALLOWED_UPLOAD_EXT = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
CHUNK_SIZE = 4 * 1024 * 1024
def _safe_upload_name(name: str) -> str:
    if not name or ".." in name or name.startswith("/"):
        raise HTTPException(status_code=400, detail="invalid_filename")
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXT:
        raise HTTPException(status_code=400, detail="unsupported_type")
    safe = Path(name).name
    if not safe:
        raise HTTPException(status_code=400, detail="invalid_filename")
    return safe

@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    IN_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for file in files:
        safe_name = _safe_upload_name(file.filename)
        dest = IN_DIR / safe_name
        size = 0
        with dest.open("wb") as fout:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="file_too_large")
                fout.write(chunk)
        saved.append(dest.name)
    return JSONResponse({"message": f"Uploaded: {', '.join(saved)}"})
@app.post("/api/master")
def master(
    infile: str = Form(...),
    preset: str = Form(...),
    strength: int = Form(80),
    lufs: float | None = Form(None),
    tp: float | None = Form(None),
    width: float | None = Form(None),
    mono_bass: float | None = Form(None),
    guardrails: int = Form(0),
):
    safe_in = _validate_input_file(infile)
    try:
        result = mastering_pack.run_master_job(
            str(safe_in.name),
            strength=strength,
            presets=preset,
            lufs=lufs,
            tp=tp,
            width=width,
            mono_bass=mono_bass,
            guardrails=bool(guardrails),
        )
        outputs = result.get("outputs") or []
        return "\n".join(outputs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.post("/api/master-pack")
def master_pack(
    infile: str = Form(...),
    strength: int = Form(80),
    lufs: float | None = Form(None),
    tp: float | None = Form(None),
    width: float | None = Form(None),
    mono_bass: float | None = Form(None),
    guardrails: int = Form(0),
    out_wav: int = Form(1),
    out_mp3: int = Form(0),
    mp3_bitrate: str | None = Form(None),
    mp3_vbr: str | None = Form(None),
    out_aac: int = Form(0),
    aac_bitrate: str | None = Form(None),
    aac_codec: str | None = Form(None),
    aac_container: str | None = Form(None),
    out_ogg: int = Form(0),
    ogg_quality: str | None = Form(None),
    out_flac: int = Form(0),
    flac_level: str | None = Form(None),
    flac_bit_depth: str | None = Form(None),
    flac_sample_rate: str | None = Form(None),
    wav_bit_depth: str | None = Form(None),
    wav_sample_rate: str | None = Form(None),
    voicing_mode: str = Form("presets"),
    voicing_name: str | None = Form(None),
    presets: str | None = Form(None),
):
    safe_in = _validate_input_file(infile)
    run_id = Path(safe_in.name).stem or safe_in.name
    def _event_cb(event: dict):
        if not isinstance(event, dict):
            return
        loop_obj = getattr(status_bus, "loop", None) or MAIN_LOOP
        if loop_obj and loop_obj.is_running():
            try:
                asyncio.run_coroutine_threadsafe(status_bus.append_events(run_id, [event]), loop_obj)
            except Exception:
                pass
    def run_pack():
        try:
            loop_obj = getattr(status_bus, "loop", None) or MAIN_LOOP
            if loop_obj and loop_obj.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(status_bus.mark_direct(run_id), loop_obj)
                except Exception:
                    pass
            mastering_pack.run_master_job(
                str(safe_in.name),
                strength=strength,
                presets=presets,
                lufs=lufs,
                tp=tp,
                width=width,
                mono_bass=mono_bass,
                guardrails=bool(guardrails),
                out_wav=out_wav,
                out_mp3=out_mp3,
                mp3_bitrate=mp3_bitrate if mp3_bitrate is not None else 320,
                mp3_vbr=mp3_vbr if mp3_vbr is not None else "none",
                out_aac=out_aac,
                aac_bitrate=aac_bitrate if aac_bitrate is not None else 256,
                aac_codec=aac_codec if aac_codec is not None else "aac",
                aac_container=aac_container if aac_container is not None else "m4a",
                out_ogg=out_ogg,
                ogg_quality=ogg_quality if ogg_quality is not None else 5.0,
                out_flac=out_flac,
                flac_level=flac_level if flac_level is not None else 5,
                flac_bit_depth=flac_bit_depth,
                flac_sample_rate=flac_sample_rate,
                wav_bit_depth=wav_bit_depth if wav_bit_depth is not None else 24,
                wav_sample_rate=wav_sample_rate if wav_sample_rate is not None else 48000,
                voicing_mode=voicing_mode or "presets",
                voicing_name=voicing_name,
                event_cb=_event_cb,
            )
        except Exception as e:
            # Log to stderr; UI will refresh Previous Runs anyway.
            print(f"[master-pack] failed: {e}", file=sys.stderr)
        else:
            print(f"[master-pack] started infile={infile} presets={presets} strength={strength}", file=sys.stderr)
    threading.Thread(target=run_pack, daemon=True).start()
    return JSONResponse({"message": "pack started (async); outputs will appear in Previous Runs", "script": str(MASTER_SCRIPT)})

@app.post("/api/run")
def start_run(
    infiles: str = Form(...),
    strength: int = Form(80),
    lufs: float | None = Form(None),
    tp: float | None = Form(None),
    width: float | None = Form(None),
    mono_bass: float | None = Form(None),
    guardrails: int = Form(0),
    stage_analyze: int = Form(1),
    stage_master: int = Form(1),
    stage_loudness: int = Form(1),
    stage_stereo: int = Form(1),
    stage_output: int = Form(1),
    out_wav: int = Form(1),
    out_mp3: int = Form(0),
    mp3_bitrate: str | None = Form(None),
    mp3_vbr: str | None = Form(None),
    out_aac: int = Form(0),
    aac_bitrate: str | None = Form(None),
    aac_codec: str | None = Form(None),
    aac_container: str | None = Form(None),
    out_ogg: int = Form(0),
    ogg_quality: str | None = Form(None),
    out_flac: int = Form(0),
    flac_level: str | None = Form(None),
    flac_bit_depth: str | None = Form(None),
    flac_sample_rate: str | None = Form(None),
    wav_bit_depth: str | None = Form(None),
    wav_sample_rate: str | None = Form(None),
    voicing_mode: str = Form("presets"),
    voicing_name: str | None = Form(None),
    presets: str | None = Form(None),
):
    files = []
    for f in [x.strip() for x in infiles.split(",") if x.strip()]:
        safe = _validate_input_file(f)
        files.append(str(safe.name))
    if not files:
        raise HTTPException(status_code=400, detail="no_files")
    if RUNS_IN_FLIGHT >= MAX_CONCURRENT_RUNS:
        raise HTTPException(status_code=429, detail="too_many_runs")
    run_ids = _start_master_jobs(
        files, presets, strength, lufs, tp, width, mono_bass, guardrails,
        stage_analyze, stage_master, stage_loudness, stage_stereo, stage_output,
        out_wav, out_mp3, mp3_bitrate, mp3_vbr,
        out_aac, aac_bitrate, aac_codec, aac_container,
        out_ogg, ogg_quality,
        out_flac, flac_level, flac_bit_depth, flac_sample_rate,
        wav_bit_depth, wav_sample_rate,
        voicing_mode, voicing_name
    )
    primary = run_ids[0] if run_ids else None
    return JSONResponse({
        "message": f"run started for {len(files)} file(s)",
        "script": str(MASTER_SCRIPT),
        "run_ids": run_ids,
        "primary_run_id": primary,
    })

@app.get("/api/run/{run_id}")
async def run_snapshot(run_id: str):
    """Return the current run snapshot (events + terminal flag) for reconnects."""
    await status_bus.ensure_watcher(run_id)
    snap = await status_bus.snapshot(run_id)
    return snap
@app.post("/api/master-bulk")
def master_bulk(
    infiles: str = Form(...),
    strength: int = Form(80),
    lufs: float | None = Form(None),
    tp: float | None = Form(None),
    width: float | None = Form(None),
    mono_bass: float | None = Form(None),
    guardrails: int = Form(0),
    stage_analyze: int = Form(1),
    stage_master: int = Form(1),
    stage_loudness: int = Form(1),
    stage_stereo: int = Form(1),
    stage_output: int = Form(1),
    out_wav: int = Form(1),
    out_mp3: int = Form(0),
    mp3_bitrate: str | None = Form(None),
    mp3_vbr: str | None = Form(None),
    out_aac: int = Form(0),
    aac_bitrate: str | None = Form(None),
    aac_codec: str | None = Form(None),
    aac_container: str | None = Form(None),
    out_ogg: int = Form(0),
    ogg_quality: str | None = Form(None),
    out_flac: int = Form(0),
    flac_level: str | None = Form(None),
    flac_bit_depth: str | None = Form(None),
    flac_sample_rate: str | None = Form(None),
    wav_bit_depth: str | None = Form(None),
    wav_sample_rate: str | None = Form(None),
    voicing_mode: str = Form("presets"),
    voicing_name: str | None = Form(None),
    presets: str | None = Form(None),
):
    files = []
    for f in [x.strip() for x in infiles.split(",") if x.strip()]:
        safe = _validate_input_file(f)
        files.append(str(safe.name))
    if not files:
        raise HTTPException(status_code=400, detail="no_files")
    if RUNS_IN_FLIGHT >= MAX_CONCURRENT_RUNS:
        raise HTTPException(status_code=429, detail="too_many_runs")
    run_ids = _start_master_jobs(
        files, presets, strength, lufs, tp, width, mono_bass, guardrails,
        stage_analyze, stage_master, stage_loudness, stage_stereo, stage_output,
        out_wav, out_mp3, mp3_bitrate, mp3_vbr,
        out_aac, aac_bitrate, aac_codec, aac_container,
        out_ogg, ogg_quality,
        out_flac, flac_level, flac_bit_depth, flac_sample_rate,
        wav_bit_depth, wav_sample_rate,
        voicing_mode, voicing_name
    )
    primary = run_ids[0] if run_ids else None
    return JSONResponse({
        "message": f"bulk started for {len(files)} file(s)",
        "script": str(MASTER_SCRIPT),
        "run_ids": run_ids,
        "primary_run_id": primary,
    })
def _validate_input_file(name: str) -> Path:
    """Validate a user-supplied infile lives under IN_DIR."""
    if not name or name.startswith("/") or ".." in name:
        raise HTTPException(status_code=400, detail="invalid_input_path")
    candidate = (IN_DIR / name).resolve()
    try:
        if IN_DIR.resolve() not in candidate.parents:
            raise HTTPException(status_code=400, detail="invalid_input_path")
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=400, detail="input_not_found")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_input_path")
    return candidate

@app.post("/api/preview/start")
def preview_start(request: Request, body: dict = Body(...), background_tasks: BackgroundTasks = None):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")
    song = (body.get("song") or "").strip()
    voicing = (body.get("voicing") or "universal").strip()
    voicing_data = body.get("voicing_data")
    strength = body.get("strength", 0)
    width = body.get("width", None)
    guardrails = bool(body.get("guardrails", False))
    lufs = body.get("lufs", None)
    tp = body.get("tp", None)
    start_s = body.get("start", None)
    try:
        strength_val = int(strength)
    except Exception:
        strength_val = 0
    strength_val = max(0, min(100, strength_val))
    if width is not None:
        try:
            width = float(width)
        except Exception:
            width = None
    if lufs is not None:
        try:
            lufs = float(lufs)
        except Exception:
            lufs = None
    if tp is not None:
        try:
            tp = float(tp)
        except Exception:
            tp = None
    if start_s is not None:
        try:
            start_s = float(start_s)
        except Exception:
            start_s = None
        if start_s is not None and start_s < 0:
            start_s = 0.0

    if voicing_data is not None:
        if not isinstance(voicing_data, dict):
            raise HTTPException(status_code=400, detail="invalid_voicing")
        voicing_data = _sanitize_preview_voicing(voicing_data)

    if not voicing_data and not _preview_find_voicing_path(voicing):
        raise HTTPException(status_code=400, detail="invalid_voicing")
    safe_in = _validate_input_file(song)
    session_key = _preview_session_key(request)
    preview_id = uuid.uuid4().hex
    voicing_key = json.dumps(voicing_data, sort_keys=True) if voicing_data else voicing
    params_raw = f"{song}|{voicing_key}|{strength_val}|{width}|{guardrails}|{lufs}|{tp}|{start_s}"
    params_hash = hashlib.sha256(params_raw.encode("utf-8")).hexdigest()
    event = threading.Event()

    _preview_cleanup(session_key)
    with PREVIEW_LOCK:
        PREVIEW_REGISTRY[preview_id] = {
            "session_key": session_key,
            "created_at": time.time(),
            "status": "building",
            "file_path": None,
            "mime": "audio/mpeg",
            "params_hash": params_hash,
            "error_msg": None,
            "input_path": str(safe_in),
            "voicing": voicing,
            "voicing_data": voicing_data,
            "strength": strength_val,
            "width": width,
            "guardrails": guardrails,
            "lufs": lufs,
            "tp": tp,
            "start_s": start_s,
            "event": event,
        }
        queue = PREVIEW_SESSION_INDEX.setdefault(session_key, deque())
        queue.append(preview_id)
    _preview_cleanup(session_key)

    if background_tasks is None:
        background_tasks = BackgroundTasks()
    background_tasks.add_task(_render_preview, preview_id)
    logger.debug("[preview] start id=%s song=%s", preview_id, safe_in.name)
    return JSONResponse({"preview_id": preview_id, "status": "building"})

@app.get("/api/preview/stream")
def preview_stream(request: Request, preview_id: str):
    session_key = _preview_session_key(request)
    with PREVIEW_LOCK:
        entry = PREVIEW_REGISTRY.get(preview_id)
        if not entry or entry.get("session_key") != session_key:
            raise HTTPException(status_code=404, detail="preview_not_found")

    def event_stream():
        with PREVIEW_LOCK:
            current = PREVIEW_REGISTRY.get(preview_id, {})
            status = current.get("status") or "error"
            url = f"/api/preview/file?preview_id={quote(preview_id)}" if status == "ready" else None
        yield f"data: {json.dumps({'status': status, 'url': url})}\n\n"
        if status in ("ready", "error"):
            return
        done_event = current.get("event")
        if isinstance(done_event, threading.Event):
            done_event.wait(timeout=PREVIEW_TTL_SEC)
        with PREVIEW_LOCK:
            current = PREVIEW_REGISTRY.get(preview_id, {})
            status = current.get("status") or "error"
            if status not in ("ready", "error"):
                status = "error"
                current["status"] = "error"
                current["error_msg"] = current.get("error_msg") or "preview_timeout"
                done_event = current.get("event")
                if isinstance(done_event, threading.Event):
                    done_event.set()
            payload = {"status": status}
            if status == "ready":
                payload["url"] = f"/api/preview/file?preview_id={quote(preview_id)}"
            if status == "error":
                payload["message"] = current.get("error_msg") or "preview_failed"
        yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )

@app.get("/api/preview/file")
def preview_file(request: Request, preview_id: str):
    session_key = _preview_session_key(request)
    with PREVIEW_LOCK:
        entry = PREVIEW_REGISTRY.get(preview_id)
        if not entry or entry.get("session_key") != session_key:
            raise HTTPException(status_code=404, detail="preview_not_found")
        if entry.get("status") != "ready":
            raise HTTPException(status_code=404, detail="preview_not_ready")
        path = entry.get("file_path")
        mime = entry.get("mime") or "audio/mpeg"
    if not path:
        raise HTTPException(status_code=404, detail="preview_missing")
    fp = Path(path)
    if PREVIEW_DIR.resolve() not in fp.resolve().parents or not fp.exists():
        raise HTTPException(status_code=404, detail="preview_missing")
    resp = FileResponse(fp, media_type=mime, filename=f"preview-{preview_id}.mp3")
    resp.headers["Cache-Control"] = "no-store"
    return resp
