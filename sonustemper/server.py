import json
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
from collections import deque
from pathlib import Path
from datetime import datetime
import os
import importlib.util
from urllib.parse import quote
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Body, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, Response, RedirectResponse
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
PREVIEW_SESSION_COOKIE = "st_preview_session"
PREVIEW_BITRATE_KBPS = int(os.getenv("PREVIEW_BITRATE_KBPS", "128"))
PREVIEW_SAMPLE_RATE = int(os.getenv("PREVIEW_SAMPLE_RATE", "44100"))
PRESET_DIR = Path(os.getenv("PRESET_DIR", os.getenv("PRESET_USER_DIR", str(DATA_DIR / "presets" / "user"))))
GEN_PRESET_DIR = Path(os.getenv("GEN_PRESET_DIR", str(DATA_DIR / "presets" / "generated")))
TAG_IN_DIR = Path(os.getenv("TAG_IN_DIR", str(DATA_DIR / "tagging" / "in")))
TAG_TMP_DIR = Path(os.getenv("TAG_TMP_DIR", str(DATA_DIR / "tagging" / "tmp")))
ANALYSIS_IN_DIR = Path(os.getenv("ANALYSIS_IN_DIR", str(DATA_DIR / "analysis" / "in")))
ANALYSIS_OUT_DIR = Path(os.getenv("ANALYSIS_OUT_DIR", str(DATA_DIR / "analysis" / "out")))
ANALYSIS_TMP_DIR = Path(os.getenv("ANALYSIS_TMP_DIR", str(DATA_DIR / "analysis" / "tmp")))
# Alias older variable names to new mastering locations for internal use
IN_DIR = MASTER_IN_DIR
OUT_DIR = MASTER_OUT_DIR
APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
UI_APP_DIR = REPO_ROOT / "sonustemper-ui" / "app"
# Security: API key protection (for CLI/scripts); set API_AUTH_DISABLED=1 to bypass explicitly.
API_KEY = os.getenv("API_KEY")
API_AUTH_DISABLED = os.getenv("API_AUTH_DISABLED") == "1"
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
# Ensure local modules are importable when loading master_pack by path.
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
# Trusted proxy check via shared secret (raw)
def is_trusted_proxy(mark: str) -> bool:
    return bool(mark) and bool(PROXY_SHARED_SECRET) and (mark == PROXY_SHARED_SECRET)
# master_pack.py is the unified mastering script (handles single or multiple presets/files).
_default_pack = REPO_ROOT / "sonustemper" / "master_pack.py"
# Use master_pack.py as the unified mastering script (handles single or multiple presets/files)
MASTER_SCRIPT = Path(os.getenv("MASTER_SCRIPT", str(_default_pack)))
app = FastAPI()
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
    app.mount("/ui/static", StaticFiles(directory=str(UI_STATIC_DIR)), name="ui-static")
if ui_router:
    app.include_router(ui_router, prefix="/ui")
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
MASTER_PACK_MODULE = None
MASTER_PACK_LOCK = threading.Lock()

def _get_master_pack_module():
    global MASTER_PACK_MODULE
    if MASTER_PACK_MODULE is not None:
        return MASTER_PACK_MODULE
    if not MASTER_SCRIPT or not Path(MASTER_SCRIPT).exists():
        return None
    with MASTER_PACK_LOCK:
        if MASTER_PACK_MODULE is not None:
            return MASTER_PACK_MODULE
        try:
            spec = importlib.util.spec_from_file_location("master_pack_preview", str(MASTER_SCRIPT))
            mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(mod)
            MASTER_PACK_MODULE = mod
        except Exception as exc:
            logger.exception("[preview] failed to load master_pack: %s", exc)
            MASTER_PACK_MODULE = None
    return MASTER_PACK_MODULE

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

def _build_preview_filter(voicing: str, strength: int, width: float | None) -> str | None:
    mod = _get_master_pack_module()
    if not mod or not hasattr(mod, "_voicing_filters"):
        return None
    try:
        return mod._voicing_filters(voicing, strength, width, True, False, None, None)
    except Exception as exc:
        logger.warning("[preview] voicing filter build failed: %s", exc)
        return None

def _render_preview(preview_id: str) -> None:
    with PREVIEW_LOCK:
        entry = PREVIEW_REGISTRY.get(preview_id)
        if not entry:
            return
        input_path = entry.get("input_path")
        voicing = entry.get("voicing") or "universal"
        strength = int(entry.get("strength") or 0)
        width = entry.get("width")
    if not input_path:
        _preview_update(preview_id, "error", error_msg="missing_input")
        return

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PREVIEW_DIR / f"{preview_id}.mp3"
    limiter = "alimiter=limit=-1.0dB"
    if PREVIEW_NORMALIZE_MODE == "loudnorm":
        limiter = "loudnorm=I=-16:TP=-1:LRA=11"
    chain = _build_preview_filter(voicing, strength, width)
    af = f"{chain},{limiter}" if chain else limiter
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(PREVIEW_SEGMENT_START),
        "-t", str(PREVIEW_SEGMENT_DURATION),
        "-i", str(input_path),
        "-af", af,
        "-vn", "-ac", "2", "-ar", str(PREVIEW_SAMPLE_RATE),
        "-codec:a", "libmp3lame", "-b:a", f"{PREVIEW_BITRATE_KBPS}k",
        str(out_path),
    ]
    try:
        logger.debug("[preview] start id=%s voicing=%s strength=%s", preview_id, voicing, strength)
        proc = subprocess.run(cmd, capture_output=True, text=True)
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
    base_cmd = ["python3", str(MASTER_SCRIPT), "--strength", str(strength)]
    target_loop = getattr(status_bus, "loop", None) or MAIN_LOOP
    run_ids = [Path(f).stem or f for f in files]
    def _is_enabled(val):
        if val is None:
            return False
        if isinstance(val, (int, float)):
            return bool(val)
        txt = str(val).strip().lower()
        return txt not in ("0","false","off","no","")
    def _emit(run_id: str, stage: str, detail: str = ""):
        ev = {"stage": stage, "detail": detail, "ts": datetime.utcnow().timestamp()}
        loop_obj = getattr(status_bus, "loop", None) or MAIN_LOOP
        if loop_obj and loop_obj.is_running():
            try:
                asyncio.run_coroutine_threadsafe(status_bus.append_events(run_id, [ev]), loop_obj)
            except Exception:
                pass
    def run_all():
        for f, rid in zip(files, run_ids):
            do_analyze  = _is_enabled(stage_analyze)
            do_master   = _is_enabled(stage_master)
            do_loudness = _is_enabled(stage_loudness)
            do_stereo   = _is_enabled(stage_stereo)
            do_output   = _is_enabled(stage_output)
            cmd = base_cmd + ["--infile", f]
            if not do_analyze: cmd += ["--no_analyze"]
            if not do_master: cmd += ["--no_master"]
            if not do_loudness: cmd += ["--no_loudness"]
            if not do_stereo: cmd += ["--no_stereo"]
            if not do_output: cmd += ["--no_output"]
            if presets:
                cmd += ["--presets", presets]
            if do_loudness and lufs is not None:
                cmd += ["--lufs", str(lufs)]
            if do_loudness and tp is not None:
                cmd += ["--tp", str(tp)]
            if do_stereo and width is not None:
                cmd += ["--width", str(width)]
            if do_stereo and mono_bass is not None:
                cmd += ["--mono_bass", str(mono_bass)]
            if do_stereo and guardrails:
                cmd += ["--guardrails"]
            if voicing_mode:
                cmd += ["--voicing_mode", str(voicing_mode)]
            if voicing_name:
                cmd += ["--voicing_name", str(voicing_name)]
            if do_output:
                cmd += ["--out_wav", "1" if out_wav else "0"]
                cmd += ["--out_mp3", "1" if out_mp3 else "0"]
                cmd += ["--out_aac", "1" if out_aac else "0"]
                cmd += ["--out_ogg", "1" if out_ogg else "0"]
                cmd += ["--out_flac", "1" if out_flac else "0"]
                if wav_bit_depth: cmd += ["--wav_bit_depth", str(wav_bit_depth)]
                if wav_sample_rate: cmd += ["--wav_sample_rate", str(wav_sample_rate)]
                if mp3_bitrate: cmd += ["--mp3_bitrate", str(mp3_bitrate)]
                if mp3_vbr: cmd += ["--mp3_vbr", str(mp3_vbr)]
                if aac_bitrate: cmd += ["--aac_bitrate", str(aac_bitrate)]
                if aac_codec: cmd += ["--aac_codec", str(aac_codec)]
                if aac_container: cmd += ["--aac_container", str(aac_container)]
                if ogg_quality: cmd += ["--ogg_quality", str(ogg_quality)]
                if flac_level: cmd += ["--flac_level", str(flac_level)]
            if flac_bit_depth: cmd += ["--flac_bit_depth", str(flac_bit_depth)]
            if flac_sample_rate: cmd += ["--flac_sample_rate", str(flac_sample_rate)]
        try:
            print(f"[master-bulk] start file={f} presets={presets}", file=sys.stderr)
            _emit(rid, "queued", f)
            _emit(rid, "start", f"Processing {Path(f).name}")
            run_cmd_passthrough(cmd)
            print(f"[master-bulk] done file={f}", file=sys.stderr)
            _emit(rid, "complete", f"Finished {Path(f).name}")
        except subprocess.CalledProcessError as e:
            msg = (e.output or str(e)).strip().splitlines()[0] if (e.output or str(e)) else ""
            _emit(rid, "error", msg)
            print(f"[master-bulk] failed file={f}: {e.output or e}", file=sys.stderr)
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
        if st["task"]:
            return
        st["task"] = asyncio.create_task(self._watch_file(run_id))

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
        # Allow only when traffic came through trusted proxy marker
        proxy_mark = request.headers.get("X-SonusTemper-Proxy")
        if proxy_mark and is_trusted_proxy(proxy_mark):
            return await call_next(request)
        if proxy_mark and not is_trusted_proxy(proxy_mark):
            logger.warning(f"[auth] proxy mark mismatch len={len(proxy_mark)} path={request.url.path} mark={repr(proxy_mark)} expected={repr(PROXY_SHARED_SECRET)}")
        key = request.headers.get("X-API-Key")
        if not API_KEY:
            # No API key set; allow (proxy/basic auth provides guard)
            return await call_next(request)
        if key != API_KEY:
            print(f"[auth] reject: bad api key from {request.client.host if request.client else 'unknown'} path={request.url.path}", file=sys.stderr)
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
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
    if PRESET_DIR.exists():
        paths.extend(sorted(PRESET_DIR.glob("*.json")))
    if GEN_PRESET_DIR.exists():
        paths.extend(sorted(GEN_PRESET_DIR.glob("*.json")))
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
    if cmd[0] not in {"python3", "python", "ffprobe", "ffmpeg"}:
        raise ValueError("unexpected executable")

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
        "ffprobe", "-v", "quiet", "-print_format", "json",
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
def measure_loudness(path: Path) -> dict:
    r = run_cmd([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
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
        "ffmpeg", "-hide_banner", "-v", "verbose", "-nostats", "-i", str(path),
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

def _safe_slug(s: str, max_len: int = 64) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] if max_len and len(s) > max_len else s

def _preset_meta_from_file(fp: Path) -> dict:
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        return {
            "title": meta.get("title") or data.get("name") or fp.stem,
            "source_file": meta.get("source_file"),
            "created_at": meta.get("created_at"),
        }
    except Exception:
        return {"title": fp.stem}
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
    files = list(PRESET_DIR.glob("*.json")) if PRESET_DIR.exists() else []
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
@app.get("/", include_in_schema=False)
def new_ui_root():
    return RedirectResponse(url="/ui/", status_code=302)

# --- Utility file manager API ---
def _util_root(utility: str, section: str) -> Path:
    root = UTILITY_ROOTS.get((utility, section))
    if not root:
        raise HTTPException(status_code=400, detail="invalid_utility")
    return root.resolve()

def _safe_rel(root: Path, rel: str) -> Path:
    rel = rel.strip().lstrip("/").replace("\\", "/")
    candidate = (root / rel).resolve()
    if root not in candidate.parents and candidate != root:
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
    presets = sorted({p.stem for p in _preset_paths()})
    return {"files": files, "presets": presets}
@app.get("/api/presets")
def presets():
    # Return list of preset names derived from preset files on disk
    names = sorted({p.stem for p in _preset_paths()})
    return names
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
@app.get("/api/preset/list")
def preset_list():
    items = []
    for fp in _preset_paths():
        items.append({
            "name": fp.stem,
            "filename": fp.name,
            "meta": _preset_meta_from_file(fp),
        })
    return {"items": items}
@app.get("/api/preset/download/{name}")
def preset_download(name: str):
    target = None
    for fp in _preset_paths():
        if fp.stem == name:
            target = fp
            break
    if not target:
        raise HTTPException(status_code=404, detail="preset_not_found")
    return FileResponse(str(target), media_type="application/json", filename=target.name)
@app.delete("/api/preset/{name}")
def preset_delete(name: str):
    target = None
    # Only allow deleting user presets (PRESET_DIR)
    for fp in PRESET_DIR.glob("*.json"):
        if fp.stem == name:
            target = fp
            break
    if not target:
        if any(fp.stem == name for fp in GEN_PRESET_DIR.glob("*.json")):
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
    if "kind" not in meta:
        kind = "profile"
        if any(k in data for k in ("lufs", "tp", "limiter", "compressor", "loudness", "target_lufs", "target_tp")):
            kind = "profile"
        elif any(k in data for k in ("eq", "width", "stereo")):
            kind = "voicing"
        meta["kind"] = kind
        data["meta"] = meta
    # Minimal sanity check
    name = data.get("name") or Path(file.filename).stem
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="invalid_name")
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip())
    if not safe_name:
        raise HTTPException(status_code=400, detail="invalid_name")
    dest = PRESET_DIR / f"{safe_name}.json"
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
        target_dir = PRESET_DIR if PRESET_DIR.exists() and _writable(PRESET_DIR) else GEN_PRESET_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / f"{name_slug}.json"
        # Analyze reference and build preset (simple heuristic)
        metrics = analyze_reference(tmp_path)
        target_lufs = metrics.get("I", -14.0)
        tp = metrics.get("TP", -1.0)
        eq = []
        cf = metrics.get("crest_factor")
        if cf is not None and cf < 10:
            eq.append({"freq": 250, "gain": -1.5, "q": 1.0})
        eq.append({"freq": 9500, "gain": 1.0, "q": 0.8})
        kind = (kind or "profile").strip().lower()
        if kind not in {"profile", "voicing"}:
            kind = "profile"
        preset = {
            "name": name_slug,
            "lufs": target_lufs,
            "eq": eq,
            "compressor": {
                "threshold": -20,
                "ratio": 2.0,
                "attack": 20,
                "release": 180
            },
            "limiter": {
                "ceiling": tp if tp is not None else -1.0
            },
            "meta": {
                "source_file": file.filename,
                "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                "kind": kind,
            }
        }
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
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ffprobe_ok = shutil.which("ffprobe") is not None
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
@app.get("/api/analyze-resolve")
def analyze_resolve(song: str, out: str = ""):
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
    source_path = find_input_file(song)
    source_name = source_path.name if source_path else song
    source_url = f"/api/analyze-source?song={quote(song)}" if source_path else ""
    processed_url = f"/out/{quote(song)}/{quote(processed.name)}"
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
    return {
        "run_id": song,
        "source_url": source_url,
        "processed_url": processed_url,
        "source_name": source_name,
        "processed_name": processed.name,
        "processed_label": processed.suffix.lower().lstrip("."),
        "available_outputs": _available_outputs(song, files, processed.stem),
        "metrics": metrics,
    }
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
    return {
        "run_id": None,
        "source_url": f"/api/analyze-file?kind={kind}&name={quote(rel)}",
        "processed_url": None,
        "source_name": path.name,
        "processed_name": "",
        "processed_label": None,
        "available_outputs": [],
        "metrics": {"input": metrics, "output": None} if metrics else None,
    }
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
    return {
        "id": dest.stem,
        "source_url": f"/analysis/{quote(dest.name)}",
        "metrics": metrics,
        "source_name": file.filename,
        "rel": dest.name,
    }
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
    cmd = ["python3", str(MASTER_SCRIPT), "--infile", str(safe_in.name), "--strength", str(strength), "--presets", preset]
    if lufs is not None:
        cmd += ["--lufs", str(lufs)]
    if tp is not None:
        cmd += ["--tp", str(tp)]
    if width is not None:
        cmd += ["--width", str(width)]
    if mono_bass is not None:
        cmd += ["--mono_bass", str(mono_bass)]
    if guardrails:
        cmd += ["--guardrails"]
    try:
        return check_output_cmd(cmd)
    except subprocess.CalledProcessError as e:
        msg = e.output or ""
        if guardrails and "unrecognized arguments: --guardrails" in msg:
            fallback_cmd = [c for c in cmd if c != "--guardrails"]
            try:
                return check_output_cmd(fallback_cmd)
            except subprocess.CalledProcessError as e2:
                raise HTTPException(status_code=500, detail=e2.output)
        raise HTTPException(status_code=500, detail=msg)
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
    base_cmd = ["python3", str(MASTER_SCRIPT), "--infile", str(safe_in.name), "--strength", str(strength)]
    if presets:
        base_cmd += ["--presets", presets]
    if lufs is not None:
        base_cmd += ["--lufs", str(lufs)]
    if tp is not None:
        base_cmd += ["--tp", str(tp)]
    if width is not None:
        base_cmd += ["--width", str(width)]
    if mono_bass is not None:
        base_cmd += ["--mono_bass", str(mono_bass)]
    if guardrails:
        base_cmd += ["--guardrails"]
    if voicing_mode:
        base_cmd += ["--voicing_mode", voicing_mode]
    if voicing_name:
        base_cmd += ["--voicing_name", voicing_name]
    base_cmd += ["--out_wav", "1" if out_wav else "0"]
    base_cmd += ["--out_mp3", "1" if out_mp3 else "0"]
    base_cmd += ["--out_aac", "1" if out_aac else "0"]
    base_cmd += ["--out_ogg", "1" if out_ogg else "0"]
    base_cmd += ["--out_flac", "1" if out_flac else "0"]
    if wav_bit_depth: base_cmd += ["--wav_bit_depth", str(wav_bit_depth)]
    if wav_sample_rate: base_cmd += ["--wav_sample_rate", str(wav_sample_rate)]
    if mp3_bitrate: base_cmd += ["--mp3_bitrate", str(mp3_bitrate)]
    if mp3_vbr: base_cmd += ["--mp3_vbr", str(mp3_vbr)]
    if aac_bitrate: base_cmd += ["--aac_bitrate", str(aac_bitrate)]
    if aac_codec: base_cmd += ["--aac_codec", str(aac_codec)]
    if aac_container: base_cmd += ["--aac_container", str(aac_container)]
    if ogg_quality: base_cmd += ["--ogg_quality", str(ogg_quality)]
    if flac_level: base_cmd += ["--flac_level", str(flac_level)]
    if flac_bit_depth: base_cmd += ["--flac_bit_depth", str(flac_bit_depth)]
    if flac_sample_rate: base_cmd += ["--flac_sample_rate", str(flac_sample_rate)]
    def run_pack():
        try:
            check_output_cmd(base_cmd)
        except subprocess.CalledProcessError as e:
            # Log to stderr; UI will refresh Previous Runs anyway.
            print(f"[master-pack] failed: {e.output or e}", file=sys.stderr)
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
    voicing = (body.get("voicing") or "universal").strip().lower()
    strength = body.get("strength", 0)
    width = body.get("width", None)
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

    if voicing not in {"universal", "airlift", "ember", "detail", "glue", "wide", "cinematic", "punch"}:
        raise HTTPException(status_code=400, detail="invalid_voicing")
    safe_in = _validate_input_file(song)
    session_key = _preview_session_key(request)
    preview_id = uuid.uuid4().hex
    params_raw = f"{song}|{voicing}|{strength_val}|{width}"
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
            "strength": strength_val,
            "width": width,
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
