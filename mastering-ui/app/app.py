import json
import shutil
import subprocess
import shlex
import re
import threading
import sys
import tempfile
import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from datetime import datetime
import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Body, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse, Response
from tagger import TaggerService
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MASTER_IN_DIR = Path(os.getenv("IN_DIR", os.getenv("MASTER_IN_DIR", str(DATA_DIR / "mastering" / "in"))))
MASTER_OUT_DIR = Path(os.getenv("OUT_DIR", os.getenv("MASTER_OUT_DIR", str(DATA_DIR / "mastering" / "out"))))
MASTER_TMP_DIR = Path(os.getenv("MASTER_TMP_DIR", str(DATA_DIR / "mastering" / "tmp")))
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
# New UI (sonustemper-ui) support; resolve both repo layout and container layout
UI_CANDIDATES = [
    APP_DIR / "sonustemper-ui" / "app",              # in-container after COPY sonustemper-ui/app -> /app/sonustemper-ui/app
    APP_DIR.parent / "sonustemper-ui" / "app",        # local dev: mastering-ui/app/... one level up
    APP_DIR.parent.parent / "sonustemper-ui" / "app", # local dev from repo root
]
UI_APP_DIR = None
for cand in UI_CANDIDATES:
    if cand.exists():
        UI_APP_DIR = cand
        sys.path.append(str(cand))
        break
# Security: API key protection (for CLI/scripts); set API_AUTH_DISABLED=1 to bypass explicitly.
API_KEY = os.getenv("API_KEY")
API_AUTH_DISABLED = os.getenv("API_AUTH_DISABLED") == "1"
PROXY_SHARED_SECRET = (os.getenv("PROXY_SHARED_SECRET", "") or "").strip()
# Basic logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("mastering-ui")
# Surface configured log level on startup to aid debugging
logger.info(
    "[startup] LOG_LEVEL=%s EVENT_LOG_LEVEL=%s",
    os.getenv("LOG_LEVEL", "error"),
    os.getenv("EVENT_LOG_LEVEL", os.getenv("LOG_LEVEL", "error")),
)
# New UI import (after sys.path update)
new_ui_router = None
logger.info("[startup] UI_APP_DIR=%s exists=%s", UI_APP_DIR, bool(UI_APP_DIR and UI_APP_DIR.exists()))
try:
    from ui import router as new_ui_router  # type: ignore
    logger.info("[startup] new UI router import success")
except Exception as exc:
    logger.warning("[startup] new UI router import failed: %s", exc)
# Trusted proxy check via shared secret (raw)
def is_trusted_proxy(mark: str) -> bool:
    return bool(mark) and bool(PROXY_SHARED_SECRET) and (mark == PROXY_SHARED_SECRET)
# Deprecated: master.py retained only as a fallback reference; master_pack.py is the unified runner.
_default_master = APP_DIR / "mastering" / "master.py"
_default_pack = APP_DIR / "mastering" / "master_pack.py"
if not _default_pack.exists():
    try:
        _default_pack = APP_DIR.parents[2] / "mastering" / "master_pack.py"
        _default_master = APP_DIR.parents[2] / "mastering" / "master.py"
    except Exception:
        pass
# Use master_pack.py as the unified mastering script (handles single or multiple presets/files)
MASTER_SCRIPT = Path(os.getenv("MASTER_SCRIPT", str(_default_pack)))
app = FastAPI()
for p in [
    MASTER_IN_DIR,
    MASTER_OUT_DIR,
    MASTER_TMP_DIR,
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
# Mount new UI static assets if present
UI_STATIC_DIR = UI_APP_DIR / "static" if UI_APP_DIR and UI_APP_DIR.exists() else None
if UI_STATIC_DIR and UI_STATIC_DIR.exists():
    app.mount("/ui/static", StaticFiles(directory=str(UI_STATIC_DIR)), name="ui-static")
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
HTML_TEMPLATE = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="icon" type="image/x-icon" href="/favicon.ico">
  <title>SonusTemper</title>
  <style>
    :root{
      --bg:#0b0f14; --card:#121a23; --muted:#9fb0c0; --text:#e7eef6;
      --line:#203042; --accent:#ff8a3d; --accent2:#2bd4bd; --danger:#ff4d4d;
    }
    body{ margin:0; background:linear-gradient(180deg,#0b0f14,#070a0e); color:var(--text);
      font-family:-apple-system,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
    .wrap{ max-width:1200px; margin:0 auto; padding:26px 18px 40px; position:relative; }
    .top{ display:flex; gap:14px; align-items:flex-end; justify-content:space-between; flex-wrap:wrap; }
    h1{ font-size:20px; margin:0; letter-spacing:.2px; }
    .sub{ color:var(--muted); font-size:13px; margin-top:6px; }
    .grid{ display:grid; grid-template-columns: 1fr 1.2fr; gap:14px; margin-top:16px; }
    @media (max-width: 980px){ .grid{ grid-template-columns:1fr; } }
    .card{ background:rgba(18,26,35,.9); border:1px solid var(--line); border-radius:16px; padding:16px; box-sizing:border-box; }
    .card h2{ font-size:14px; margin:0 0 12px 0; color:#cfe0f1; }
    .row{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
    label{ color:#cfe0f1; font-size:13px; font-weight:600; }
    select,input[type="range"],input[type="file"],button{
      border-radius:12px; border:1px solid var(--line); background:#0f151d; color:var(--text);
      padding:10px 12px; font-size:14px;
    }
    select{ min-width:260px; }
    button{ cursor:pointer; }
    .btn{ background:linear-gradient(180deg, rgba(255,138,61,.95), rgba(255,138,61,.75));
      border:0; color:#1a0f07; font-weight:800; }
    .btn2{ background:linear-gradient(180deg, rgba(43,212,189,.95), rgba(43,212,189,.75));
      border:0; color:#05110f; font-weight:800; }
    .btnGhost{ background:#0f151d; }
    .btnDanger{ background:rgba(255,77,77,.15); border:1px solid rgba(255,77,77,.35); color:#ffd0d0; }
    .pill{ font-size:12px; color:var(--muted); border:1px solid var(--line); border-radius:999px; padding:6px 10px; }
    .mono{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size:12px; color:#cfe0f1; }
    .hr{ height:1px; background:var(--line); margin:12px 0; }
    .result{ white-space:pre-wrap; background:#0f151d; border:1px solid var(--line);
      border-radius:14px; padding:12px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size:12px; color:#d7e6f5; min-height:48px; }
    .spinner{ display:inline-flex; align-items:center; gap:8px; }
    .spinner:before{ content:\"\"; width:14px; height:14px; border:2px solid var(--line); border-top-color: var(--accent); border-radius:50%; display:inline-block; animation: spin 0.9s linear infinite; }
    @keyframes spin{ to { transform: rotate(360deg); } }
    .links a{ color: #ffd3b3; text-decoration:none; }
    .links a:hover{ text-decoration:underline; }
    .outlist{ margin-top:10px; display:flex; flex-direction:column; gap:10px; }
    .outitem{ padding:10px; border:1px solid var(--line); border-radius:14px; background:#0f151d; }
    audio{ width:100%; margin-top:8px; }
    .small{ color:var(--muted); font-size:12px; }
    .toggle{ display:flex; gap:8px; align-items:center; }
    input[type="checkbox"]{ transform: scale(1.15); }
    .twoCol{ display:grid; grid-template-columns: 1fr 1fr; gap:14px; }
    @media (max-width: 980px){ .twoCol{ grid-template-columns:1fr; } }
    .runRow{ display:flex; justify-content:space-between; gap:10px; align-items:center; }
    .runLeft{ display:flex; flex-direction:column; gap:4px; }
    .runBtns{ display:flex; gap:8px; }
    .linkish{ color:#ffd3b3; text-decoration:none; }
    .linkish:hover{ text-decoration:underline; }
    .hidden{ display:none !important; }
    .footer{ margin-top:18px; text-align:center; font-size:12px; opacity:.75; }
    .manage-wrap{ padding:14px; border:1px solid var(--line); border-radius:14px; background:#0b121d; margin-top:10px; }
    .manage-list{ display:flex; flex-direction:column; gap:8px; }
    .manage-item{ display:flex; justify-content:space-between; align-items:center; padding:8px 10px; border:1px solid var(--line); border-radius:10px; }
    .smallBtn{ padding:6px 10px; font-size:12px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; cursor:pointer; }
  
/* Pipeline sections */
.pipeWrap{ display:flex; flex-direction:column; gap:10px; margin-top:6px; }
.pipeSection{ border:1px solid rgba(255,255,255,.08); border-radius:16px; padding:10px 12px; background: rgba(0,0,0,.10); }
.pipeHeader{ display:flex; align-items:center; gap:10px; font-weight:600; cursor:pointer; user-select:none; }
.pipeHeader input{ transform: translateY(1px); }
.pipeBody{ margin-top:10px; }
.pipeBodyCollapsed{ display:none; }
.pipeSection.disabled{ opacity:.6; }

/* --- Metrics: wrapping chips + Advanced toggle --- */
.metricsGrid{ display:flex; flex-wrap:wrap; gap:10px 12px; }
.metricChip{ flex:1 1 150px; min-width:150px; border:1px solid var(--line); border-radius:12px;
  padding:10px; background:rgba(10,16,22,.45); }
.metricTitle{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }
.metricTitle .label{ font-size:12px; color:var(--muted); }
.metricLines{ display:flex; flex-direction:column; gap:4px; }
.metricLine{ display:flex; align-items:baseline; gap:8px; }
.metricTag{ font-size:11px; color:var(--muted); min-width:26px; }
.metricVal{ font-size:13px; }
.metricDelta{ font-size:11px; color:var(--muted); margin-left:auto; }
.advToggle{ display:flex; align-items:center; gap:10px; margin:10px 0 2px; }
.advToggle button{ background:transparent; border:1px solid var(--line); color:var(--text); border-radius:10px; padding:6px 10px; }
.advHidden{display:none !important;}
.tagRowTitle{ font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:#e7eef6; }
  .badgeRow{ display:flex; gap:6px; align-items:center; white-space:nowrap; overflow:hidden; margin-top:6px; width:100%; }
.badge{ font-size:11px; padding:4px 8px; border-radius:999px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; }
.badge-voicing{ background:rgba(255,138,61,0.2); border-color:rgba(255,138,61,0.6); color:#ffb07a; }
.badge-param{ background:rgba(43,212,189,0.15); border-color:rgba(43,212,189,0.45); color:#9df1e5; }
.badge-format{ background:rgba(255,255,255,0.04); border-color:rgba(255,255,255,0.15); color:#cfe0f1; }
.badge-container{ background:rgba(255,255,255,0.02); border-color:rgba(255,255,255,0.12); color:#9fb0c0; }
.outHeader{ display:flex; align-items:flex-start; gap:8px; min-width:0; }
.outTitle{ font-weight:700; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:0 1 auto; color:#e7eef6; }
.outHeader .badgeRow{ width:auto; flex:1 1 auto; min-width:0; justify-content:flex-end; margin-top:0; }


/* --- Metrics compact overrides --- */
.metricsGrid{ display:grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap:8px 10px; }
.metricChip{ padding:8px 10px; border-radius:12px; }
.metricTitle .label{ font-size:11px; letter-spacing:.2px; }
.metricVal{ font-size:12px; }
.metricTag{ font-size:10px; min-width:22px; }
.metricDelta{ font-size:10px; }

/* advHidden specificity */
.metricsGrid.advHidden{display:none !important;}

</style>
<style>
/* --- Mastering UI control rows --- */
.control-row{
  display:flex;
  align-items:center;
  gap:12px;
  width:100%;
  margin-top:10px;
  flex-wrap:nowrap;
}
.formatRow{
  display:flex;
  flex-wrap:wrap;
  gap:12px;
  align-items:center;
  margin-top:8px;
}
.selectRow{
  display:grid;
  grid-template-columns: 140px 1fr 120px;
  align-items:flex-start;
  gap:12px;
}
.selectActions{
  display:flex;
  flex-direction:column;
  gap:6px;
  min-width:120px;
}
.placeholderBtn{ visibility:hidden; display:block; height:32px; }
.control-row label{
  min-width:220px;
  display:flex;
  align-items:center;
  gap:8px;
}
.control-row input[type="range"]{
  flex:1;
  min-width:260px;
}
.control-row .pill{
  min-width:96px;
  text-align:center;
}
@media (max-width: 900px){
  .control-row{
    flex-wrap:wrap;
  }
  .control-row label{
    min-width:100%;
  }
  .control-row input[type="range"]{
    min-width:100%;
  }
}
</style>
<style>
/* --- Responsive layout fix (force on-screen) --- */
html, body{
  width:100%;
  max-width:100%;
  overflow-x:hidden;
}
body{
  margin:0;
}
/* Make the main wrapper fluid */
.wrap{
  width:100% !important;
  max-width:1400px;
  margin:0 auto;
  padding:16px;
  box-sizing:border-box;
}
/* Grid: 2 columns on wide screens, 1 column on small */
.grid{
  display:grid !important;
  grid-template-columns: 360px 1fr;
  gap:16px;
}
@media (max-width: 1000px){
  .grid{
    grid-template-columns: 1fr;
  }
}
/* Cards should not force overflow */
.card{
  min-width:0 !important;
}
/* Control rows should wrap and never push past viewport */
.control-row{
  min-width:0 !important;
  flex-wrap:wrap;
}
.control-row label{
  min-width:180px;
}
@media (max-width: 700px){
  .control-row label{
    min-width:100%;
  }
}
/* Make sliders behave */
input[type="range"]{
  min-width: 180px !important;
  max-width: 100%;
}
/* Make selects fluid */
    select, input[type="text"], input[type="file"]{
      max-width:100%;
      min-width:0 !important;
    }
  </style>
<style>
.drawer-backdrop{
  position:fixed; inset:0; background:rgba(0,0,0,0.35); backdrop-filter: blur(2px);
  z-index:999; transition: opacity .2s ease;
}
.info-drawer{
  position:fixed; top:0; right:0; width:420px; max-width:90vw; height:100%;
  background:#0f151d; border-left:1px solid var(--line); box-shadow: -6px 0 18px rgba(0,0,0,0.35);
  z-index:1000; transform: translateX(100%); transition: transform .25s ease;
  display:flex; flex-direction:column; padding:16px;
}
@media (max-width: 768px){
  .info-drawer{ width:100%; height:65vh; top:auto; bottom:0; border-left:0; border-top:1px solid var(--line); transform: translateY(100%); }
}
.info-drawer.open{ transform: translateX(0); }
@media (max-width: 768px){
  .info-drawer.open{ transform: translateY(0); }
}
.drawer-header{ display:flex; justify-content:space-between; align-items:center; gap:10px; }
.drawer-header h2{ margin:0; font-size:16px; color:#e7eef6; }
.drawer-subtitle{ color:var(--muted); font-size:12px; }
.drawer-body{ margin-top:12px; overflow:auto; padding-right:6px; display:flex; flex-direction:column; gap:10px; }
.drawer-section h3{ margin:0 0 6px 0; font-size:13px; color:#cfe0f1; }
.drawer-section ul{ margin:0; padding-left:18px; color:#d7e6f5; font-size:12px; }
.drawer-section .chips{ display:flex; flex-wrap:wrap; gap:6px; }
.drawer-section .chip{ padding:6px 10px; border:1px solid var(--line); border-radius:12px; font-size:12px; color:#d7e6f5; background:#0f151d; }
.hidden{ display:none !important; }
.info-btn{
  border:1px solid var(--line);
  background:#0f151d;
  color:var(--muted);
  border-radius:50%;
  width:22px; height:22px;
  display:inline-flex; align-items:center; justify-content:center;
  cursor:pointer;
  font-size:12px;
}
.info-btn:hover{ color:var(--text); border-color:var(--accent); }
.section-gap{
  border:0;
  height:20px;
  margin:26px 0 12px 0;
  padding:0;
  background: transparent;
}
.section-gap.strong{
  border:0;
  height:26px;
  margin:30px 0 18px 0;
  padding:0;
  background: transparent;
}
.section-title{ margin:0 0 6px 0; font-size:14px; color:#cfe0f1; }
.ioRow{
  display:flex;
  flex-direction:column;
  gap:2px;
  font-size:12px;
  margin:4px 0;
  color:#d7e6f5;
}
.ioRow .label{ opacity:.75; }
.ioTable{
  width:100%;
  border-collapse:collapse;
  font-size:12px;
  margin:6px 0 4px 0;
}
.ioTable th, .ioTable td{
  padding:2px 6px;
  text-align:left;
  white-space:nowrap;
}
.ioTable th{ opacity:.75; }
.ioTable td{ opacity:.9; }
.progressWrap{margin-top:8px; height:10px; background:rgba(255,255,255,0.08); border:1px solid var(--line); border-radius:999px; overflow:hidden; display:none;}
.progressBar{height:100%; width:0%; background:linear-gradient(90deg, var(--accent), #9ef4ff);}
/* Utilities menu */
.utilMenu{ position:relative; }
.utilToggle{ padding:8px 12px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; cursor:pointer; }
.utilToggle:hover{ border-color:var(--accent); color:var(--text); }
.utilDropdown{
  position:absolute; right:0; top:calc(100% + 6px);
  background:#0f151d; border:1px solid var(--line); border-radius:10px;
  min-width:160px; z-index:50; box-shadow:0 8px 22px rgba(0,0,0,0.35);
  display:flex; flex-direction:column; overflow:hidden;
}
.utilDropdown a{
  padding:10px 12px; color:#d7e6f5; text-decoration:none; font-size:13px;
}
.utilDropdown a:hover{ background:rgba(255,138,61,0.12); color:var(--text); }
.utilDropdown.hidden{ display:none; }
</style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1>SonusTemper</h1>
        <div class="sub">Preset-Based Mastering &amp; Normalization</div>
      </div>
      <div class="utilMenu">
        <button class="utilToggle" id="utilToggleMain" aria-haspopup="true" aria-expanded="false">☰ Utilities</button>
        <div class="utilDropdown hidden" id="utilDropdownMain">
          <a href="/">Mastering</a>
          <div style="height:1px; background:var(--line); margin:4px 0;"></div>
          <a href="/manage-files">File Manager</a>
          <a href="/manage-presets">Preset Manager</a>
          <a href="/tagger">Tag Editor</a>
        </div>
      </div>
    </div>
<div class="grid" id="masterView">
      <div class="card masterPane" id="uploadCard">
        <h2>Upload</h2>
        <form id="uploadForm">
          <div class="row">
            <input type="file" id="file" name="files" accept=".wav,.mp3,.flac,.aiff,.aif" multiple style="display:none" />
            <button class="btn2" type="button" id="uploadBtn" onclick="triggerUpload()">Upload files</button>
            <button class="btnGhost" type="button" onclick="window.location.href='/manage-files'">Manage Files</button>
          </div>
        </form>
        <div id="uploadResult" class="small" style="margin-top:10px;"></div>
        <div class="section-gap"></div>
        <h3 class="section-title">Processing Status</h3>
        <div id="result" class="result">(waiting)</div>
        <div id="progressWrap" class="progressWrap"><div id="progressBar" class="progressBar"></div></div>
      </div>
      <div class="card masterPane">
        <h2>Master</h2>
        <div class="pipeWrap">
          <!-- Inputs -->
          <div class="pipeSection">
            <div class="pipeHeader">
              <span>Inputs</span>
            </div>
            <div class="pipeBody" data-stage="stage_inputs">
              <div class="hidden"><select id="infile"></select></div>
              <div class="control-row" style="align-items:flex-start; margin-top:6px;">
                <label style="min-width:140px;">Input files</label>
                <div id="bulkFilesBox" class="small" style="flex:1; display:flex; flex-wrap:wrap; gap:8px;"></div>
                <div class="small" style="display:flex; flex-direction:column; gap:6px;">
                  <button class="btnGhost" type="button" onclick="selectAllBulk()">Select all</button>
                  <button class="btnGhost" type="button" onclick="clearAllBulk()">Clear</button>
                </div>
              </div>
            </div>
          </div>

          <!-- Analyze -->
          <div class="pipeSection">
            <label class="pipeHeader">
              <input type="checkbox" id="stage_analyze" checked>
              <span>Analyze</span>
            </label>
            <div class="pipeBody" data-stage="stage_analyze">
              <div class="small" style="color:var(--muted); line-height:1.35;">
                Computes loudness, true-peak, width, and additional stats used for comparison. (This runs by default today.)
              </div>
            </div>
          </div>

          <!-- Master: presets + strength -->
          <div class="pipeSection">
            <label class="pipeHeader">
              <input type="checkbox" id="stage_master" checked>
              <span>Voicings and User Presets</span>
            </label>
            <div class="pipeBody" data-stage="stage_master">
              <div class="control-row" style="margin-top:4px;">
                <label style="min-width:140px;">Mode</label>
                <div class="small" style="display:flex; gap:10px; align-items:center;">
                  <label style="display:flex; align-items:center; gap:6px;"><input type="radio" name="modePresetVoicing" value="voicing" checked> Voicing</label>
                  <label style="display:flex; align-items:center; gap:6px;"><input type="radio" name="modePresetVoicing" value="presets"> User Presets</label>
                </div>
              </div>
              <div class="selectRow" id="voicingRow" style="margin-top:8px;">
                <label style="min-width:140px;">Voicing</label>
                <div id="voicingBox" class="small" style="display:flex; flex-wrap:wrap; gap:8px; min-height:96px;"></div>
                <div class="small selectActions">
                  <button class="btnGhost" type="button" onclick="clearVoicing()">Clear</button>
                  <span class="placeholderBtn">&nbsp;</span>
                </div>
              </div>
              <div class="selectRow" id="presetRow" style="margin-top:8px; display:none;">
                <label style="min-width:140px;">User Presets</label>
                <div id="packPresetsBox" class="small" style="display:flex; flex-wrap:wrap; gap:8px; min-height:96px;"></div>
                <div id="presetControls" class="small selectActions">
                  <button class="btnGhost" type="button" onclick="window.location.href='/manage-presets'">Manage Presets</button>
                  <button class="btnGhost" type="button" onclick="clearAllPackPresets()">Clear</button>
                </div>
              </div>

              <div class="control-row">
                <label><span id="strengthLabel">Strength</span></label>
                <input type="range" id="strength" min="0" max="100" value="80" oninput="strengthVal.textContent=this.value">
                <span class="pill">S=<span id="strengthVal">80</span></span>
              </div>
              <div class="small" id="presetNote" style="color:var(--muted); display:none; margin-top:4px; text-align:center; width:100%;">
                Presets are user-customization from the <code>presets</code> directory.
              </div>
            </div>
          </div>

          <!-- Loudness / Normalize -->
          <div class="pipeSection">
            <label class="pipeHeader">
              <input type="checkbox" id="stage_loudness" checked>
              <span>Loudness & Normalize</span>
            </label>
            <div class="pipeBody" data-stage="stage_loudness">
              <div class="control-row">
                <label>Loudness Mode</label>
                <div style="display:flex; align-items:center; gap:8px;">
                  <select id="loudnessMode"></select>
                  <button class="info-btn" data-info-type="loudness" data-id="loudness" title="About loudness mode" aria-label="About loudness mode">ⓘ</button>
                </div>
              </div>

              <div class="control-row">
                <label><input type="checkbox" id="ov_target_I"> Override Target LUFS</label>
                <input type="range" id="target_I" min="-22" max="-8" step="0.5" value="-16.0" oninput="targetIVal.textContent=this.value">
                <span class="pill"><span id="targetIVal">-16.0</span> LUFS</span>
              </div>

              <div class="control-row">
                <label><input type="checkbox" id="ov_target_TP"> Override True Peak (TP)</label>
                <input type="range" id="target_TP" min="-3.0" max="0.0" step="0.1" value="-1.0" oninput="targetTPVal.textContent=this.value">
                <span class="pill"><span id="targetTPVal">-1.0</span> dBTP</span>
              </div>
            </div>
          </div>

          <!-- Stereo / Tone -->
          <div class="pipeSection">
            <label class="pipeHeader">
              <input type="checkbox" id="stage_stereo" checked>
              <span>Stereo & Tone</span>
            </label>
            <div class="pipeBody" data-stage="stage_stereo">
              <div class="control-row">
                <label><input type="checkbox" id="ov_width"> Override Stereo Width</label>
                <div style="flex:1; display:flex; align-items:center; gap:10px;">
                  <input type="range" id="width" min="0.70" max="1.40" step="0.01" value="1.12" oninput="widthVal.textContent=this.value">
                  <span class="pill" id="widthVal">1.12</span>
                </div>
              </div>

              <div class="control-row">
                <label><input type="checkbox" id="guardrails"> Enable Width Guardrails</label>
                <div class="small" style="color:var(--muted);">Keeps lows mono-ish and softly caps extreme width if risky.</div>
              </div>

              <div class="control-row">
                <label><input type="checkbox" id="ov_mono_bass"> Mono Bass Below (Hz)</label>
                <input type="range" id="mono_bass" min="60" max="200" step="5" value="120" oninput="monoBassVal.textContent=this.value">
                <span class="pill" id="monoBassVal">120</span>
              </div>
            </div>
          </div>

          <!-- Output -->
          <div class="pipeSection">
            <label class="pipeHeader">
              <input type="checkbox" id="stage_output" checked>
              <span>Output</span>
            </label>
            <div class="pipeBody" data-stage="stage_output">
              <div class="formatRow">
                <label class="small"><input type="checkbox" id="out_wav" checked> WAV</label>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">Bit depth</span>
                  <select id="wav_bit_depth">
                    <option value="16">16-bit</option>
                    <option value="24" selected>24-bit</option>
                    <option value="32">32-bit</option>
                  </select>
                </div>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">Sample rate</span>
                  <select id="wav_sample_rate">
                    <option value="44100">44.1 kHz</option>
                    <option value="48000" selected>48 kHz</option>
                    <option value="96000">96 kHz</option>
                  </select>
                </div>
              </div>
              <div class="formatRow">
                <label class="small"><input type="checkbox" id="out_mp3"> MP3</label>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">Bitrate</span>
                  <select id="mp3_bitrate">
                    <option value="192">192 kbps</option>
                    <option value="256">256 kbps</option>
                    <option value="320" selected>320 kbps</option>
                  </select>
                </div>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">VBR</span>
                  <select id="mp3_vbr">
                    <option value="none" selected>None (CBR)</option>
                    <option value="V0">V0</option>
                    <option value="V2">V2</option>
                  </select>
                </div>
              </div>
              <div class="formatRow">
                <label class="small"><input type="checkbox" id="out_aac"> AAC / M4A</label>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">Codec</span>
                  <select id="aac_codec">
                    <option value="aac" selected>AAC (native)</option>
                  </select>
                </div>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">Container</span>
                  <select id="aac_container">
                    <option value="m4a" selected>M4A</option>
                    <option value="aac">AAC</option>
                  </select>
                </div>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">Bitrate</span>
                  <select id="aac_bitrate">
                    <option value="128">128 kbps</option>
                    <option value="192">192 kbps</option>
                    <option value="256" selected>256 kbps</option>
                    <option value="320">320 kbps</option>
                  </select>
                </div>
              </div>
              <div class="formatRow">
                <label class="small"><input type="checkbox" id="out_ogg"> OGG Vorbis</label>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">Quality</span>
                  <select id="ogg_quality">
                    <option value="3">Q3 (~112 kbps)</option>
                    <option value="5" selected>Q5 (~160 kbps)</option>
                    <option value="7">Q7 (~224 kbps)</option>
                    <option value="9">Q9 (~320 kbps)</option>
                  </select>
                </div>
              </div>
              <div class="formatRow">
                <label class="small"><input type="checkbox" id="out_flac"> FLAC</label>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">Level</span>
                  <select id="flac_level">
                    <option value="0">0 (fastest)</option>
                    <option value="5" selected>5</option>
                    <option value="8">8 (smallest)</option>
                  </select>
                </div>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">Bit depth</span>
                  <select id="flac_bit_depth">
                    <option value="">Auto</option>
                    <option value="16">16-bit</option>
                    <option value="24" selected>24-bit</option>
                  </select>
                </div>
                <div class="small" style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--muted);">Sample rate</span>
                  <select id="flac_sample_rate">
                    <option value="">Auto</option>
                    <option value="44100">44.1 kHz</option>
                    <option value="48000">48 kHz</option>
                    <option value="96000">96 kHz</option>
                  </select>
                </div>
              </div>
            </div>
          </div>
        </div>
      <div class="row" style="margin-top:12px;">
          <button class="btn" id="runPackBtn" onclick="runPack()">Run Job</button>
        </div>
      </div>
      <div class="card masterPane" id="recentCard">
        <h2>Previous Runs</h2>
        <div class="small">Click a run to load outputs. Delete removes the entire song output folder.</div>
        <div id="recent" class="outlist" style="margin-top:10px;"></div>
      </div>
      <div class="card masterPane">
        <h2>Job Output</h2>
        <div id="outlist" class="outlist"></div>
      </div>
      <div class="card hidden" id="manageView">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <h2>Manage uploads & runs</h2>
          <button class="btnGhost" type="button" onclick="showMaster()">Return to Mastering</button>
        </div>
        <div class="manage-wrap">
          <h3 style="margin:0 0 8px 0;">Uploaded files</h3>
          <div id="manageUploads" class="manage-list small"></div>
        </div>
        <div class="manage-wrap">
          <h3 style="margin:0 0 8px 0;">Runs</h3>
          <div id="manageRuns" class="manage-list small"></div>
        </div>
      </div>
    </div>
    <div class="footer">SonusTemper v{{VERSION}} – developed by <a class="linkish" href="http://www.jasondaemon.net">jasondaemon.net</a></div>
  </div>
<script>
function setStatus(msg) {
  const el = document.getElementById('statusMsg');
  if (el) el.textContent = msg;
}
function setupUtilMenu(toggleId, menuId){
  const toggle = document.getElementById(toggleId);
  const menu = document.getElementById(menuId);
  if(!toggle || !menu) return;
  const close = ()=>{ menu.classList.add('hidden'); toggle.setAttribute('aria-expanded','false'); };
  toggle.addEventListener('click', (e)=>{
    e.stopPropagation();
    const isOpen = !menu.classList.contains('hidden');
    if(isOpen){ close(); } else { menu.classList.remove('hidden'); toggle.setAttribute('aria-expanded','true'); }
  });
  document.addEventListener('click', (e)=>{
    if(!menu.contains(e.target) && e.target !== toggle){ close(); }
  });
}
const LOUDNESS_MODES = {
  apple: { label: "Apple Music", lufs: -16.0, tp: -1.0, hint: "Target -16 LUFS / -1.0 dBTP" },
  streaming: { label: "Streaming Safe", lufs: -14.0, tp: -1.0, hint: "Target -14 LUFS / -1.0 dBTP" },
  loud: { label: "Loud", lufs: -9.0, tp: -0.8, hint: "Target -9 LUFS / -0.8 dBTP" },
  manual: { label: "Manual", hint: "Use LUFS/TP sliders (optional)" },
};
const LOUDNESS_MODE_KEY = "loudnessMode";
const LOUDNESS_MANUAL_KEY = "loudnessManualValues";
const LOUDNESS_ORDER = ["apple", "streaming", "loud", "manual"];
const GUARDRAILS_KEY = "widthGuardrailsEnabled";
const PACK_PRESETS_KEY = "packPresets";
const VOICING_MODE_KEY = "voicingMode";
const VOICING_SELECTED_KEY = "voicingSelected";
const BULK_FILES_KEY = "bulkFilesSelected";
let suppressRecentDuringRun = false;
let lastRunInputMetrics = null;
let runPollPrimary = null;
let runPollActive = false;
const pendingMetricsRetry = new Set();
const metricsRetryCount = new Map();
let statusStream = null;
let statusEntries = [];
// Badge rendering (shared with Tag Editor)
const TAG_BADGE_GAP = 6;
let badgeMeasureHost = null;
function ensureBadgeMeasureHost(){
  if(badgeMeasureHost) return badgeMeasureHost;
  const host = document.createElement('div');
  host.style.position = 'absolute';
  host.style.visibility = 'hidden';
  host.style.pointerEvents = 'none';
  host.style.top = '-9999px';
  host.style.left = '-9999px';
  host.style.display = 'flex';
  host.style.gap = `${TAG_BADGE_GAP}px`;
  document.body.appendChild(host);
  badgeMeasureHost = host;
  return host;
}
function measureBadgeWidth(badgeEl){
  const host = ensureBadgeMeasureHost();
  host.appendChild(badgeEl);
  const w = badgeEl.offsetWidth;
  host.removeChild(badgeEl);
  return w;
}
function makeBadge(label, type, title){
  const span = document.createElement('span');
  span.className = 'badge' + (type ? ` badge-${type}` : '');
  span.textContent = label;
  if(title) span.title = title;
  return span;
}
function badgeTitle(text, tooltip){
  const div = document.createElement('div');
  div.className = 'tagRowTitle';
  div.textContent = text || '';
  if(tooltip) div.title = tooltip;
  return div;
}
function computeVisibleBadges(badges, containerWidth){
  if(!badges || !badges.length || !containerWidth) return { visible: badges || [], hidden: [] };
  const pinned = [];
  const seenPinned = new Set();
  badges.forEach(b=>{
    if(b && (b.type === 'voicing' || b.type === 'preset')){
      const key = `${b.type}:${b.label}`;
      if(!seenPinned.has(key)){
        pinned.push(b);
        seenPinned.add(key);
      }
    }
  });
  const rest = badges.filter(b=>!(b && (b.type === 'voicing' || b.type === 'preset')));
  const ordered = [...pinned, ...rest];
  if(!ordered.length) return { visible: [], hidden: [] };

  const widths = ordered.map(b => {
    const el = makeBadge(b.label || '', b.type || '', b.title);
    return measureBadgeWidth(el);
  });
  const totalWidth = widths.reduce((acc,w,idx)=> acc + w + (idx>0 ? TAG_BADGE_GAP : 0), 0);
  if(totalWidth <= containerWidth){
    return { visible: ordered, hidden: [] };
  }
  const reserveBadge = makeBadge("+99", "format");
  const reserveWidth = measureBadgeWidth(reserveBadge);
  const available = Math.max(0, containerWidth - reserveWidth - TAG_BADGE_GAP);
  let used = 0;
  const visible = [];
  let hiddenStart = ordered.length;
  for(let i=0;i<ordered.length;i++){
    const w = widths[i] + (visible.length ? TAG_BADGE_GAP : 0);
    if(used + w <= available){
      visible.push(ordered[i]);
      used += w;
    }else{
      hiddenStart = i;
      break;
    }
  }
  const hidden = ordered.slice(hiddenStart);
  return { visible, hidden };
}
function renderBadges(badges, container){
  const wrap = container || document.createElement('div');
  wrap.className = 'badgeRow';
  wrap.innerHTML = '';
  if(!badges || !badges.length) return wrap;
  let width = wrap.parentElement ? wrap.parentElement.clientWidth : 0;
  if(!width) width = wrap.getBoundingClientRect().width || wrap.clientWidth;
  if(!width) width = 320;
  const { visible, hidden } = computeVisibleBadges(badges, width);
  const toRender = visible.length ? visible : badges;
  toRender.forEach(b => {
    const lbl = b.label || '';
    const type = b.type || '';
    wrap.appendChild(makeBadge(lbl, type, b.title));
  });
  if(hidden.length > 0){
    const more = makeBadge(`+${hidden.length}`, 'format', hidden.map(b=>b.title || b.label).join(', '));
    wrap.appendChild(more);
  }
  return wrap;
}
function layoutBadgeRows(){
  const rows = document.querySelectorAll('.badgeRow');
  rows.forEach(br=>{
    let badges = [];
    try { badges = JSON.parse(br.dataset.badges || '[]'); } catch {}
    renderBadges(badges, br);
  });
}
let badgeLayoutRaf = null;
function queueBadgeLayout(){
  if(badgeLayoutRaf) cancelAnimationFrame(badgeLayoutRaf);
  badgeLayoutRaf = requestAnimationFrame(()=>{ badgeLayoutRaf = null; layoutBadgeRows(); });
}
window.addEventListener('resize', queueBadgeLayout);
const mainBadgeObserver = new ResizeObserver(() => queueBadgeLayout());
document.addEventListener('DOMContentLoaded', () => {
  const out = document.getElementById('outlist');
  if(out) mainBadgeObserver.observe(out);
});
const METRIC_META = [
  { key:"I", label:"I", desc:"Integrated loudness (LUFS) averaged over the whole song. Higher (less negative) is louder; aim for musical balance, not just numbers." },
  { key:"TP", label:"TP", desc:"True Peak (dBTP) or peak dBFS if TP unavailable. Closer to 0 dBTP is louder but riskier; keep headroom for clean playback." },
  { key:"LRA", label:"LRA", desc:"Loudness Range. Shows how much the loudness moves; higher can feel more dynamic, lower can feel more consistent." },
  { key:"Peak", label:"Peak", desc:"Sample peak level (dBFS). Helpful for headroom checks." },
  { key:"RMS", label:"RMS", desc:"RMS level (dBFS). Complements LUFS by showing signal power/density." },
  { key:"DR", label:"DR", desc:"Dynamic range from astats (dB). Higher usually feels punchier/more dynamic." },
  { key:"Noise", label:"Noise", desc:"Noise floor estimate (dBFS). Useful for quiet intros/outros, spoken word, or transfers." },
  { key:"CF", label:"CF", desc:"Crest Factor (peak vs average). Higher keeps punch/transients; lower feels denser/limited." },
  { key:"Corr", label:"Corr", desc:"Stereo correlation. 1.0 is mono/coherent, 0 is wide, negative can sound phasey or hollow." },
  { key:"Dur", label:"Dur", desc:"Duration in seconds." },
  { key:"W", label:"W", desc:"Width factor applied in mastering (if present). >1 widens, <1 narrows." },
];
function setLoudnessHint(text){
  const el = document.getElementById('loudnessHint');
  if (el) el.textContent = text || '';
}
function setSliderValue(id, value){
  const el = document.getElementById(id);
  if (!el || value === undefined || value === null) return;
  el.value = value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}
function loadManualLoudness(){
  try {
    const raw = localStorage.getItem(LOUDNESS_MANUAL_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}
function saveManualLoudness(){
  const mode = getCurrentLoudnessMode();
  if (mode !== 'manual') return;
  const payload = {
    lufs: document.getElementById('lufs')?.value,
    tp: document.getElementById('tp')?.value,
    useLufs: document.getElementById('useLufs')?.checked,
    useTp: document.getElementById('useTp')?.checked,
  };
  try { localStorage.setItem(LOUDNESS_MANUAL_KEY, JSON.stringify(payload)); } catch {}
}
function getCurrentLoudnessMode(){
  const sel = document.getElementById('loudnessMode');
  if (sel && sel.value) return sel.value;
  const stored = localStorage.getItem(LOUDNESS_MODE_KEY);
  if (stored && LOUDNESS_MODES[stored]) return stored;
  return "apple";
}
function applyLoudnessMode(modeKey, { fromInit=false } = {}){
  const cfg = LOUDNESS_MODES[modeKey] || LOUDNESS_MODES.apple;
  const sel = document.getElementById('loudnessMode');
  if (sel && sel.value !== modeKey) sel.value = modeKey;
  try { localStorage.setItem(LOUDNESS_MODE_KEY, modeKey); } catch {}
    const lock = modeKey !== 'manual';
    const lufsInput = document.getElementById('target_I');
    const tpInput = document.getElementById('target_TP');
    const useLufs = document.getElementById('ov_target_I');
    const useTp = document.getElementById('ov_target_TP');
  if (lock) {
    if (lufsInput) { lufsInput.disabled = true; setSliderValue('target_I', cfg.lufs); }
    if (tpInput) { tpInput.disabled = true; setSliderValue('target_TP', cfg.tp); }
    if (useLufs) { useLufs.checked = true; useLufs.disabled = true; }
    if (useTp) { useTp.checked = true; useTp.disabled = true; }
  } else {
    if (lufsInput) lufsInput.disabled = false;
    if (tpInput) tpInput.disabled = false;
    if (useLufs) useLufs.disabled = false;
    if (useTp) useTp.disabled = false;
    const manual = loadManualLoudness();
    if (manual) {
      if (manual.lufs !== undefined && manual.lufs !== null) setSliderValue('target_I', manual.lufs);
      if (manual.tp !== undefined && manual.tp !== null) setSliderValue('target_TP', manual.tp);
      if (useLufs && typeof manual.useLufs === 'boolean') useLufs.checked = manual.useLufs;
      if (useTp && typeof manual.useTp === 'boolean') useTp.checked = manual.useTp;
    }
  }
  setLoudnessHint(cfg.hint || "");
}
function initLoudnessMode(){
  const sel = document.getElementById('loudnessMode');
  if (!sel) return;
  sel.innerHTML = '';
  LOUDNESS_ORDER.forEach(key => {
    const cfg = LOUDNESS_MODES[key];
    if (!cfg) return;
    const o = document.createElement('option');
    o.value = key;
    o.textContent = cfg.label;
    sel.appendChild(o);
  });
  const initial = getCurrentLoudnessMode();
  sel.value = LOUDNESS_MODES[initial] ? initial : "apple";
  sel.addEventListener('change', () => applyLoudnessMode(sel.value));
  applyLoudnessMode(sel.value, { fromInit: true });
}
async function refreshRecent(force=false) {
  if (suppressRecentDuringRun && !force) return;
  const el = document.getElementById('recent');
  if (!el) return;
  try {
    const r = await fetch('/api/recent?limit=30', { cache: 'no-store' });
    const data = await r.json();
    el.innerHTML = '';
    const items = (data && data.items) ? data.items : [];
    if (!items.length) {
      el.innerHTML = '<div class="small" style="opacity:.75;">No runs yet.</div>';
      return;
    }
    for (const it of items) {
      const div = document.createElement('div');
      div.className = 'outitem';
      div.innerHTML = `
        <div class="runRow">
          <div class="runLeft">
            <div class="mono" style="font-weight:600;">${it.song || it.name}</div>
          </div>
          <div class="runBtns">
            <button class="btnGhost" onclick="loadSong('${it.song}')">Load</button>
            <button class="btnDanger" onclick="deleteSong('${it.song}')">Delete</button>
          </div>
        </div>
      `;
      el.appendChild(div);
    }
    const last = localStorage.getItem("lastSong");
    if (last && items.find(x => x.song === last)) {
      // Optional auto-restore could be added here if desired.
    }
  } catch (e) {
    console.error('refreshRecent failed', e);
  }
}
function wireUI() {
  // If this shows up, JS is definitely running.
  setStatus("UI ready.");
  const bind = (chkId, sliderId, pillId, fmt=(v)=>v) => {
    const chk = document.getElementById(chkId);
    const slider = document.getElementById(sliderId);
    const pill = document.getElementById(pillId);
    if (!slider || !pill) return;
    const update = () => { pill.textContent = fmt(slider.value); };
    slider.addEventListener('input', update);
    slider.addEventListener('change', update);
    update();
    if (chk) {
      // Keep sliders usable even when override is off; checkbox only gates payload
      const syncEnabled = () => { slider.disabled = false; };
      chk.addEventListener('change', syncEnabled);
      syncEnabled();
    }
  };
  // Strength (no checkbox)
  const strength = document.getElementById('strength');
  const strengthVal = document.getElementById('strengthVal');
  if (strength && strengthVal) {
    const u = () => strengthVal.textContent = strength.value;
    strength.addEventListener('input', u);
    strength.addEventListener('change', u);
    u();
  }
  // Overrides (checkbox + slider + pill)
  bind('useLufs', 'lufs', 'lufsVal', (v)=>Number(v).toFixed(1));
  bind('useTp', 'tp', 'tpVal', (v)=>Number(v).toFixed(1));
  bind('ov_width', 'width', 'widthVal', (v)=>Number(v).toFixed(2));
  bind('ov_mono_bass', 'mono_bass', 'monoBassVal', (v)=>String(parseInt(v,10)));
  // If your IDs are different, this will silently no-op rather than crash.
  const trackManual = () => saveManualLoudness();
  const lufsInput = document.getElementById('lufs');
  const tpInput = document.getElementById('tp');
  const useLufs = document.getElementById('useLufs');
  const useTp = document.getElementById('useTp');
  [lufsInput, tpInput, useLufs, useTp].forEach(el => {
    if (el) {
      el.addEventListener('input', trackManual);
      el.addEventListener('change', trackManual);
    }
  });
  // Guardrails toggle persistence
  const guardrails = document.getElementById('guardrails');
  if (guardrails) {
    const stored = localStorage.getItem(GUARDRAILS_KEY);
    guardrails.checked = stored === null ? true : stored === '1';
    guardrails.addEventListener('change', () => {
      try { localStorage.setItem(GUARDRAILS_KEY, guardrails.checked ? '1' : '0'); } catch {}
    });
  }
}
async function refreshAll() {
  try {
    setStatus("Loading lists...");
    const r = await fetch("/api/files", { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const infileSel = document.getElementById("infile");
    const packBox = document.getElementById("packPresetsBox");
    if (infileSel) {
      const prevIn = infileSel.value;
      infileSel.innerHTML = "";
      (data.files || []).forEach(f => {
        const o = document.createElement("option");
        o.value = f;
        o.textContent = f;
        infileSel.appendChild(o);
      });
      if (prevIn && [...infileSel.options].some(o => o.value === prevIn)) infileSel.value = prevIn;
    }
    // Populate presets as checkboxes
    if (packBox) {
      packBox.innerHTML = "";
      const prevPackVal = (localStorage.getItem(PACK_PRESETS_KEY) || "").trim();
      const presets = data.presets || [];
      const havePrev = presets.includes(prevPackVal);
      presets.forEach((pr, idx) => {
        const wrap = document.createElement('label');
        wrap.style = "display:flex; align-items:center; gap:6px; padding:4px 8px; border:1px solid var(--line); border-radius:10px;";
        const checked = havePrev ? prevPackVal === pr : (idx === 0);
        wrap.innerHTML = `<input type="radio" name="presetSel" value="${pr}" ${checked ? 'checked' : ''}> <span class="mono">${pr}</span> <button class="info-btn" type="button" data-info-type="preset" data-id="${pr}" aria-label="About ${pr}">ⓘ</button>`;
        const input = wrap.querySelector('input');
        input.addEventListener('change', () => {
          if (input.checked) {
            try { localStorage.setItem(PACK_PRESETS_KEY, pr); } catch {}
          }
          updatePackButtonState();
          updateRunButtonsState();
        });
        packBox.appendChild(wrap);
      });
      if (!havePrev && presets.length) {
        try { localStorage.setItem(PACK_PRESETS_KEY, presets[0]); } catch {}
      }
      updatePackButtonState();
    }
    // Bulk files checkboxes
    const bulkBox = document.getElementById("bulkFilesBox");
    if (bulkBox) {
      bulkBox.innerHTML = "";
      const prevBulk = new Set(((localStorage.getItem(BULK_FILES_KEY) || "")).split(",").filter(Boolean));
      const files = data.files || [];
      const havePrevBulk = files.some(f => prevBulk.has(f));
      files.forEach((f, idx) => {
        const wrap = document.createElement("label");
        wrap.style = "display:flex; align-items:center; gap:6px; padding:4px 8px; border:1px solid var(--line); border-radius:10px;";
        const checked = havePrevBulk ? prevBulk.has(f) : (idx === 0);
        wrap.innerHTML = `<input type="checkbox" value="${f}" ${checked ? 'checked' : ''}> <span class="mono">${f}</span>`;
        const input = wrap.querySelector('input');
        input.addEventListener('change', () => {
          try { localStorage.setItem(BULK_FILES_KEY, getSelectedBulkFiles().join(",")); } catch {}
          updateRunButtonsState();
        });
        bulkBox.appendChild(wrap);
      });
      if (!havePrevBulk && files.length) {
        try { localStorage.setItem(BULK_FILES_KEY, files[0]); } catch {}
      }
    }
    setStatus("");
    } catch (e) {
      console.error("refreshAll failed:", e);
      setStatus("ERROR loading lists (open console)");
    }
  updateRunButtonsState();
  await refreshRecent();
}
function setVoicingMode(mode){
  const radios = document.querySelectorAll('input[name="modePresetVoicing"]');
  radios.forEach(r => { r.checked = (r.value === mode); });
  try { localStorage.setItem(VOICING_MODE_KEY, mode); } catch {}
  const presetRow = document.getElementById('presetRow');
  const presetControls = document.getElementById('presetControls');
  const voicingRow = document.getElementById('voicingRow');
  const strengthLabel = document.getElementById('strengthLabel');
   const presetNote = document.getElementById('presetNote');
  if (presetRow && presetControls) {
    presetRow.style.display = mode === 'presets' ? 'flex' : 'none';
    presetControls.style.display = mode === 'presets' ? 'flex' : 'none';
  }
  if (voicingRow) voicingRow.style.display = mode === 'voicing' ? 'flex' : 'none';
  if (strengthLabel) strengthLabel.textContent = mode === 'voicing' ? 'Intensity' : 'Strength';
   if (presetNote) presetNote.style.display = mode === 'presets' ? 'block' : 'none';
  if (mode === 'presets') {
    clearVoicing();
  } else {
    clearAllPackPresets();
    localStorage.setItem(PACK_PRESETS_KEY, "");
  }
  updateRunButtonsState();
}
function getVoicingMode(){
  try {
    const stored = localStorage.getItem(VOICING_MODE_KEY);
    if (stored === 'voicing' || stored === 'presets') return stored;
  } catch {}
  return 'voicing';
}
function selectVoicing(slug){
  const box = document.getElementById('voicingBox');
  if (!box) return;
  box.querySelectorAll('input[type=radio]').forEach(r => {
    r.checked = (r.value === slug);
  });
  try { localStorage.setItem(VOICING_SELECTED_KEY, slug || ""); } catch {}
  updateRunButtonsState();
}
function clearVoicing(){
  const box = document.getElementById('voicingBox');
  if (!box) return;
  box.querySelectorAll('input[type=radio]').forEach(r => r.checked = false);
  try { localStorage.setItem(VOICING_SELECTED_KEY, ""); } catch {}
  updateRunButtonsState();
}
function setResult(text){ const el=document.getElementById('result'); if(el) el.textContent = text || '(no output)'; }
function setResultHTML(html){ const el=document.getElementById('result'); if(el) el.innerHTML = html || ''; }
function setLinks(html){
  const el = document.getElementById('links');
  if (!el) return;
  el.innerHTML = html || '';
}
function clearOutList(){ document.getElementById('outlist').innerHTML = ''; }
function setMetricsPanel(html){
  const el = document.getElementById('metricsPanel');
  if (!el) return;
  el.innerHTML = html || '<span style="opacity:.7;">(none)</span>';
}
function startJobLog(message){
  setResultHTML(`<div id="joblog" class="mono"><div>${message || 'Processing...'}</div></div>`);
}
function appendJobLog(message){
  const el = document.getElementById('joblog');
  if (el) {
    const div = document.createElement('div');
    div.textContent = message;
    el.appendChild(div);
  } else {
    setResult(message);
  }
}
function setProgress(fraction){
  const wrap = document.getElementById('progressWrap');
  const bar = document.getElementById('progressBar');
  if (!wrap || !bar) return;
  if (fraction === null || fraction === undefined || isNaN(fraction)) {
    if (runPollActive) {
      wrap.style.display = 'block';
      bar.style.width = '5%';
    } else {
      wrap.style.display = 'none';
      bar.style.width = '0%';
    }
    return;
  }
  const pct = Math.max(0, Math.min(1, fraction)) * 100;
  wrap.style.display = 'block';
  bar.style.width = `${pct}%`;
}
function updateProgressFromEntries(entries){
  if (!entries || !entries.length) {
    setProgress(runPollActive ? 0.05 : null);
    return;
  }
  const stagesSeen = new Set(entries.map(e => e.stage).filter(Boolean));
  const complete = stagesSeen.has('complete');
  if (complete) {
    setProgress(null);
    return;
  }
  // Estimate based on known stage milestones; include outputs if present
  const ordered = ['start','preset_start','preset_done','mp3_done','aac_done','ogg_done','flac_done','metrics_start','metrics_done','playlist'];
  const total = ordered.length;
  let done = 0;
  ordered.forEach(s => { if (stagesSeen.has(s)) done += 1; });
  const fraction = total ? done / total : 0;
  setProgress(fraction);
}
function cleanResultText(t){
  const lines = (t || '').split('\n').map(l=>l.trim()).filter(l => l && !l.toLowerCase().startsWith('script:'));
  return lines.join('\n') || '(running…)';
}
function renderStatusEntries(entries){
  updateProgressFromEntries(entries || []);
  if (!entries || !entries.length) return '<div class="small" style="opacity:.7;">(waiting for status)</div>';
  const stageLabel = (s) => {
    switch (s) {
      case 'start': return 'Job started';
      case 'preset_start': return 'Preset: start';
      case 'preset_done': return 'Preset: done';
      case 'mp3_done': return 'Preview ready';
      case 'metrics_start': return 'Metrics: start';
      case 'metrics_done': return 'Metrics: done';
      case 'playlist': return 'Playlist';
      case 'complete': return 'Complete';
      default: return s || 'Step';
    }
  };
  const items = entries.map(e => {
    const ts = e.ts ? new Date(e.ts * 1000).toLocaleTimeString() : '';
    const label = stageLabel(e.stage);
    const detail = e.detail || '';
    const preset = e.preset ? ` [${e.preset}]` : '';
    return `<div class="mono" style="margin-bottom:4px;"><span style="opacity:.65;">${ts}</span> ${label}${preset}: ${detail}</div>`;
  }).join('');
  return `<div id="joblog" class="mono">${items}</div>`;
}
function selectAllPackPresets(){
  const box = document.getElementById('packPresetsBox');
  if (!box) return;
  setVoicingMode('presets');
  const radios = [...box.querySelectorAll('input[type=radio]')];
  if (radios.length) {
    radios[0].checked = true;
    try { localStorage.setItem(PACK_PRESETS_KEY, radios[0].value); } catch {}
  }
  updateRunButtonsState();
}
function clearAllPackPresets(){
  const box = document.getElementById('packPresetsBox');
  if (!box) return;
  const checks = [...box.querySelectorAll('input[type=radio]')];
  checks.forEach(c => { c.checked = false; });
  try { localStorage.setItem(PACK_PRESETS_KEY, ""); } catch {}
  updatePackButtonState();
}
function selectAllBulk(){
  const box = document.getElementById('bulkFilesBox');
  if (!box) return;
  box.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = true);
  try { localStorage.setItem(BULK_FILES_KEY, getSelectedBulkFiles().join(",")); } catch {}
  updateRunButtonsState();
}
function clearAllBulk(){
  const box = document.getElementById('bulkFilesBox');
  if (!box) return;
  box.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = false);
  try { localStorage.setItem(BULK_FILES_KEY, ""); } catch {}
  updateRunButtonsState();
}
function getSelectedBulkFiles(){
  const box = document.getElementById('bulkFilesBox');
  if (!box) return [];
  return [...box.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value);
}
function getFirstSelectedFile(){
  const files = getSelectedBulkFiles();
  return files.length ? files[0] : null;
}
function getSelectedPresets(){
  const box = document.getElementById('packPresetsBox');
  if (!box) return [];
  const sel = box.querySelector('input[type=radio]:checked');
  return sel ? [sel.value] : [];
}
function getSelectedVoicing(){
  const box = document.getElementById('voicingBox');
  if (!box) return null;
  const sel = box.querySelector('input[type=radio]:checked');
  return sel ? sel.value : null;
}
function updatePackButtonState(){
  const btn = document.getElementById('runPackBtn');
  if (!btn) return;
  const mode = getVoicingMode();
  const hasFiles = getSelectedBulkFiles().length > 0;
  const hasPreset = getSelectedPresets().length > 0;
  const hasVoicing = !!getSelectedVoicing();
  const ok = mode === 'presets' ? hasPreset : hasVoicing;
  btn.disabled = !(hasFiles && ok);
}
function initVoicingUI(){
  // Force default to voicing on fresh load (and persist)
  setVoicingMode('voicing');
  try { localStorage.setItem(VOICING_MODE_KEY, 'voicing'); } catch {}
  const radios = document.querySelectorAll('input[name="modePresetVoicing"]');
  radios.forEach(r => {
    r.addEventListener('change', () => setVoicingMode(r.value));
  });
  // populate voicing cards
  const box = document.getElementById('voicingBox');
  if (box) {
    box.innerHTML = '';
    const stored = localStorage.getItem(VOICING_SELECTED_KEY) || '';
    const voicingMeta = window.VOICING_META || {};
    const order = ["universal","airlift","ember","detail","glue","wide","cinematic","punch"];
    order.forEach(slug => {
      const meta = voicingMeta[slug] || {};
      const wrap = document.createElement('label');
      wrap.style = "display:flex; align-items:center; gap:6px; padding:6px 8px; border:1px solid var(--line); border-radius:10px;";
      const checked = stored === slug;
      wrap.innerHTML = `<input type="radio" name="voicingSel" value="${slug}" ${checked ? 'checked':''}> <span class="mono">${meta.title || slug}</span> <button class="info-btn" type="button" data-info-type="voicing" data-id="${slug}" aria-label="About ${slug}">ⓘ</button>`;
      const input = wrap.querySelector('input');
      input.addEventListener('change', () => {
        selectVoicing(slug);
        setVoicingMode('voicing');
      });
      box.appendChild(wrap);
    });
  }
  updateRunButtonsState();
}
function showManage(){
  document.querySelectorAll('.masterPane').forEach(el => el.classList.add('hidden'));
  const mv = document.getElementById('manageView');
  if (mv) mv.classList.remove('hidden');
  renderManage();
}
function showMaster(){
  document.querySelectorAll('.masterPane').forEach(el => el.classList.remove('hidden'));
  const mv = document.getElementById('manageView');
  if (mv) mv.classList.add('hidden');
}
function updateRunButtonsState(){
  const hasFiles = getSelectedBulkFiles().length > 0;
  const runPackBtn = document.getElementById('runPackBtn');
  const runBulkBtn = document.getElementById('runBulkBtn');
  const runOneBtn = document.getElementById('runOneBtn');
  // Only block when no files; runPack() will surface validation for presets/voicing
  const blocked = !hasFiles;
  if (runPackBtn) runPackBtn.disabled = blocked;
  if (runBulkBtn) runBulkBtn.disabled = blocked;
  if (runOneBtn) runOneBtn.disabled = blocked;
}
async function renderManage(){
  const uploadsDiv = document.getElementById('manageUploads');
  const runsDiv = document.getElementById('manageRuns');
  uploadsDiv.innerHTML = '<div class="small">Loading...</div>';
  runsDiv.innerHTML = '<div class="small">Loading...</div>';
  try{
    const filesResp = await fetch("/api/files", { cache:'no-store' });
    const files = filesResp.ok ? (await filesResp.json()).files || [] : [];
    uploadsDiv.innerHTML = files.length ? '' : '<div class="small" style="opacity:.7;">No uploads</div>';
    const selectRow = document.createElement('div');
    selectRow.className = 'manage-item';
    selectRow.innerHTML = `<div class="small">Select uploads:</div><div><button class="smallBtn" id="selectAllUploads">Select all</button> <button class="smallBtn" id="deleteUploads">Delete selected</button></div>`;
    if (files.length) uploadsDiv.appendChild(selectRow);
    files.forEach(f => {
      const row = document.createElement('div');
      row.className = 'manage-item';
      row.innerHTML = `<label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" data-upload="${f}"><span class="mono">${f}</span></label>`;
      uploadsDiv.appendChild(row);
    });
  }catch(e){ uploadsDiv.innerHTML = '<div class="small" style="color:#f99;">Error loading uploads</div>'; }
  try{
    const runsResp = await fetch("/api/recent?limit=200", { cache:'no-store' });
    const runs = runsResp.ok ? (await runsResp.json()).items || [] : [];
    runsDiv.innerHTML = runs.length ? '' : '<div class="small" style="opacity:.7;">No runs</div>';
    const selectRowR = document.createElement('div');
    selectRowR.className = 'manage-item';
    selectRowR.innerHTML = `<div class="small">Select runs:</div><div><button class="smallBtn" id="selectAllRuns">Select all</button> <button class="smallBtn" id="deleteRuns">Delete selected</button></div>`;
    if (runs.length) runsDiv.appendChild(selectRowR);
    runs.forEach(r => {
      const row = document.createElement('div');
      row.className = 'manage-item';
      row.innerHTML = `<label style="display:flex; align-items:center; gap:8px;"><input type="checkbox" data-run="${r.song}"><span class="mono">${r.song}</span></label>`;
      runsDiv.appendChild(row);
    });
  }catch(e){ runsDiv.innerHTML = '<div class="small" style="color:#f99;">Error loading runs</div>'; }
  const selectAllUploadsBtn = document.getElementById('selectAllUploads');
  const deleteUploadsBtn = document.getElementById('deleteUploads');
  if (selectAllUploadsBtn) selectAllUploadsBtn.onclick = () => {
    uploadsDiv.querySelectorAll('input[type=checkbox][data-upload]').forEach(cb => cb.checked = true);
  };
  if (deleteUploadsBtn) deleteUploadsBtn.onclick = async () => {
    const selected = [...uploadsDiv.querySelectorAll('input[data-upload]:checked')].map(cb => cb.getAttribute('data-upload'));
    if (!selected.length) return;
    if (!confirm(`Delete ${selected.length} upload(s) from {{IN_DIR}}?`)) return;
    for (const name of selected) {
      await fetch(`/api/upload/${encodeURIComponent(name)}`, { method:'DELETE' });
    }
    renderManage();
    refreshAll();
  };
  const selectAllRunsBtn = document.getElementById('selectAllRuns');
  const deleteRunsBtn = document.getElementById('deleteRuns');
  if (selectAllRunsBtn) selectAllRunsBtn.onclick = () => {
    runsDiv.querySelectorAll('input[type=checkbox][data-run]').forEach(cb => cb.checked = true);
  };
  if (deleteRunsBtn) deleteRunsBtn.onclick = async () => {
    const selected = [...runsDiv.querySelectorAll('input[data-run]:checked')].map(cb => cb.getAttribute('data-run'));
    if (!selected.length) return;
    if (!confirm(`Delete ${selected.length} run(s)?`)) return;
    for (const name of selected) {
      await fetch(`/api/song/${encodeURIComponent(name)}`, { method:'DELETE' });
    }
    renderManage();
    refreshAll();
  };
}
function triggerUpload(){
  const fileInput = document.getElementById('file');
  if (!fileInput) return;
  fileInput.click();
}

async function uploadFilesSequential(files){
  const setMsg = (msg) => {
    try { setResult(msg); } catch(_){}
    try { setStatus(msg); } catch(_){}
  };

  setResultHTML('<span class="spinner">Uploading…</span>');

  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    setMsg(`Uploading (${i+1}/${files.length}): ${f.name}`);
    const fd = new FormData();
    // backend expects "files"
    fd.append('files', f, f.name);
    const r = await fetch('/api/upload', { method:'POST', body: fd });
    const t = await r.text();
    if (!r.ok) {
      setMsg(`Upload failed: ${f.name} — ${t}`);
      throw new Error(t || `Upload failed for ${f.name}`);
    }
  }

  setMsg('Upload complete.');
  try { await refreshAll(); } catch(_){}
}

function wireUploadForm(){
  const form = document.getElementById('uploadForm');
  const fileInput = document.getElementById('file');
  const uploadBtn = document.getElementById('uploadBtn');

  if (uploadBtn && fileInput) {
    // allow keyboard users to trigger
    uploadBtn.addEventListener('keydown', (e)=> {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); triggerUpload(); }
    });
  }

  if (fileInput) {
    fileInput.addEventListener('change', async ()=>{
      const files = [...(fileInput.files || [])];
      if (!files.length) return;
      try {
        await uploadFilesSequential(files);
      } catch(_) {
        // message already set
      } finally {
        // allow re-uploading same filename
        fileInput.value = '';
      }
    });
  }

  // prevent form submit (we're using file picker change)
  if (form) {
    form.addEventListener('submit', (e)=> e.preventDefault());
  }
}

function initInfoDrawer(){
  const drawer = document.getElementById('infoDrawer');
  const backdrop = document.getElementById('drawerBackdrop');
  const closeBtn = document.getElementById('drawerClose');
  const titleEl = document.getElementById('drawerTitle');
  const subEl = document.getElementById('drawerSubtitle');
  const bodyEl = document.getElementById('drawerBody');
  let lastFocus = null;
  const closeDrawer = () => {
    drawer.classList.remove('open');
    setTimeout(() => drawer.classList.add('hidden'), 150);
    backdrop.classList.add('hidden');
    if (lastFocus) {
      try { lastFocus.focus(); } catch(_){}
    }
  };
  const openDrawer = (title, subtitle, bodyHTML, trigger) => {
    lastFocus = trigger || null;
    titleEl.textContent = title || '';
    subEl.textContent = subtitle || '';
    bodyEl.innerHTML = bodyHTML || '';
    drawer.classList.remove('hidden');
    setTimeout(() => drawer.classList.add('open'), 10);
    backdrop.classList.remove('hidden');
    drawer.focus();
  };
  const renderList = (items) => {
    if (!items || !items.length) return '<div class="small" style="opacity:.7;">—</div>';
    return `<ul>${items.map(i=>`<li>${i}</li>`).join('')}</ul>`;
  };
  const renderChips = (items) => {
    if (!items || !items.length) return '<div class="small" style="opacity:.7;">—</div>';
    return `<div class="chips">${items.map(i=>`<span class="chip">${i}</span>`).join('')}</div>`;
  };
    const renderPresetDrawer = (id) => {
      const m = (window.PRESET_META || {})[id];
      if (!m) return;
      const body = `
      <div class="small" style="margin-bottom:8px;">Each preset produces its own mastered version for A/B comparison.</div>
      <div class="drawer-section">
        <h3>Intent</h3>
        <div class="small">${m.intent || ''}</div>
      </div>
      <div class="drawer-section">
        <h3>DSP Meaning</h3>
        ${renderList(m.dsp || [])}
      </div>
      <div class="drawer-section">
        <h3>Best For</h3>
        ${renderChips(m.bestFor || [])}
      </div>
      <div class="drawer-section">
        <h3>Watch Out</h3>
        ${renderList(m.watchOut || [])}
      </div>
      ${m.abNotes ? `<div class="drawer-section"><h3>What to listen for</h3>${renderList(m.abNotes)}</div>` : ''}
    `;
    openDrawer(m.title || id, '', body, document.querySelector(`.info-btn[data-id="${id}"]`));
    };
  const renderLoudnessDrawer = () => {
    const profiles = window.LOUDNESS_PROFILES || {};
    const sel = document.getElementById('loudnessMode');
    const currentId = sel ? sel.value : 'apple';
    const prof = profiles[currentId] || {};
    const overrideLufs = document.getElementById('useLufs')?.checked ? document.getElementById('lufs')?.value : null;
    const overrideTp = document.getElementById('useTp')?.checked ? document.getElementById('tp')?.value : null;
    const currentLufs = overrideLufs !== null && overrideLufs !== undefined ? overrideLufs : prof.targetLUFS;
    const currentTp = overrideTp !== null && overrideTp !== undefined ? overrideTp : prof.truePeakDBTP;
    const tableRows = Object.entries(profiles).map(([k,v]) => {
      const l = v.targetLUFS; const t = v.truePeakDBTP;
      return `<tr><td style="padding:4px 6px;">${v.title || k}</td><td style="padding:4px 6px;">${l ?? '—'}</td><td style="padding:4px 6px;">${t ?? '—'}</td></tr>`;
    }).join('');
    const body = `
      <div class="drawer-section">
        <h3>Current profile</h3>
        <div class="small">Target LUFS: ${currentLufs ?? '—'} | TP ceiling: ${currentTp ?? '—'}</div>
        <div class="small" style="margin-top:6px;">Notes:</div>
        ${renderList(prof.notes || [])}
      </div>
      <div class="drawer-section">
        <h3>Rationale</h3>
        ${renderList(prof.rationale || [])}
      </div>
      <div class="drawer-section">
        <h3>Typical use</h3>
        ${renderChips(prof.typicalUse || [])}
      </div>
      <div class="drawer-section">
        <h3>Caution</h3>
        ${renderList(prof.caution || [])}
      </div>
      <div class="drawer-section">
        <h3>Profiles</h3>
        <table style="width:100%; border-collapse:collapse; font-size:12px;">
          <thead>
            <tr><th style="text-align:left; padding:4px 6px;">Profile</th><th style="text-align:left; padding:4px 6px;">LUFS</th><th style="text-align:left; padding:4px 6px;">TP (dBTP)</th></tr>
          </thead>
          <tbody>${tableRows}</tbody>
        </table>
        <div class="small" style="margin-top:6px;">Defaults are editable via Override controls.</div>
      </div>
    `;
    openDrawer("Loudness Profiles", prof.title || currentId, body, document.querySelector('.info-btn[data-info-type="loudness"]'));
  };
  const renderVoicingDrawer = (id) => {
    const vm = (window.VOICING_META || {})[id];
    if (!vm) return;
    const body = `
      <div class="drawer-section">
        <h3>What it does</h3>
        ${renderList(vm.what || [])}
      </div>
      <div class="drawer-section">
        <h3>Best for</h3>
        ${renderChips(vm.best || [])}
      </div>
      <div class="drawer-section">
        <h3>Watch-outs</h3>
        ${renderList(vm.watch || [])}
      </div>
      <div class="drawer-section">
        <h3>Intensity tips</h3>
        ${renderList(vm.intensity || [])}
      </div>
    `;
    openDrawer(vm.title || id, '', body, document.querySelector(`.info-btn[data-id="${id}"]`));
  };
  window.openDrawer = openDrawer;
  window.renderPresetDrawer = renderPresetDrawer;
  window.renderLoudnessDrawer = renderLoudnessDrawer;
  window.renderVoicingDrawer = renderVoicingDrawer;
  window.closeDrawer = closeDrawer;
  backdrop.addEventListener('click', closeDrawer);
  closeBtn.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
  document.body.addEventListener('click', (e) => {
    const btn = e.target.closest('.info-btn');
    if (!btn) return;
    e.preventDefault(); e.stopPropagation();
    const type = btn.getAttribute('data-info-type');
    if (type === 'preset') {
      renderPresetDrawer(btn.getAttribute('data-id'));
    } else if (type === 'loudness') {
      renderLoudnessDrawer();
    } else if (type === 'metrics') {
      renderMetricsDrawer(btn);
    } else if (type === 'voicing') {
      renderVoicingDrawer(btn.getAttribute('data-id'));
    }
  });
}
function fmtMetric(v, suffix=""){
  if (v === null || v === undefined) return "—";
  if (typeof v === "number" && Number.isFinite(v)) return `${v.toFixed(1)}${suffix}`;
  return String(v);
}
function fmtDelta(out, inp, suffix=""){
  if (out === null || out === undefined || inp === null || inp === undefined) return "";
  if (typeof out !== "number" || typeof inp !== "number") return "";
  const d = out - inp;
  const sign = d > 0 ? "+" : "";
  return ` (${sign}${d.toFixed(1)}${suffix})`;
}
function metricVal(m, key){
  if (!m) return null;
  switch(key){
    case "I": return m.I;
    case "TP": return m.TP;
    case "LRA": return m.LRA;
    case "Peak": return m.peak_level;
    case "RMS": return m.rms_level;
    case "DR": return m.dynamic_range;
    case "Noise": return m.noise_floor;
    case "CF": return m.crest_factor;
    case "Corr": return m.stereo_corr;
    case "Dur": return m.duration_sec;
    case "W": return m.W !== undefined ? m.W : (m.width !== undefined ? m.width : null);
    default: return null;
  }
}
function fmtCompactIO(inputM, outputM){
  // Core: the 4 most representative "headline" mastering metrics
  // 1) I  = integrated loudness (target adherence)
  // 2) TP = true peak (platform safety)
  // 3) LRA = loudness range (macro dynamics)
  // 4) CF = crest factor (transient / punch proxy)
  const core = [
    { key:"I",   label:"I",   suffix:" LUFS", tip:"Integrated loudness (LUFS)" },
    { key:"TP",  label:"TP",  suffix:" dB",   tip:"True peak (dBTP)" },
    { key:"LRA", label:"LRA", suffix:"",      tip:"Loudness range" },
    { key:"CF",  label:"CF",  suffix:" dB",   tip:"Crest factor (Peak - RMS)" },
  ];

  // Everything else goes under "More"
  const more = [
    { key:"Peak", label:"Peak", suffix:" dB", tip:"Sample peak level (dBFS)" },
    { key:"RMS",  label:"RMS",  suffix:" dB", tip:"RMS level (dBFS)" },
    { key:"DR",   label:"DR",   suffix:" dB", tip:"Dynamic range (astats proxy)" },
    { key:"Noise",label:"Noise",suffix:" dB", tip:"Noise floor (astats)" },
    { key:"Corr", label:"Corr", suffix:"",    tip:"Stereo correlation" },
    { key:"Dur",  label:"Dur",  suffix:" s",  tip:"Duration (seconds)" },
    { key:"W",    label:"W",    suffix:"",    tip:"Stereo width factor" },
  ];

  function chip(c){
    const vIn = metricVal(inputM, c.key);
    const vOut = metricVal(outputM, c.key);
    const d = (typeof vIn === "number" && typeof vOut === "number") ? (vOut - vIn) : null;
    const dTxt = (d === null) ? "" : `${d>0?"+":""}${d.toFixed(1)}${c.suffix||""}`;
    return `
      <div class="metricChip">
        <div class="metricTitle">
          <div class="label">${c.label}</div>
          <button class="info-btn" data-info-type="metrics" data-id="${c.key}" aria-label="${c.tip}">ⓘ</button>
        </div>
        <div class="metricLines">
          <div class="metricLine"><span class="metricTag">In</span><span class="metricVal">${fmtMetric(vIn, c.suffix||"")}</span></div>
          <div class="metricLine"><span class="metricTag">Out</span><span class="metricVal">${fmtMetric(vOut, c.suffix||"")}</span><span class="metricDelta">${dTxt ? `Δ ${dTxt}` : ""}</span></div>
        </div>
      </div>`;
  }

  const coreHtml = core.map(chip).join("");
  const moreHtml = more.map(chip).join("");
  const id = `more_${Math.random().toString(36).slice(2)}`;

  return `
    <div class="metricsGrid">${coreHtml}</div>
    <div class="advToggle">
      <button type="button" onclick="(function(){const el=document.getElementById('${id}'); if(!el) return; el.classList.toggle('advHidden');})()">More</button>
    </div>
    <div id="${id}" class="metricsGrid advHidden">${moreHtml}</div>
  `;
}

function renderMetricsDrawer(triggerBtn){
  const id = triggerBtn?.getAttribute('data-id') || null;
  const meta = METRIC_META.find(m => m.key === id);
  const title = meta ? `${meta.label}` : "Metrics";
  const desc = meta ? meta.desc : "Per-output input/output metrics";
  const body = `
    <div class="drawer-section">
      <h3>${title}</h3>
      <div class="small" style="line-height:1.4;">${desc}</div>
      <div class="small" style="margin-top:10px;">How it impacts sound:</div>
      <ul>
        ${id === "I" ? "<li>Higher (less negative) values are louder; aim for musical balance, not just numbers.</li><li>Lower can preserve dynamics and headroom.</li>" : ""}
        ${id === "TP" ? "<li>Closer to 0 dBTP is louder but riskier for distortion on playback devices.</li><li>Leaving headroom (-1 dBTP) keeps encodes clean.</li>" : ""}
        ${id === "LRA" ? "<li>Higher LRA feels more dynamic and cinematic.</li><li>Lower LRA feels consistent and controlled; good for steady loudness.</li>" : ""}
        ${id === "CF" ? "<li>Higher crest factor keeps punch and transient snap.</li><li>Lower crest factor sounds denser but can feel squashed.</li>" : ""}
        ${id === "Corr" ? "<li>Positive values keep mix coherent; negative can cause phase issues.</li><li>Extremely low/negative correlation can thin out low end.</li>" : ""}
        ${id === "Dur" ? "<li>Duration is informational; long tracks may tolerate gentler processing.</li>" : ""}
        ${id === "W" ? "<li>Width >1 can feel wider and more immersive.</li><li>Width <1 narrows for mono-compatibility; watch for phase.</li>" : ""}
      </ul>
    </div>
  `;
  openDrawer("Metrics", title, body, triggerBtn);
}
function renderMetricsTable(m){
  return '<span style="opacity:.7;">(metrics unavailable)</span>';
}
function appendOverrides(fd){
  const addIfChecked = (chkId, inputId, key) => {
    const chk = document.getElementById(chkId);
    const input = document.getElementById(inputId);
    if (chk && input && chk.checked) fd.append(key, input.value);
  };
  addIfChecked('ov_target_I', 'target_I', 'lufs');
  addIfChecked('ov_target_TP', 'target_TP', 'tp');
  addIfChecked('ov_width', 'width', 'width');
  addIfChecked('ov_mono_bass', 'mono_bass', 'mono_bass');
  const guardrails = document.getElementById('guardrails');
  if (guardrails && guardrails.checked) fd.append('guardrails', '1');
}
function appendOutputOptions(fd){
  const on = (id) => document.getElementById(id)?.checked;
  const val = (id) => document.getElementById(id)?.value;
  fd.append('out_wav', on('out_wav') ? '1' : '0');
  fd.append('wav_bit_depth', val('wav_bit_depth') || '');
  fd.append('wav_sample_rate', val('wav_sample_rate') || '');
  fd.append('out_mp3', on('out_mp3') ? '1' : '0');
  fd.append('mp3_bitrate', val('mp3_bitrate') || '');
  fd.append('mp3_vbr', val('mp3_vbr') || 'none');
  fd.append('out_aac', on('out_aac') ? '1' : '0');
  fd.append('aac_codec', val('aac_codec') || '');
  fd.append('aac_container', val('aac_container') || '');
  fd.append('aac_bitrate', val('aac_bitrate') || '');
  fd.append('out_ogg', on('out_ogg') ? '1' : '0');
  fd.append('ogg_quality', val('ogg_quality') || '');
  fd.append('out_flac', on('out_flac') ? '1' : '0');
  fd.append('flac_level', val('flac_level') || '');
  fd.append('flac_bit_depth', val('flac_bit_depth') || '');
  fd.append('flac_sample_rate', val('flac_sample_rate') || '');
}
function appendVoicing(fd){
  const mode = getVoicingMode();
  fd.append('voicing_mode', mode);
  if (mode === 'voicing') {
    const v = getSelectedVoicing();
    if (v) fd.append('voicing_name', v);
  }
}
let runPollFiles = [];
function stopRunPolling() {
  if (statusStream) {
    statusStream.close();
    statusStream = null;
  }
  runPollFiles = [];
  runPollActive = false;
  suppressRecentDuringRun = false;
  statusEntries = [];
}
async function finishPolling(finishedPrimary){
  stopRunPolling();
  setStatus("");
  setProgress(null);
  suppressRecentDuringRun = false;
  // clear pending metric retries for this song
  if (finishedPrimary) {
    metricsRetryCount.delete(finishedPrimary);
    pendingMetricsRetry.delete(finishedPrimary);
  }
  // Do a single quiet refresh without forcing an extra reload if output already loaded
  try { await refreshRecent(true); } catch(e) { console.debug('recent refresh after polling stop failed', e); }
  if (finishedPrimary) {
    try { await loadSong(finishedPrimary, { quiet:true }); } catch(_){}
  }
  runPollPrimary = null;
}
function startRunPolling(files) {
  stopRunPolling();
  const arr = Array.isArray(files) ? files : [];
  if (!arr.length) return;
  runPollFiles = [...arr];
  runPollPrimary = runPollFiles[0] || null;
  runPollActive = true;
  setStatus(`Processing ${arr.join(', ')}`);
  // Show an immediate placeholder so the user sees progress instantly
  setResultHTML(`<div id="joblog" class="mono"><div>Starting…</div></div>`);
  setProgress(0.05);
  // Start SSE stream for status updates
  if (runPollPrimary) {
    statusEntries = [];
    let url = `/api/status-stream?song=${encodeURIComponent(runPollPrimary)}`;
    statusStream = new EventSource(url);
    statusStream.onmessage = (ev) => {
      try {
        const entry = JSON.parse(ev.data);
        statusEntries.push(entry);
        const html = renderStatusEntries(statusEntries);
        setResultHTML(html);
        updateProgressFromEntries(statusEntries);
        if (entry.stage === 'complete') {
          // On complete, load outputs once and refresh recent, then stop stream
          (async () => {
            setStatus("Loading outputs...");
            try {
              await Promise.allSettled([
                refreshRecent(true),
                loadSong(runPollPrimary, { preOutlist: entry.result || null, quiet:false })
              ]);
            } catch (_){}
          })().finally(() => finishPolling(runPollPrimary));
        }
      } catch (e) {
        console.debug('status stream parse error', e);
      }
    };
    statusStream.onerror = async () => {
      // On error, try a snapshot once to repaint, then stop the stream
      try {
        if (runPollPrimary) {
          const snap = await fetch(`/api/run/${encodeURIComponent(runPollPrimary)}`, { cache:'no-store' }).then(r=>r.ok?r.json():null);
          if (snap && Array.isArray(snap.events)) {
            statusEntries = snap.events;
            const html = renderStatusEntries(statusEntries);
            setResultHTML(html);
            updateProgressFromEntries(statusEntries);
            if (snap.terminal) {
              await Promise.allSettled([
                refreshRecent(true),
                loadSong(runPollPrimary, { preOutlist: (snap.events[snap.events.length-1]||{}).result || null, quiet:false })
              ]);
              finishPolling(runPollPrimary);
              return;
            }
          }
        }
      } catch(_){}
      if (statusStream) {
        statusStream.close();
        statusStream = null;
      }
      finishPolling(runPollPrimary);
    };
  }
}
async function loadSong(song, options=false){
  let opts = { skipEmpty: false, quiet: false, preOutlist: null };
  if (typeof options === 'object' && options !== null) {
    opts.skipEmpty = !!options.skipEmpty;
    opts.quiet = !!options.quiet;
    opts.preOutlist = options.preOutlist || null;
  } else {
    opts.skipEmpty = !!options;
    opts.quiet = !!options; // boolean true from polling implies quiet
  }
  if (!opts.quiet) {
    localStorage.setItem("lastSong", song);
    setLinks('');
  }
  lastRunInputMetrics = null;
  let j = opts.preOutlist;
  if (!j) {
    const r = await fetch(`/api/outlist?song=${encodeURIComponent(song)}`, { cache:'no-store' });
    j = await r.json();
  }
  const hasItems = j.items && j.items.length > 0;
  if (opts.skipEmpty && !hasItems) return { hasItems:false, hasPlayable:false, processing:false };
  let hasPlayable = false;
  let anyMetricsStrings = false;
  if (!opts.quiet) {
    const out = document.getElementById('outlist');
    out.innerHTML = '';
    if (j.input) {
      lastRunInputMetrics = j.input;
    }
    j.items.forEach(it => {
      const downloads = Array.isArray(it.downloads) ? it.downloads : [];
      const audioSrc = it.audio || (downloads[0]?.url) || it.mp3 || it.wav || null;
      if (audioSrc) hasPlayable = true;
      if (it.metrics) anyMetricsStrings = true;
      const compact = fmtCompactIO(lastRunInputMetrics, it.metrics_obj || {});
      const ioBlock = compact ? `<div class="ioRow">${compact}</div>` : '';
      const linkParts = [];
      downloads.forEach(d => {
        if (d && d.url && d.label) {
          linkParts.push(`<a class="linkish" href="${d.url}" download>${d.label}</a>`);
        }
      });
      if (!downloads.length) {
        if (it.wav) linkParts.push(`<a class="linkish" href="${it.wav}" download>WAV</a>`);
        if (it.mp3) linkParts.push(`<a class="linkish" href="${it.mp3}" download>MP3</a>`);
      }
      linkParts.push(`<a class="linkish" href="#" onclick="deleteOutput('${song}','${it.name}'); return false;">Delete</a>`);
      const div = document.createElement('div');
      div.className = 'outitem';
      const badgeRowId = `badges_${Math.random().toString(36).slice(2)}`;
      div.innerHTML = `
        <div class="mono" style="display:flex; flex-direction:column; gap:6px;">
          <div class="outHeader">
            <div class="outTitle">${it.display_title || it.name}</div>
            <div class="badgeRow" id="${badgeRowId}"></div>
          </div>
        </div>
        ${ioBlock}
        ${audioSrc ? `<audio controls preload="none" src="${audioSrc}"></audio>` : ''}
        <div class="small">${linkParts.join(' | ')}</div>
        `;
      out.appendChild(div);
      const br = document.getElementById(badgeRowId);
      if (br) {
        br.dataset.badges = JSON.stringify(it.badges || []);
      }
      queueBadgeLayout();
    });
  } else {
    j.items.forEach(it => {
      const downloads = Array.isArray(it.downloads) ? it.downloads : [];
      const audioSrc = it.audio || (downloads[0]?.url) || it.mp3 || it.wav || null;
      if (audioSrc) hasPlayable = true;
      if (it.metrics) anyMetricsStrings = true;
    });
  }
  return { hasItems, hasPlayable, processing:false };
}
async function showOutputsFromText(text){
  const lines = (text || '').split('\n').map(x => x.trim()).filter(Boolean);
  if (!lines.length) return;
  const m = lines[0].match(/\/out\/([^\/]+)\//);
  if (!m) return;
  const song = m[1];
  await loadSong(song);
  await refreshRecent();
}
async function runOne(){
  suppressRecentDuringRun = true;
  clearOutList(); setLinks(''); setMetricsPanel('(waiting)');
  setStatus("Running master...");
  startJobLog('Processing...');
  setProgress(0.05);
  const files = getSelectedBulkFiles();
  const presets = getSelectedPresets();
  const mode = getVoicingMode();
  const voicing = getSelectedVoicing();
  const needPreset = (mode === 'presets');
  const needVoicing = (mode === 'voicing');
  if (!files.length || (needPreset && !presets.length) || (needVoicing && !voicing)) {
    alert("Select at least one input file and a selection for the active mode (voicing or preset).");
    suppressRecentDuringRun = false;
    return;
  }
  const song = (files[0] || '').replace(/\.[^.]+$/, '') || files[0];
  const strength = document.getElementById('strength').value;
  const pollFiles = files.map(f => f.replace(/\.[^.]+$/, '') || f);
  const fd = new FormData();
  const stageVal = (id, defV=1) => {
    const el = document.getElementById(id);
    if (!el) return defV;
    return el.checked ? 1 : 0;
  };
  fd.append('stage_analyze', String(stageVal('stage_analyze', 1)));
  fd.append('stage_master', String(stageVal('stage_master', 1)));
  fd.append('stage_loudness', String(stageVal('stage_loudness', 1)));
  fd.append('stage_stereo', String(stageVal('stage_stereo', 1)));
  fd.append('stage_output', String(stageVal('stage_output', 1)));
  fd.append('infiles', files.join(","));
  fd.append('strength', strength);
  fd.append('presets', needPreset ? presets.join(",") : "");
  appendOverrides(fd);
  appendOutputOptions(fd);
  appendVoicing(fd);
  if (needPreset) {
    presets.forEach(p => files.forEach(f => appendJobLog(`Queued ${f} with preset ${p}`)));
  } else if (voicing) {
    files.forEach(f => appendJobLog(`Queued ${f} with voicing ${voicing}`));
  }
  const r = await fetch('/api/run', { method:'POST', body: fd });
  const t = await r.text();
  let runIds = pollFiles;
  try {
    const j = JSON.parse(t);
    appendJobLog(j.message || 'Run submitted');
    if (Array.isArray(j.run_ids) && j.run_ids.length) {
      runIds = j.run_ids;
    }
  } catch {
    appendJobLog(cleanResultText(t));
  }
  startRunPolling(runIds);
  try { await refreshAll(); } catch (e) { console.error(e); }
}
async function runPack(){
  const files = getSelectedBulkFiles();
  const presets = getSelectedPresets();
  const mode = getVoicingMode();
  const voicing = getSelectedVoicing();
  const needPreset = (mode === 'presets');
  const needVoicing = (mode === 'voicing');
  if (!files.length || (needPreset && !presets.length) || (needVoicing && !voicing)) {
    const msg = "Run failed: please select at least one input file and a voicing or preset.";
    setStatus(msg);
    setResultHTML(`<div id="joblog" class="mono"><div>${msg}</div></div>`);
    setProgress(null);
    return;
  }
  suppressRecentDuringRun = true;
  clearOutList(); setLinks(''); setMetricsPanel('(waiting)');
  setStatus("A/B pack running...");
  startJobLog('Processing...');
  setProgress(0.05);
  try { localStorage.setItem("packInFlight", String(Date.now())); } catch {}
  const strength = document.getElementById('strength').value;
  const pollFiles = files.map(f => f.replace(/\.[^.]+$/, '') || f);
  const fd = new FormData();

  // Pipeline stage flags (UI -> backend). Defaults to enabled if checkbox not present.
  const stageVal = (id, defV=1) => {
    const el = document.getElementById(id);
    if (!el) return defV;
    return el.checked ? 1 : 0;
  };
  fd.append('stage_analyze', String(stageVal('stage_analyze', 1)));
  fd.append('stage_master', String(stageVal('stage_master', 1)));
  fd.append('stage_loudness', String(stageVal('stage_loudness', 1)));
  fd.append('stage_stereo', String(stageVal('stage_stereo', 1)));
  fd.append('stage_output', String(stageVal('stage_output', 1)));
  fd.append('infiles', files.join(","));
  fd.append('strength', strength);
  fd.append('presets', needPreset ? presets.join(",") : "");
  appendOverrides(fd);
  appendOutputOptions(fd);
  appendVoicing(fd);
  if (needPreset) {
    presets.forEach(p => files.forEach(f => appendJobLog(`Queued ${f} with preset ${p}`)));
  } else if (voicing) {
    files.forEach(f => appendJobLog(`Queued ${f} with voicing ${voicing}`));
  }
  const r = await fetch('/api/run', { method:'POST', body: fd });
  const t = await r.text();
  let runIds = pollFiles;
  try {
    const j = JSON.parse(t);
    appendJobLog(j.message || 'Bulk submitted');
    if (Array.isArray(j.run_ids) && j.run_ids.length) {
      runIds = j.run_ids;
    }
  } catch {
    appendJobLog(cleanResultText(t));
  }
  startRunPolling(runIds);
  try { await refreshAll(); } catch (e) { console.error('post-job refreshAll failed', e); }
}
async function runBulk(){
  const files = getSelectedBulkFiles();
  const presets = getSelectedPresets();
  const mode = getVoicingMode();
  const voicing = getSelectedVoicing();
  const needPreset = (mode === 'presets');
  const needVoicing = (mode === 'voicing');
  if (!files.length || (needPreset && !presets.length) || (needVoicing && !voicing)) {
    const msg = "Run failed: please select at least one input file and a voicing or preset.";
    setStatus(msg);
    setResultHTML(`<div id="joblog" class="mono"><div>${msg}</div></div>`);
    setProgress(null);
    return;
  }
  suppressRecentDuringRun = true;
  clearOutList(); setLinks(''); setMetricsPanel('(waiting)');
  setStatus("Bulk run starting...");
  startJobLog('Processing...');
  setProgress(0.05);
  const song = (files[0] || '').replace(/\.[^.]+$/, '') || files[0];
  const strength = document.getElementById('strength').value;
  const pollFiles = files.map(f => f.replace(/\.[^.]+$/, '') || f);
  const fd = new FormData();
  const stageVal = (id, defV=1) => {
    const el = document.getElementById(id);
    if (!el) return defV;
    return el.checked ? 1 : 0;
  };
  fd.append('stage_analyze', String(stageVal('stage_analyze', 1)));
  fd.append('stage_master', String(stageVal('stage_master', 1)));
  fd.append('stage_loudness', String(stageVal('stage_loudness', 1)));
  fd.append('stage_stereo', String(stageVal('stage_stereo', 1)));
  fd.append('stage_output', String(stageVal('stage_output', 1)));
  fd.append('infiles', files.join(","));
  fd.append('strength', strength);
  fd.append('presets', needPreset ? presets.join(",") : "");
  appendOverrides(fd);
  appendOutputOptions(fd);
  appendVoicing(fd);
  if (needPreset) {
    presets.forEach(p => files.forEach(f => appendJobLog(`Queued ${f} with preset ${p}`)));
  } else if (voicing) {
    files.forEach(f => appendJobLog(`Queued ${f} with voicing ${voicing}`));
  }
  const r = await fetch('/api/run', { method:'POST', body: fd });
  const t = await r.text();
  let runIds = pollFiles;
  try {
    const j = JSON.parse(t);
    if (j && typeof j === 'object') {
      appendJobLog(j.message || 'Bulk submitted');
      if (Array.isArray(j.run_ids) && j.run_ids.length) {
        runIds = j.run_ids;
      }
    } else {
      appendJobLog(cleanResultText(t));
    }
  } catch {
    appendJobLog(cleanResultText(t));
  }
  startRunPolling(runIds);
  await refreshAll();
}
async function deleteOutput(song, name){
  if (!confirm(`Delete output "${name}"?`)) return;
  try {
    const res = await fetch(`/api/output/${encodeURIComponent(song)}/${encodeURIComponent(name)}`, { method:'DELETE' });
    let msg = '';
    try { msg = (await res.json()).message || ''; } catch(_){}
    setResult(msg || (res.ok ? 'Deleted.' : 'Delete failed.'));
  } catch (e) {
    console.error('deleteOutput failed', e);
    setResult('Delete failed (see console).');
  }
  try { await loadSong(song); } catch(_){}
  try { await refreshRecent(); } catch(_){}
}
async function deleteSong(song){
  if (!confirm(`Delete all outputs for "${song}"? This removes {{OUT_DIR}}/${song}/`)) return;
  const r = await fetch(`/api/song/${encodeURIComponent(song)}`, { method:'DELETE' });
  const j = await r.json();
  setResult(j.message || 'Deleted.');
  await refreshRecent();
  const last = localStorage.getItem("lastSong");
  if (last === song) {
    localStorage.removeItem("lastSong");
    setLinks('');
    clearOutList();
  }
}
document.getElementById('uploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  setResult('(waiting)'); setLinks(''); clearOutList();
  const f = document.getElementById('file').files[0];
  const fd = new FormData();
  fd.append('file', f);
  const r = await fetch('/api/upload', { method:'POST', body: fd });
  const j = await r.json();
  
  try { await refreshAll(); } catch (e) { console.error('post-upload refreshAll failed', e); }
});
document.addEventListener('DOMContentLoaded', () => {
  try {
    wireUI();
    initLoudnessMode();
    setMetricsPanel('(none)');
    updateRunButtonsState();
    initVoicingUI();
    // Restore pack-in-flight status if page refreshed mid-run (10 min window)
    try {
      const ts = parseInt(localStorage.getItem("packInFlight") || "0", 10);
      if (ts && (Date.now() - ts) < 10*60*1000) setStatus("A/B pack running...");
    } catch {}
    // Ensure only one audio element plays at a time
    document.addEventListener('play', (ev) => {
      if (!(ev.target && ev.target.tagName === 'AUDIO')) return;
      const audios = document.querySelectorAll('audio');
      audios.forEach(a => {
        if (a !== ev.target && !a.paused) {
          try { a.pause(); } catch(_){}
        }
      });
    }, true);
    refreshAll();
    initInfoDrawer();
    wireUploadForm();
    setupUtilMenu('utilToggleMain','utilDropdownMain');
  } catch(e){
    console.error(e);
    setStatus("UI init error (open console)");
  }
});


/* Pipeline section toggles (UI-only for now) */
function initPipelineSections(){
  document.querySelectorAll('.pipeSection').forEach(sec => {
    const cb = sec.querySelector('.pipeHeader input[type="checkbox"]');
    const body = sec.querySelector('.pipeBody');
    if(!cb || !body) return;

    const apply = () => {
      if(cb.checked){
        body.classList.remove('pipeBodyCollapsed');
        sec.classList.remove('disabled');
        body.querySelectorAll('input,select,button,textarea').forEach(el => { el.disabled = false; });
      }else{
        body.classList.add('pipeBodyCollapsed');
        sec.classList.add('disabled');
        body.querySelectorAll('input,select,button,textarea').forEach(el => { el.disabled = true; });
      }
    };
    cb.addEventListener('change', () => {
      if (cb.checked && cb.id !== 'stage_analyze') {
        const analyze = document.getElementById('stage_analyze');
        if (analyze) analyze.checked = true;
        const analyzeSec = analyze ? analyze.closest('.pipeSection') : null;
        if (analyzeSec) {
          analyzeSec.classList.remove('disabled');
          analyzeSec.querySelectorAll('.pipeBody input, .pipeBody select, .pipeBody button, .pipeBody textarea').forEach(el => el.disabled = false);
          const analyzeBody = analyzeSec.querySelector('.pipeBody');
          if (analyzeBody) analyzeBody.classList.remove('pipeBodyCollapsed');
        }
      }
      apply();
    });
    apply();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  try { initPipelineSections(); } catch (e) { console.warn("pipeline init failed", e); }
});
</script>
<script>
window.PRESET_META = {{ preset_meta_json }};
window.LOUDNESS_PROFILES = {{ loudness_profiles_json }};
window.VOICING_META = {{ voicing_meta_json }};
</script>
<div id="drawerBackdrop" class="drawer-backdrop hidden" tabindex="-1"></div>
<aside id="infoDrawer"
       class="info-drawer hidden"
       role="dialog"
       aria-modal="true"
       aria-labelledby="drawerTitle">
  <div class="drawer-header">
    <div>
      <h2 id="drawerTitle"></h2>
      <div id="drawerSubtitle" class="drawer-subtitle"></div>
    </div>
    <button id="drawerClose" aria-label="Close">✕</button>
  </div>
  <div id="drawerBody" class="drawer-body"></div>
</aside>
</body>
</html>
"""
@app.get("/", response_class=HTMLResponse)
def index():
    html = HTML_TEMPLATE
    html = html.replace("{{BUILD_STAMP}}", BUILD_STAMP)
    html = html.replace("{{VERSION}}", VERSION)
    html = html.replace("{{ preset_meta_json }}", json.dumps(load_preset_meta()))
    html = html.replace("{{ loudness_profiles_json }}", json.dumps(LOUDNESS_PROFILES))
    html = html.replace("{{ voicing_meta_json }}", json.dumps(VOICING_META))
    html = html.replace("{{IN_DIR}}", str(IN_DIR))
    html = html.replace("{{OUT_DIR}}", str(OUT_DIR))
    return HTMLResponse(html)
@app.get("/manage-presets", response_class=HTMLResponse)
def manage_presets():
    html = MANAGE_PRESETS_HTML.replace("{{VERSION}}", VERSION)
    return HTMLResponse(html)
@app.get("/manage-files", response_class=HTMLResponse)
def manage_files():
    html = MANAGE_FILES_HTML.replace("{{VERSION}}", VERSION)
    return HTMLResponse(html)
@app.get("/tagger", response_class=HTMLResponse)
def tagger_page():
    return HTMLResponse(TAGGER_HTML.replace("{{VERSION}}", VERSION))

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
MANAGE_PRESETS_HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="icon" type="image/x-icon" href="/favicon.ico">
  <title>Manage Presets - SonusTemper</title>
  <style>
    :root{
      --bg:#0b0f14; --card:#121a23; --muted:#9fb0c0; --text:#e7eef6;
      --line:#203042; --accent:#ff8a3d; --accent2:#2bd4bd; --danger:#ff4d4d;
    }
    body{ margin:0; background:linear-gradient(180deg,#0b0f14,#070a0e); color:var(--text);
      font-family:-apple-system,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
    .wrap{ max-width:1200px; margin:0 auto; padding:26px 18px 40px; }
    h1{ font-size:20px; margin:0 0 6px 0; letter-spacing:.2px; }
    .card{ background:rgba(18,26,35,.9); border:1px solid var(--line); border-radius:16px; padding:16px; margin-top:14px; }
    .row{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
    .btn{ background:linear-gradient(180deg, rgba(255,138,61,.95), rgba(255,138,61,.75));
      border:0; color:#1a0f07; font-weight:800; padding:8px 14px; border-radius:10px; cursor:pointer; }
    .btnGhost{ padding:8px 14px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; cursor:pointer; }
    .btnDanger{ padding:8px 14px; border-radius:10px; border:1px solid rgba(255,77,77,.35); background:rgba(255,77,77,.15); color:#ffd0d0; cursor:pointer; }
    .list{ display:flex; flex-direction:column; gap:10px; margin-top:10px; }
    .item{ padding:10px; border:1px solid var(--line); border-radius:12px; background:#0f151d; display:flex; justify-content:space-between; align-items:center; gap:10px; }
    .mono{ font-family: ui-monospace, Menlo, Consolas, monospace; }
    .small{ color:var(--muted); font-size:12px; }
    label{ color:#cfe0f1; font-size:13px; font-weight:600; }
    input[type="file"]{ color:var(--text); }
    .col{ display:flex; flex-direction:column; gap:6px; }
    .info-btn{
      border:1px solid var(--line);
      background:#0f151d;
      color:var(--muted);
      border-radius:50%;
      width:22px; height:22px;
      display:inline-flex; align-items:center; justify-content:center;
      cursor:pointer;
      font-size:12px;
    }
    .info-btn:hover{ color:var(--text); border-color:var(--accent); }
    .drawer-backdrop{
      position:fixed; inset:0; background:rgba(0,0,0,0.35); backdrop-filter: blur(2px);
      z-index:999; transition: opacity .2s ease;
    }
    .info-drawer{
      position:fixed; top:0; right:0; width:420px; max-width:90vw; height:100%;
      background:#0f151d; border-left:1px solid var(--line); box-shadow: -6px 0 18px rgba(0,0,0,0.35);
      z-index:1000; transform: translateX(100%); transition: transform .25s ease;
      display:flex; flex-direction:column; padding:16px;
    }
    .utilMenu{ position:relative; }
    .utilMenuTop{ position:absolute; top:12px; right:18px; z-index:20; }
    .utilToggle{ padding:8px 12px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; cursor:pointer; }
    .utilToggle:hover{ border-color:var(--accent); color:var(--text); }
    .utilDropdown{
      position:absolute; right:0; top:calc(100% + 6px);
      background:#0f151d; border:1px solid var(--line); border-radius:10px;
      min-width:160px; z-index:50; box-shadow:0 8px 22px rgba(0,0,0,0.35);
      display:flex; flex-direction:column; overflow:hidden;
    }
    .utilDropdown a{
      padding:10px 12px; color:#d7e6f5; text-decoration:none; font-size:13px;
    }
    .utilDropdown a:hover{ background:rgba(255,138,61,0.12); color:var(--text); }
    .utilDropdown.hidden{ display:none; }
    .info-drawer.open{ transform: translateX(0); }
    @media (max-width: 768px){
      .info-drawer{ width:100%; height:65vh; top:auto; bottom:0; border-left:0; border-top:1px solid var(--line); transform: translateY(100%); }
      .info-drawer.open{ transform: translateY(0); }
    }
    .drawer-header{ display:flex; justify-content:space-between; align-items:center; gap:10px; }
    .drawer-header h2{ margin:0; font-size:16px; color:#e7eef6; }
    .drawer-subtitle{ color:var(--muted); font-size:12px; }
    .drawer-body{ margin-top:12px; overflow:auto; padding-right:6px; display:flex; flex-direction:column; gap:10px; }
    .drawer-section h3{ margin:0 0 6px 0; font-size:13px; color:#cfe0f1; }
    .drawer-section ul{ margin:0; padding-left:18px; color:#d7e6f5; font-size:12px; }
    .drawer-section .chips{ display:flex; flex-wrap:wrap; gap:6px; }
    .drawer-section .chip{ padding:6px 10px; border:1px solid var(--line); border-radius:12px; font-size:12px; color:#d7e6f5; background:#0f151d; }
    .hidden{ display:none !important; }
    .info-drawer.hidden{ display:none !important; }
    .drawer-backdrop.hidden{ display:none !important; }
    .utilMenu{ position:relative; }
    .utilToggle{ padding:8px 12px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; cursor:pointer; }
    .utilToggle:hover{ border-color:var(--accent); color:var(--text); }
    .utilDropdown{
      position:absolute; right:0; top:calc(100% + 6px);
      background:#0f151d; border:1px solid var(--line); border-radius:10px;
      min-width:160px; z-index:50; box-shadow:0 8px 22px rgba(0,0,0,0.35);
      display:flex; flex-direction:column; overflow:hidden;
    }
    .utilDropdown a{
      padding:10px 12px; color:#d7e6f5; text-decoration:none; font-size:13px;
    }
    .utilDropdown a:hover{ background:rgba(255,138,61,0.12); color:var(--text); }
    .utilDropdown.hidden{ display:none; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="row" style="justify-content:space-between; align-items:center;">
      <div>
        <h1>Manage Presets</h1>
        <div class="small">Download, delete, or create presets from a reference audio file (≤100MB).</div>
      </div>
      <div class="utilMenu">
        <button class="utilToggle" id="utilToggleManage" aria-haspopup="true" aria-expanded="false">☰ Utilities</button>
        <div class="utilDropdown hidden" id="utilDropdownManage">
          <a href="/">Mastering</a>
          <div style="height:1px; background:var(--line); margin:4px 0;"></div>
          <a href="/manage-files">File Manager</a>
          <a href="/manage-presets">Preset Manager</a>
          <a href="/tagger">Tag Editor</a>
        </div>
      </div>
    </div>

    <div class="row" style="gap:16px; flex-wrap:wrap;">
      <div class="card" style="flex:1 1 0; min-width:320px;">
        <h2 style="margin:0 0 8px 0; font-size:15px; display:flex; align-items:center; gap:8px;">
          Analyze reference to create preset
          <button class="info-btn" type="button" data-info-type="manage-preset-info" aria-label="How this works">ⓘ</button>
        </h2>
        <div class="small" style="margin-bottom:6px;">Upload supported audio (wav/mp3/flac/aiff, ≤100MB). We will analyze loudness/tonal balance and seed a preset.</div>
        <form id="uploadPresetForm" class="col">
          <div class="row" style="gap:10px; align-items:center; flex-wrap:wrap;">
            <input type="file" id="presetFile" accept=".wav,.mp3,.flac,.aiff,.aif" required />
            <button class="btn" type="submit">Analyze & Create</button>
            <div id="uploadStatus" class="small"></div>
          </div>
        </form>
      </div>

      <div class="card" style="flex:1 1 0; min-width:320px;">
        <h2 style="margin:0 0 8px 0; font-size:15px;">Upload preset JSON</h2>
        <div class="small" style="margin-bottom:6px;">Have a JSON preset you edited? Upload it here (≤1MB). Invalid JSON will be rejected.</div>
        <div class="col" style="gap:8px;">
          <div class="row" style="gap:10px; align-items:center; flex-wrap:wrap;">
            <input type="file" id="presetJsonFile" accept=".json" />
            <button class="btnGhost" type="button" id="uploadPresetJsonBtn">Upload Preset</button>
            <div id="uploadPresetJsonStatus" class="small"></div>
          </div>
        </div>
      </div>
    </div>

      <div class="card">
        <h2 style="margin:0 0 8px 0; font-size:15px;">Available presets</h2>
        <div id="presetList" class="list"></div>
    </div>
  </div>
  <div id="drawerBackdropManage" class="drawer-backdrop hidden" tabindex="-1"></div>
  <aside id="infoDrawerManage"
         class="info-drawer hidden"
         role="dialog"
         aria-modal="true"
         aria-labelledby="drawerTitleManage">
    <div class="drawer-header">
      <div>
        <h2 id="drawerTitleManage"></h2>
        <div id="drawerSubtitleManage" class="drawer-subtitle"></div>
      </div>
      <button id="drawerCloseManage" aria-label="Close">✕</button>
    </div>
    <div id="drawerBodyManage" class="drawer-body"></div>
  </aside>
<script>
const manageFetch = (url, opts = {}) => fetch(url, opts);
async function loadPresets(){
  const list = document.getElementById('presetList');
  list.innerHTML = '<div class="small">Loading…</div>';
  try{
    const res = await manageFetch('/api/preset/list', { cache:'no-store' });
    if(!res.ok) throw new Error();
    const data = await res.json();
    const items = data.items || [];
    if(!items.length){
      list.innerHTML = '<div class="small" style="opacity:.7;">No presets found.</div>';
      return;
    }
    list.innerHTML = '';
    items.forEach(it => {
      const meta = it.meta || {};
      const src = meta.source_file || '—';
      const created = meta.created_at || '—';
      const row = document.createElement('div');
      row.className = 'item';
      row.innerHTML = `
        <div class="col" style="gap:2px;">
          <div class="mono" style="font-weight:600;">${it.name}</div>
          <div class="small">${meta.title || ''}</div>
          <div class="small">Source: ${src} • Created: ${created}</div>
        </div>
        <div class="row" style="gap:6px;">
          <button class="btnGhost" onclick="downloadPreset('${it.name}')">Download</button>
          <button class="btnDanger" onclick="deletePreset('${it.name}')">Delete</button>
        </div>
      `;
      list.appendChild(row);
    });
  }catch(e){
    list.innerHTML = '<div class="small" style="color:#f99;">Failed to load presets.</div>';
  }
}
async function downloadPreset(name){
  window.location.href = `/api/preset/download/${encodeURIComponent(name)}`;
}
async function deletePreset(name){
  if(!confirm(`Delete preset "${name}"?`)) return;
  const res = await manageFetch(`/api/preset/${encodeURIComponent(name)}`, { method:'DELETE' });
  if(!res.ok){
    alert('Delete failed');
  }
  loadPresets();
}
document.getElementById('uploadPresetForm').addEventListener('submit', async (e)=>{
  e.preventDefault();
  const status = document.getElementById('uploadStatus');
  const fileInput = document.getElementById('presetFile');
  const f = fileInput.files[0];
  if(!f){ status.textContent = 'Select a file.'; return; }
  status.textContent = 'Uploading...';
  const fd = new FormData();
  fd.append('file', f, f.name);
  const res = await manageFetch('/api/preset/generate', { method:'POST', body: fd });
  if(!res.ok){
    status.textContent = 'Failed to create preset.';
  }else{
    const j = await res.json();
    status.textContent = j.message || 'Preset created.';
    fileInput.value = '';
    loadPresets();
  }
});
document.getElementById('uploadPresetJsonBtn').addEventListener('click', async ()=>{
  const input = document.getElementById('presetJsonFile');
  const status = document.getElementById('uploadPresetJsonStatus');
  const f = input.files && input.files[0];
  if(!f){ status.textContent = 'Select a JSON preset.'; return; }
  status.textContent = 'Uploading...';
  const fd = new FormData();
  fd.append('file', f, f.name);
  try{
    const res = await manageFetch('/api/preset/upload', { method:'POST', body: fd });
    if(!res.ok){
      const t = await res.text();
      status.textContent = `Upload failed: ${t || res.status}`;
      return;
    }
    const j = await res.json();
    status.textContent = j.message || 'Preset uploaded.';
    input.value = '';
    loadPresets();
  }catch(err){
    status.textContent = 'Upload failed.';
  }
});
loadPresets();
loadPresets();
function openDrawerManage(title, subtitle, bodyHTML){
  const drawer = document.getElementById('infoDrawerManage');
  const backdrop = document.getElementById('drawerBackdropManage');
  const titleEl = document.getElementById('drawerTitleManage');
  const subEl = document.getElementById('drawerSubtitleManage');
  const bodyEl = document.getElementById('drawerBodyManage');
  titleEl.textContent = title || '';
  subEl.textContent = subtitle || '';
  bodyEl.innerHTML = bodyHTML || '';
  drawer.classList.remove('hidden');
  setTimeout(()=>drawer.classList.add('open'), 10);
  backdrop.classList.remove('hidden');
}
function closeDrawerManage(){
  const drawer = document.getElementById('infoDrawerManage');
  const backdrop = document.getElementById('drawerBackdropManage');
  drawer.classList.remove('open');
  setTimeout(()=>drawer.classList.add('hidden'), 150);
  backdrop.classList.add('hidden');
}
document.getElementById('drawerBackdropManage').addEventListener('click', closeDrawerManage);
document.getElementById('drawerCloseManage').addEventListener('click', closeDrawerManage);
document.addEventListener('keydown', (e)=>{ if(e.key === 'Escape') closeDrawerManage(); });
document.body.addEventListener('click', (e)=>{
  const btn = e.target.closest('.info-btn');
  if(!btn) return;
  const type = btn.getAttribute('data-info-type');
  if(type === 'manage-preset-info'){
    const body = `
      <div class="drawer-section">
        <h3>How it works</h3>
        <div class="small">We analyze your reference with ffmpeg (loudness, crest factor, basic tone) and seed a simple preset (LUFS target, limiter ceiling, gentle EQ/comp). The source file is discarded after creation.</div>
      </div>
      <div class="drawer-section">
        <h3>Included in the preset</h3>
        <ul>
          <li>Integrated LUFS target from the reference</li>
          <li>Limiter ceiling based on measured true peak</li>
          <li>Light EQ suggestions (de-mud/air lift) from crest factor clues</li>
          <li>Starter compressor settings</li>
          <li>Metadata: source filename and creation time</li>
        </ul>
      </div>
      <div class="drawer-section">
        <h3>Tips</h3>
        <ul>
          <li>Use a mix that represents your desired tone.</li>
          <li>You can edit the saved JSON in the presets directory anytime.</li>
          <li>Files over 100MB or unsupported formats are rejected.</li>
        </ul>
      </div>
    `;
    openDrawerManage("Reference-Based Preset", "", body);
  }
});
// Utilities menu
function setupUtilMenu(toggleId, menuId){
  const toggle = document.getElementById(toggleId);
  const menu = document.getElementById(menuId);
  if(!toggle || !menu) return;
  const close = ()=>{ menu.classList.add('hidden'); toggle.setAttribute('aria-expanded','false'); };
  toggle.addEventListener('click', (e)=>{
    e.stopPropagation();
    const isOpen = !menu.classList.contains('hidden');
    if(isOpen){ close(); } else { menu.classList.remove('hidden'); toggle.setAttribute('aria-expanded','true'); }
  });
  document.addEventListener('click', (e)=>{
    if(!menu.contains(e.target) && e.target !== toggle){ close(); }
  });
}
setupUtilMenu('utilToggleManage','utilDropdownManage');
</script>
</body>
</html>
"""
MANAGE_FILES_HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="icon" type="image/x-icon" href="/favicon.ico">
  <title>Utilities File Manager - SonusTemper</title>
  <style>
    :root{
      --bg:#0b0f14; --card:#121a23; --muted:#9fb0c0; --text:#e7eef6;
      --line:#203042; --accent:#ff8a3d; --accent2:#2bd4bd; --danger:#ff4d4d;
    }
    body{ margin:0; background:linear-gradient(180deg,#0b0f14,#070a0e); color:var(--text);
      font-family:-apple-system,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
    .wrap{ max-width:1200px; margin:0 auto; padding:26px 18px 40px; }
    h1{ font-size:20px; margin:0 0 6px 0; letter-spacing:.2px; }
    .top{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }
    .card{ background:rgba(18,26,35,.9); border:1px solid var(--line); border-radius:16px; padding:16px; }
    .grid{ display:grid; grid-template-columns: 240px 1fr; gap:16px; align-items:start; margin-top:16px; }
    @media (max-width: 960px){ .grid{ grid-template-columns: 1fr; } }
    .btn{ background:linear-gradient(180deg, rgba(255,138,61,.95), rgba(255,138,61,.75));
      border:0; color:#1a0f07; font-weight:800; padding:8px 14px; border-radius:10px; cursor:pointer; }
    .btnGhost{ padding:8px 14px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; cursor:pointer; }
    .btnDanger{ padding:8px 14px; border-radius:10px; border:1px solid rgba(255,77,77,.35); background:rgba(255,77,77,.15); color:#ffd0d0; cursor:pointer; }
    .small{ color:var(--muted); font-size:12px; }
    .utilMenu{ position:relative; }
    .utilToggle{ padding:8px 12px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; cursor:pointer; }
    .utilToggle:hover{ border-color:var(--accent); color:#fff; }
    .utilDropdown{
      position:absolute; right:0; top:calc(100% + 6px);
      background:#0f151d; border:1px solid var(--line); border-radius:10px;
      min-width:180px; z-index:50; box-shadow:0 8px 22px rgba(0,0,0,0.35);
      display:flex; flex-direction:column; overflow:hidden;
    }
    .utilDropdown a{ padding:10px 12px; color:#d7e6f5; text-decoration:none; font-size:13px; }
    .utilDropdown a:hover{ background:rgba(255,138,61,0.12); color:#fff; }
    .utilDropdown.hidden{ display:none; }
    .sidebar h3{ margin:0 0 8px 0; font-size:13px; color:#cfe0f1; }
    .sidebar a{ display:block; padding:10px 12px; border-radius:10px; color:#d7e6f5; text-decoration:none; border:1px solid var(--line); margin-bottom:8px; }
    .sidebar a.active{ border-color:var(--accent); color:#fff; }
    table{ width:100%; border-collapse:collapse; }
    th,td{ padding:8px; border-bottom:1px solid var(--line); text-align:left; }
    th{ color:#cfe0f1; font-size:12px; }
    td{ font-size:13px; }
    .mono{ font-family: ui-monospace, Menlo, Consolas, monospace; }
  </style>
</head>
<body>
    <div class="wrap">
    <div class="top">
      <div>
        <h1>File Manager</h1>
        <div class="small">Browse and manage audio and presets.</div>
      </div>
      <div class="utilMenu">
        <button class="utilToggle" id="utilToggleFiles" aria-haspopup="true" aria-expanded="false">☰ Utilities</button>
        <div class="utilDropdown hidden" id="utilDropdownFiles">
          <a href="/">Mastering</a>
          <div style="height:1px; background:var(--line); margin:4px 0;"></div>
          <a href="/manage-files" style="opacity:.6; pointer-events:none;">File Manager</a>
          <a href="/manage-presets">Preset Manager</a>
          <a href="/tagger">Tag Editor</a>
        </div>
      </div>
    </div>

    <div class="grid">
      <div class="sidebar">
        <h3>Utilities</h3>
        <a href="#" data-utility="mastering" class="active">Mastering</a>
        <a href="#" data-utility="tagging">Tagging</a>
        <a href="#" data-utility="presets">Presets</a>
        <a href="#" style="opacity:.4; pointer-events:none;">Analysis (coming soon)</a>
      </div>
      <div id="utilPanels" class="col" style="gap:14px;">
        <!-- Panels injected -->
      </div>
    </div>
  </div>
<script>
function setupUtilMenu(toggleId, menuId){
  const toggle = document.getElementById(toggleId);
  const menu = document.getElementById(menuId);
  if(!toggle || !menu) return;
  const close = ()=>{ menu.classList.add('hidden'); toggle.setAttribute('aria-expanded','false'); };
  toggle.addEventListener('click', (e)=>{
    e.stopPropagation();
    const isOpen = !menu.classList.contains('hidden');
    if(isOpen){ close(); } else { menu.classList.remove('hidden'); toggle.setAttribute('aria-expanded','true'); }
  });
  document.addEventListener('click', (e)=>{
    if(!menu.contains(e.target) && e.target !== toggle){ close(); }
  });
}
const panels = {
  mastering: [
    { title:"Source Files", section:"source", utility:"mastering" },
    { title:"Job Output", section:"output", utility:"mastering" },
  ],
  tagging: [
    { title:"MP3 Library", section:"library", utility:"tagging" },
  ],
  presets: [
    { title:"User Presets", section:"user", utility:"presets" },
    { title:"Generated Presets", section:"generated", utility:"presets" },
  ],
};
const state = {
  utility: 'mastering',
  selections: {}, // key utility:section -> Set
};
function renderPanels(){
  const cont = document.getElementById('utilPanels');
  cont.innerHTML = '';
  (panels[state.utility] || []).forEach(p=>{
    cont.appendChild(renderPanel(p.utility, p.section, p.title));
  });
  loadAll();
}
function renderPanel(utility, section, title){
  const card = document.createElement('div');
  card.className = 'card';
  card.innerHTML = `
    <div class="row" style="justify-content:space-between; align-items:center;">
      <h2 style="margin:0; font-size:15px;">${title}</h2>
      <div class="small" id="status-${utility}-${section}"></div>
    </div>
    <div class="row" style="justify-content:flex-end; gap:8px; margin:6px 0 10px 0;">
      <button class="btnGhost" type="button" data-action="delete-all" data-utility="${utility}" data-section="${section}">Delete All</button>
      <button class="btnDanger" type="button" data-action="delete-sel" data-utility="${utility}" data-section="${section}">Delete Selected</button>
    </div>
    <div class="tableWrap">
      <table>
        <thead><tr><th></th><th>Name</th><th>Size</th><th>Modified</th><th>Actions</th></tr></thead>
        <tbody id="tbody-${utility}-${section}"><tr><td colspan="5" class="small">Loading…</td></tr></tbody>
      </table>
    </div>
  `;
  return card;
}
function humanSize(bytes){
  if(bytes === null || bytes === undefined) return '';
  const units = ['B','KB','MB','GB'];
  let b = bytes; let i=0;
  while(b>=1024 && i<units.length-1){ b/=1024; i++; }
  return `${b.toFixed(1)} ${units[i]}`;
}
async function loadTable(utility, section){
  const tbody = document.getElementById(`tbody-${utility}-${section}`);
  const status = document.getElementById(`status-${utility}-${section}`);
  if(tbody) tbody.innerHTML = '<tr><td colspan="5" class="small">Loading…</td></tr>';
  try{
    const res = await fetch(`/api/utility-files?utility=${utility}&section=${section}`, { cache:'no-store' });
    if(!res.ok) throw new Error();
    const data = await res.json();
    const items = data.items || [];
    if(tbody){
      tbody.innerHTML = '';
      if(!items.length){
        tbody.innerHTML = '<tr><td colspan="5" class="small" style="opacity:.7;">No files</td></tr>';
      }
      items.forEach(it=>{
        const tr = document.createElement('tr');
        const key = `${utility}:${section}`;
        const selected = state.selections[key]?.has(it.rel);
        tr.innerHTML = `
          <td><input type="checkbox" data-rel="${it.rel}"></td>
          <td title="${it.rel}" class="mono">${it.name}</td>
          <td>${it.is_dir ? '' : humanSize(it.size)}</td>
          <td>${it.mtime ? new Date(it.mtime*1000).toLocaleString() : ''}</td>
          <td><button class="btnGhost" type="button" data-dl="${it.rel}" data-utility="${utility}" data-section="${section}">Download</button></td>
        `;
        const cb = tr.querySelector('input[type=checkbox]');
        if(cb && selected) cb.checked = true;
        tbody.appendChild(tr);
      });
      tbody.querySelectorAll('input[type=checkbox]').forEach(cb=>{
        cb.addEventListener('change', ()=>{
          const key = `${utility}:${section}`;
          if(!state.selections[key]) state.selections[key] = new Set();
          if(cb.checked) state.selections[key].add(cb.dataset.rel);
          else state.selections[key].delete(cb.dataset.rel);
        });
      });
      tbody.querySelectorAll('button[data-dl]').forEach(btn=>{
        btn.addEventListener('click', ()=>{
          const u = btn.dataset.utility, s = btn.dataset.section, rel = btn.dataset.dl;
          window.location.href = `/api/utility-download?utility=${u}&section=${s}&rel=${encodeURIComponent(rel)}`;
        });
      });
    }
    if(status) status.textContent = `${items.length} item(s)`;
  }catch(e){
    if(tbody) tbody.innerHTML = '<tr><td colspan="5" class="small" style="color:#f99;">Failed to load</td></tr>';
  }
}
function loadAll(){
  (panels[state.utility] || []).forEach(p=> loadTable(p.utility, p.section));
}
async function deleteAction(utility, section, all=false){
  const key = `${utility}:${section}`;
  const sels = Array.from(state.selections[key] || []);
  if(!all && !sels.length) return;
  if(all){
    // re-fetch to get all rels
    const res = await fetch(`/api/utility-files?utility=${utility}&section=${section}`, { cache:'no-store' });
    if(!res.ok) return;
    const data = await res.json();
    sels.push(...(data.items||[]).filter(i=>!i.is_dir).map(i=>i.rel));
  }
  if(!sels.length) return;
  if(!confirm(`Delete ${sels.length} file(s)?`)) return;
  await fetch('/api/utility-delete', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ utility, section, rels: sels })
  });
  state.selections[key] = new Set();
  loadAll();
}
document.addEventListener('DOMContentLoaded', ()=>{
  setupUtilMenu('utilToggleFiles','utilDropdownFiles');
  document.querySelectorAll('.sidebar a[data-utility]').forEach(a=>{
    a.addEventListener('click', (e)=>{
      e.preventDefault();
      document.querySelectorAll('.sidebar a[data-utility]').forEach(el=> el.classList.remove('active'));
      a.classList.add('active');
      state.utility = a.dataset.utility;
      state.selections = {};
      renderPanels();
    });
  });
  document.addEventListener('click', (e)=>{
    const btn = e.target.closest('button[data-action]');
    if(!btn) return;
    const action = btn.dataset.action;
    const utility = btn.dataset.utility;
    const section = btn.dataset.section;
    if(action === 'delete-sel') deleteAction(utility, section, false);
    if(action === 'delete-all') deleteAction(utility, section, true);
  });
  renderPanels();
});
</script>
</body>
</html>
"""
TAGGER_HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="icon" type="image/x-icon" href="/favicon.ico">
  <title>Tag Editor - SonusTemper</title>
  <style>
    :root{
      --bg:#0b0f14; --card:#121a23; --muted:#9fb0c0; --text:#e7eef6;
      --line:#203042; --accent:#ff8a3d; --accent2:#2bd4bd; --danger:#ff4d4d;
    }
    body{ margin:0; background:linear-gradient(180deg,#0b0f14,#070a0e); color:var(--text);
      font-family:-apple-system,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
    .wrap{ max-width:1200px; margin:0 auto; padding:22px 18px 40px; }
    h1{ font-size:20px; margin:0 0 6px 0; letter-spacing:.2px; }
    h2{ margin:0 0 10px 0; font-size:16px; }
    .sub{ color:var(--muted); font-size:13px; }
    .top{ display:flex; justify-content:space-between; align-items:flex-start; gap:10px; }
    .card{ background:rgba(18,26,35,.9); border:1px solid var(--line); border-radius:16px; padding:16px; }
    .grid{ display:grid; grid-template-columns: 360px 1fr; gap:16px; align-items:start; margin-top:22px; }
    @media (max-width: 960px){ .grid{ grid-template-columns: 1fr; } }
    .row{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
    .col{ display:flex; flex-direction:column; gap:8px; }
    .btn{ background:linear-gradient(180deg, rgba(255,138,61,.95), rgba(255,138,61,.75));
      border:0; color:#1a0f07; font-weight:800; padding:8px 14px; border-radius:10px; cursor:pointer; }
    .btnGhost{ padding:8px 14px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; cursor:pointer; }
    .btnPrimary{ background:linear-gradient(180deg, #2bd4bd, #1aa390); border:0; color:#062d28; font-weight:800; padding:8px 14px; border-radius:10px; cursor:pointer; }
    .btnDanger{ padding:8px 14px; border-radius:10px; border:1px solid rgba(255,77,77,.35); background:rgba(255,77,77,.15); color:#ffd0d0; cursor:pointer; }
    input[type="text"]{ width:100%; padding:9px 10px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:var(--text); box-sizing:border-box; }
    label{ color:#cfe0f1; font-size:13px; font-weight:600; }
    .small{ color:var(--muted); font-size:12px; }
  .tagList{ margin-top:10px; max-height:none; overflow:visible; display:flex; flex-direction:column; gap:8px; }
  .tagItem{ border:1px solid var(--line); border-radius:12px; padding:10px; background:#0f151d; cursor:pointer; display:flex; justify-content:space-between; align-items:flex-start; gap:8px; }
    .tagItem:hover{ border-color:var(--accent); }
    .tagItem.active{ border-color:var(--accent); box-shadow:0 0 0 1px rgba(255,138,61,0.35); }
    .badge{ font-size:11px; padding:4px 8px; border-radius:999px; background:rgba(255,138,61,0.12); color:#ffb07a; border:1px solid rgba(255,138,61,0.35); }
    .scopeBtns button{ padding:6px 10px; }
    .scopeBtns .active{ border-color:var(--accent); color:var(--text); }
    .utilMenu{ position:relative; }
    .utilToggle{ padding:8px 12px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; cursor:pointer; }
    .utilToggle:hover{ border-color:var(--accent); color:var(--text); }
    .utilDropdown{
      position:absolute; right:0; top:calc(100% + 6px);
      background:#0f151d; border:1px solid var(--line); border-radius:10px;
      min-width:160px; z-index:50; box-shadow:0 8px 22px rgba(0,0,0,0.35);
      display:flex; flex-direction:column; overflow:hidden;
    }
    .utilDropdown a{
      padding:10px 12px; color:#d7e6f5; text-decoration:none; font-size:13px;
    }
    .utilDropdown a:hover{ background:rgba(255,138,61,0.12); color:var(--text); }
    .utilDropdown.hidden{ display:none; }
    .fieldGrid{ display:grid; grid-template-columns: repeat(auto-fit, minmax(240px,1fr)); gap:10px; width:100%; box-sizing:border-box; }
    .fieldGrid label{ display:block; margin-bottom:4px; }
    .artBox{ padding:10px; border:1px dashed var(--line); border-radius:10px; background:rgba(255,255,255,0.02); position:relative; }
    .artThumb{ position:relative; display:inline-block; }
    .artThumb img{ max-width:280px; max-height:280px; border-radius:8px; display:block; }
    .artThumb .artClear{ position:absolute; top:4px; right:4px; background:rgba(0,0,0,0.5); border:1px solid var(--line); color:#fff; border-radius:999px; width:20px; height:20px; display:flex; align-items:center; justify-content:center; cursor:pointer; }
    .placeholder{ color:var(--muted); font-size:13px; }
    .tagRowTitle{ font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .badgeRow{ display:flex; gap:6px; align-items:center; white-space:nowrap; overflow:hidden; margin-top:6px; width:100%; }
    .badge{ font-size:11px; padding:4px 8px; border-radius:999px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; }
    .badge-voicing{ background:rgba(255,138,61,0.2); border-color:rgba(255,138,61,0.6); color:#ffb07a; }
    .badge-param{ background:rgba(43,212,189,0.15); border-color:rgba(43,212,189,0.45); color:#9df1e5; }
  .badge-format{ background:rgba(255,255,255,0.04); border-color:rgba(255,255,255,0.15); color:#cfe0f1; }
  .badge-container{ background:rgba(255,255,255,0.02); border-color:rgba(255,255,255,0.12); color:#9fb0c0; }
    .tagRow{ display:flex; gap:12px; align-items:flex-start; width:100%; }
    .tagRowLeft{ flex:1; min-width:0; display:flex; flex-direction:column; overflow:hidden; }
    .tagRowTitle{ font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%; }
    .badgeRow{ display:flex; gap:6px; align-items:center; white-space:nowrap; overflow:hidden; margin-top:6px; width:100%; }
    .tagActions{ display:flex; align-items:center; gap:8px; margin-left:auto; justify-content:flex-end; }
    .trackDlBtn{ padding:4px 8px; }
    .trackDlBtn:disabled{ opacity:0.35; cursor:not-allowed; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1>Tag Editor</h1>
        <div class="sub">Edit ID3 tags for mastered and imported MP3s.</div>
      </div>
      <div class="utilMenu">
        <button class="utilToggle" id="utilToggleTag" aria-haspopup="true" aria-expanded="false">☰ Utilities</button>
        <div class="utilDropdown hidden" id="utilDropdownTag">
          <a href="/">Mastering</a>
          <div style="height:1px; background:var(--line); margin:4px 0;"></div>
          <a href="/manage-files">File Manager</a>
          <a href="/manage-presets">Preset Manager</a>
          <a href="/tagger">Tag Editor</a>
        </div>
      </div>
    </div>
    <div class="grid">
      <div class="col" style="gap:12px;">
        <div class="card" style="display:flex; flex-direction:column; gap:12px;">
          <div class="row" style="justify-content:space-between; align-items:center;">
            <h2 style="margin:0;">Library</h2>
            <div class="row scopeBtns" id="tagScopeBtns">
              <button class="btnGhost active" data-scope="out">Mastered</button>
              <button class="btnGhost" data-scope="tag">Imported</button>
              <button class="btnGhost" data-scope="all">All</button>
            </div>
          </div>
          <div class="row" style="margin-top:8px; justify-content:space-between; align-items:center;">
            <input type="text" id="tagSearch" placeholder="Search filename..." style="flex:1; min-width:0;">
            <div class="small" id="tagSelectedCount" style="min-width:110px; text-align:right;"></div>
          </div>
          <div id="tagList" class="tagList small"></div>
          <div class="row" style="margin-top:10px; flex-wrap:wrap; justify-content:space-between;">
            <input type="file" id="tagImportFile" accept=".mp3" style="display:none;">
            <button class="btn" type="button" onclick="triggerTagImport()">Import MP3</button>
            <button class="btnGhost" type="button" id="tagSelectAllBtn">Add All (filtered)</button>
          </div>
        </div>
        <div class="card" style="display:flex; flex-direction:column; gap:12px;">
          <div class="row" style="justify-content:space-between; align-items:center; margin-top:4px;">
            <h3 style="margin:0;">Working Set</h3>
            <div class="small">Order = track order</div>
          </div>
          <div id="workingList" class="tagList small" style="max-height:none; overflow:visible;"></div>
          <div class="row" style="flex-wrap:wrap; justify-content:flex-end;">
            <button class="btnGhost" type="button" id="tagClearSelBtn">Clear Working Set</button>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="row" style="justify-content:space-between; align-items:center; margin-bottom:6px;">
          <h2 style="margin:0;">Editor</h2>
          <div class="small" id="tagSaveStatus"></div>
        </div>
        <h3 style="margin:0 0 8px 0;">Album Details</h3>
        <div id="tagAlbumEmpty" class="placeholder">Add files to edit tags.</div>
        <div id="tagAlbumForm" class="col" style="display:none; gap:12px;">
          <div class="fieldGrid">
            <div><label>Album</label><input type="text" id="albAlbum"></div>
            <div><label>Album Artist</label><input type="text" id="albAlbumArtist"></div>
            <div><label>Default Artist</label><input type="text" id="albArtist"></div>
            <div><label>Year</label><input type="text" id="albYear"></div>
            <div><label>Genre</label><input type="text" id="albGenre"></div>
            <div><label>Disc</label><input type="text" id="albDisc" placeholder="e.g., 1/1"></div>
          </div>
          <div>
            <label>Comment</label>
            <input type="text" id="albComment">
          </div>
          <div class="artBox">
            <div class="row" style="justify-content:space-between; align-items:flex-start; margin-bottom:6px; gap:12px;">
              <div class="col" style="gap:6px; flex:1; min-width:0;">
                <div>Artwork: <span id="albArtStatus">Unknown</span></div>
                <input type="file" id="albArtFile" accept=".png,.jpg,.jpeg" style="display:none;">
                <button class="btnGhost" type="button" id="albArtUploadBtn" style="align-self:flex-start;">Upload Artwork…</button>
                <div class="small" id="albArtInfo"></div>
                <div class="small" id="albArtNone" style="color:var(--muted);">No artwork</div>
              </div>
              <div class="artThumb" id="albArtThumb" style="display:none; margin-top:0; margin-left:auto;">
                <img id="albArtImg" />
                <div class="artClear" id="albArtClearBtn">✕</div>
              </div>
            </div>
          </div>
          <div class="row" style="justify-content:space-between; align-items:center;">
            <h4 style="margin:0;">Tracks</h4>
            <div class="row" style="gap:8px;">
              <button class="btnGhost" type="button" id="albAutoNumberBtn">Auto-number</button>
            </div>
          </div>
          <div id="albTableWrap" class="small" style="border:1px solid var(--line); border-radius:10px; padding:8px; max-height:320px; overflow:auto;">
            <table style="width:100%; border-collapse:collapse; color:var(--text); font-size:13px;">
              <thead>
                <tr style="text-align:left;">
                  <th style="padding:6px; width:30px;"></th>
                  <th style="padding:6px;">Track</th>
                  <th style="padding:6px;">Title</th>
                  <th style="padding:6px;">Artist</th>
                  <th style="padding:6px;">Filename</th>
                </tr>
              </thead>
              <tbody id="albTableBody"></tbody>
            </table>
          </div>
          <div class="row" style="gap:10px; flex-wrap:wrap; justify-content:flex-start;">
            <button class="btnPrimary" type="button" id="albApplyBtn">Save</button>
            <button class="btnGhost" type="button" id="albDownloadBtn">Download Zip</button>
          </div>
          <div id="albStatus" class="small"></div>
        </div>
      </div>
    </div>
    <div class="small" style="margin-top:12px; color:var(--muted);">SonusTemper v{{VERSION}} – Tag Editor</div>
  </div>
<script>
const tagState = {
  scope: 'out',
  items: [],
  filtered: [],
  selectedId: null,
  working: [],
  selectedIds: new Set(),
  fileDetails: {},
  artInfoCache: {},
  albumArt: { mode:'keep', uploadId:null, mime:null, size:0, preview:null },
  loading: false,
  dirty: false,
};
function markDirty(){
  tagState.dirty = true;
  updateDownloadState();
}
function updateDownloadState(){
  const zipBtn = document.getElementById('albDownloadBtn');
  if(zipBtn){
    zipBtn.disabled = tagState.dirty || !tagState.working.length;
  }
  document.querySelectorAll('.trackDlBtn').forEach(btn=>{
    btn.disabled = tagState.dirty;
  });
}
const TAG_BADGE_GAP = 6;
let badgeMeasureHost = null;
function setupUtilMenu(toggleId, menuId){
  const toggle = document.getElementById(toggleId);
  const menu = document.getElementById(menuId);
  if(!toggle || !menu) return;
  const close = ()=>{ menu.classList.add('hidden'); toggle.setAttribute('aria-expanded','false'); };
  toggle.addEventListener('click', (e)=>{
    e.stopPropagation();
    const isOpen = !menu.classList.contains('hidden');
    if(isOpen){ close(); } else { menu.classList.remove('hidden'); toggle.setAttribute('aria-expanded','true'); }
  });
  document.addEventListener('click', (e)=>{
    if(!menu.contains(e.target) && e.target !== toggle){ close(); }
  });
}
function tagToast(msg){ const s=document.getElementById('tagSaveStatus'); if(s) s.textContent=msg||''; }
function ensureBadgeMeasureHost(){
  if(badgeMeasureHost) return badgeMeasureHost;
  const host = document.createElement('div');
  host.style.position = 'absolute';
  host.style.visibility = 'hidden';
  host.style.pointerEvents = 'none';
  host.style.top = '-9999px';
  host.style.left = '-9999px';
  host.style.display = 'flex';
  host.style.gap = `${TAG_BADGE_GAP}px`;
  document.body.appendChild(host);
  badgeMeasureHost = host;
  return host;
}
function measureBadgeWidth(badgeEl){
  const host = ensureBadgeMeasureHost();
  host.appendChild(badgeEl);
  const w = badgeEl.offsetWidth;
  host.removeChild(badgeEl);
  return w;
}
function makeBadge(label, type, title){
  const span = document.createElement('span');
  span.className = 'badge' + (type ? ` badge-${type}` : '');
  span.textContent = label;
  if(title) span.title = title;
  return span;
}
function badgeTitle(text, tooltip){
  const div = document.createElement('div');
  div.className = 'tagRowTitle';
  div.textContent = text || '';
  if(tooltip) div.title = tooltip;
  return div;
}
function computeVisibleBadges(badges, containerWidth){
  if(!badges || !badges.length || !containerWidth) return { visible: badges || [], hidden: [] };
  const pinned = [];
  const seenPinned = new Set();
  badges.forEach(b=>{
    if(b && (b.type === 'voicing' || b.type === 'preset')){
      const key = `${b.type}:${b.label}`;
      if(!seenPinned.has(key)){
        pinned.push(b);
        seenPinned.add(key);
      }
    }
  });
  const rest = badges.filter(b=>!(b && (b.type === 'voicing' || b.type === 'preset')));
  const ordered = [...pinned, ...rest];
  if(!ordered.length) return { visible: [], hidden: [] };

  // Pre-measure widths and total
  const widths = ordered.map(b => {
    const el = makeBadge(b.label || '', b.type || '', b.title);
    return measureBadgeWidth(el);
  });
  const totalWidth = widths.reduce((acc,w,idx)=> acc + w + (idx>0 ? TAG_BADGE_GAP : 0), 0);
  if(totalWidth <= containerWidth){
    return { visible: ordered, hidden: [] };
  }
  // Need overflow handling; reserve space for +N
  const reserveBadge = makeBadge("+99", "format");
  const reserveWidth = measureBadgeWidth(reserveBadge);
  const available = Math.max(0, containerWidth - reserveWidth - TAG_BADGE_GAP);
  let used = 0;
  const visible = [];
  let hiddenStart = ordered.length;
  for(let i=0;i<ordered.length;i++){
    const w = widths[i] + (visible.length ? TAG_BADGE_GAP : 0);
    if(used + w <= available){
      visible.push(ordered[i]);
      used += w;
    }else{
      hiddenStart = i;
      break;
    }
  }
  const hidden = ordered.slice(hiddenStart);
  return { visible, hidden };
}
function renderBadges(badges, container){
  const wrap = container || document.createElement('div');
  wrap.className = 'badgeRow';
  wrap.innerHTML = '';
  if(!badges || !badges.length) return wrap;
  let width = wrap.parentElement ? wrap.parentElement.clientWidth : 0;
  if(!width) width = wrap.getBoundingClientRect().width || wrap.clientWidth;
  if(!width) width = 320;
  const { visible, hidden } = computeVisibleBadges(badges, width);
  visible.forEach(b => {
    const lbl = b.label || '';
    const type = b.type || '';
    wrap.appendChild(makeBadge(lbl, type, b.title));
  });
  if(hidden.length > 0){
    const more = makeBadge(`+${hidden.length}`, 'format', hidden.map(b=>b.title || b.label).join(', '));
    wrap.appendChild(more);
  }
  return wrap;
}
function fileListRow(item){
  const row = document.createElement('div');
  row.className = 'tagItem' + (tagState.selectedId === item.id ? ' active' : '');
  row.title = item.full_name || item.basename || item.relpath || '';
  const left = document.createElement('div');
  left.className = 'tagRow';
  left.style.flex = '1';
  left.style.minWidth = '0';
  const leftCol = document.createElement('div');
  leftCol.className = 'tagRowLeft';
  const titleText = item.display_title || item.basename || item.relpath || '(untitled)';
  const titleNode = badgeTitle(titleText, item.full_name || item.basename || item.relpath || titleText);
  leftCol.appendChild(titleNode);
  const badgeRow = document.createElement('div');
  badgeRow.className = 'badgeRow';
  badgeRow.dataset.badges = JSON.stringify(item.badges || []);
  leftCol.appendChild(badgeRow);
  left.appendChild(leftCol);
  row.appendChild(left);
  return row;
}
function renderTagList(){
  const list = document.getElementById('tagList');
  if(!list) return;
  const q = (document.getElementById('tagSearch')?.value || '').toLowerCase();
  const workingIds = new Set(tagState.working.map(w=>w.id));
  const items = tagState.items.filter(it => !workingIds.has(it.id)).filter(it => !q || it.basename.toLowerCase().includes(q));
  tagState.filtered = items;
  list.innerHTML = '';
  if(!items.length){
    list.innerHTML = '<div class="small" style="opacity:.7;">No MP3s found.</div>';
    updateSelectedCount();
    return;
  }
  updateSelectedCount();
  items.forEach(it => {
    const enriched = {
      ...it,
      display_title: it.display_title || it.basename || it.relpath || it.id,
      badges: (it.badges && Array.isArray(it.badges) ? it.badges : []).slice(),
    };
    if(!enriched.display_title || enriched.display_title === enriched.root){
      enriched.display_title = it.basename || it.relpath || it.id || '(untitled)';
    }
    if(!enriched.badges.length){
      enriched.badges.push({ label: it.root === 'out' ? 'Mastered' : 'Imported', type:'format' });
    }
    const row = document.createElement('div');
    row.className = 'tagItem' + (tagState.selectedId === it.id ? ' active' : '');
    row.dataset.id = it.id;
    const left = document.createElement('div');
    left.className = 'tagRow';
    left.style.flex = '1';
    left.style.minWidth = '0';
    const leftCol = document.createElement('div');
    leftCol.className = 'tagRowLeft';
    const titleText = enriched.display_title || enriched.basename || enriched.relpath || '(untitled)';
    const titleNode = badgeTitle(titleText, enriched.full_name || enriched.basename || enriched.relpath || titleText);
    leftCol.appendChild(titleNode);
    const badgeRow = document.createElement('div');
    badgeRow.className = 'badgeRow';
    badgeRow.dataset.badges = JSON.stringify(enriched.badges || []);
    leftCol.appendChild(badgeRow);
    left.appendChild(leftCol);
    const right = document.createElement('div');
    right.className = 'tagActions';
    right.style.marginLeft = 'auto';
    right.style.flex = '0 0 auto';
    const btn = document.createElement('button');
    btn.className = 'btnGhost';
    btn.textContent = 'Add';
    btn.style.marginLeft = 'auto';
    btn.addEventListener('click', (e)=>{
      e.stopPropagation();
      addToWorking(enriched);
    });
    right.appendChild(btn);
    row.appendChild(left);
    row.appendChild(right);
  row.addEventListener('click', ()=> { addToWorking(enriched); });
  list.appendChild(row);
});
  queueBadgeLayout();
}
function layoutBadgeRows(){
  const rows = document.querySelectorAll('.badgeRow');
  rows.forEach(br=>{
    let badges = [];
    try { badges = JSON.parse(br.dataset.badges || '[]'); } catch {}
    renderBadges(badges, br);
  });
}
let badgeLayoutRaf = null;
function queueBadgeLayout(){
  if(badgeLayoutRaf) cancelAnimationFrame(badgeLayoutRaf);
  badgeLayoutRaf = requestAnimationFrame(()=>{ badgeLayoutRaf = null; layoutBadgeRows(); });
}
window.addEventListener('resize', queueBadgeLayout);
const tagListObserver = new ResizeObserver(() => queueBadgeLayout());
document.addEventListener('DOMContentLoaded', () => {
  const list = document.getElementById('tagList');
  if(list) tagListObserver.observe(list);
});
async function fetchTagList(scope = 'out'){
  tagState.scope = scope;
  try{
    const res = await fetch(`/api/tagger/mp3s?scope=${encodeURIComponent(scope)}`, { cache:'no-store' });
    if(!res.ok){
      const txt = await res.text();
      throw new Error(`HTTP ${res.status} ${txt || ''}`);
    }
    const data = await res.json();
    const workingIds = new Set(tagState.working.map(w=>w.id));
    tagState.items = (data.items || []).filter(it => !workingIds.has(it.id));
    renderTagList();
  }catch(e){
    console.error('tagger list error', e);
    const list = document.getElementById('tagList');
    if(list) list.innerHTML = '<div class="small" style="color:#f99;">Failed to load list.</div>';
  }
}
async function selectTagFile(id, skipRender=false){
  tagState.selectedId = id;
  if(!skipRender){
    renderTagList();
    renderWorkingList();
  }
  const detailEmpty = document.getElementById('tagDetailEmpty');
  const detailForm = document.getElementById('tagDetailForm');
  if(detailEmpty) detailEmpty.style.display = 'none';
  if(detailForm) detailForm.style.display = 'none'; // track form hidden in unified view
  try{
    const res = await fetch(`/api/tagger/file/${encodeURIComponent(id)}`, { cache:'no-store' });
    if(!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if(data && data.id){
      tagState.fileDetails[data.id] = data;
    }
    tagToast('');
  }catch(e){
    tagToast('Failed to load tags.');
  }
}
// Legacy track save (unused with unified album view) removed
function triggerTagImport(){
  const input = document.getElementById('tagImportFile');
  if(input) input.click();
}
  document.addEventListener('DOMContentLoaded', ()=>{
    setupUtilMenu('utilToggleTag','utilDropdownTag');
    document.getElementById('tagSearch')?.addEventListener('input', renderTagList);
  document.getElementById('tagImportFile')?.addEventListener('change', async (e)=>{
    const status = document.getElementById('tagImportStatus');
    const setStatus = (msg) => {
      if(status) status.textContent = msg || '';
      else tagToast(msg || '');
    };
    const file = e.target.files[0];
    if(!file){ return; }
    setStatus('Uploading...');
    const fd = new FormData();
    fd.append('file', file, file.name);
    try{
      const res = await fetch('/api/tagger/import', { method:'POST', body: fd });
      if(!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setStatus('Imported.');
      await fetchTagList(tagState.scope);
      if(data && data.id){ selectTagFile(data.id); }
    }catch(err){
      setStatus('Import failed.');
    }finally{
      e.target.value = '';
    }
  });
  document.querySelectorAll('#tagScopeBtns button').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      document.querySelectorAll('#tagScopeBtns button').forEach(b=>b.classList.remove('active'));
      btn.classList.add('active');
      fetchTagList(btn.dataset.scope || 'out');
    });
  });
  fetchTagList('out');
  updateDownloadState();
});

// Selection helpers
document.getElementById('tagSelectAllBtn').addEventListener('click', ()=>{
  tagState.filtered.forEach(i=> addToWorking(i));
});
document.getElementById('tagClearSelBtn').addEventListener('click', ()=>{
  tagState.working = [];
  tagState.selectedId = null;
  syncWorkingSelected();
  renderWorkingList();
  renderTagList();
  updateEditorView();
  tagState.dirty = false;
  updateDownloadState();
});

function updateSelectedCount(){
  const el = document.getElementById('tagSelectedCount');
  if(el) el.textContent = `${tagState.selectedIds.size} selected`;
}
function syncWorkingSelected(){
  tagState.selectedIds = new Set(tagState.working.map(w=>w.id));
  updateSelectedCount();
}
function addToWorking(item){
  if(!item || !item.id) return;
  if(tagState.working.find(w=>w.id === item.id)) return;
  tagState.working.push(item);
  if(!tagState.selectedId) tagState.selectedId = item.id;
  syncWorkingSelected();
  renderWorkingList();
  renderTagList();
  updateEditorView();
  updateDownloadState();
}
function removeFromWorking(id){
  tagState.working = tagState.working.filter(w=>w.id !== id);
  if(tagState.selectedId === id){
    tagState.selectedId = tagState.working.length ? tagState.working[0].id : null;
  }
  syncWorkingSelected();
  renderWorkingList();
  renderTagList();
  updateEditorView();
}
function renderWorkingList(){
  const list = document.getElementById('workingList');
  if(!list) return;
  list.innerHTML = '';
  if(!tagState.working.length){
    list.innerHTML = '<div class="small" style="opacity:.7;">Add files from Library to start.</div>';
    return;
  }
  tagState.working.forEach(it=>{
    const row = document.createElement('div');
    row.className = 'tagItem' + (tagState.selectedId === it.id ? ' active' : '');
    row.dataset.id = it.id;
    const left = document.createElement('div');
    left.className = 'tagRow';
    left.style.flex = '1';
    left.style.minWidth = '0';
    const leftCol = document.createElement('div');
    leftCol.className = 'tagRowLeft';
    leftCol.appendChild(badgeTitle(it.display_title || it.basename || it.relpath || '(untitled)', it.full_name || it.basename || it.relpath || ''));
    const badgeRow = document.createElement('div');
    badgeRow.className = 'badgeRow';
    badgeRow.dataset.badges = JSON.stringify(it.badges || []);
    leftCol.appendChild(badgeRow);
    left.appendChild(leftCol);
    const right = document.createElement('div');
    right.className = 'tagActions';
    right.style.marginLeft = 'auto';
    right.style.flex = '0 0 auto';
    const rem = document.createElement('button');
    rem.className = 'btnGhost';
    rem.textContent = '✕';
    rem.style.padding = '4px 8px';
    rem.style.marginLeft = 'auto';
    rem.addEventListener('click',(e)=>{ e.stopPropagation(); removeFromWorking(it.id); });
    right.appendChild(rem);
    row.appendChild(left);
    row.appendChild(right);
    row.addEventListener('click', ()=> { tagState.selectedId = it.id; renderWorkingList(); selectTagFile(it.id, true); updateEditorView({fromSelection:true}); });
    list.appendChild(row);
  });
  queueBadgeLayout();
}
function updateEditorView(opts={}){
  const fromSelection = opts.fromSelection || false;
  const albumPane = document.getElementById('tagAlbumForm');
  const albumEmpty = document.getElementById('tagAlbumEmpty');
  if(!tagState.working.length){
    if(albumEmpty) albumEmpty.style.display = 'block';
    if(albumPane) albumPane.style.display = 'none';
    return;
  }
  syncWorkingSelected();
  if(albumEmpty) albumEmpty.style.display = 'none';
  if(albumPane) albumPane.style.display = 'flex';
  renderAlbumForm();
  if(!fromSelection){
    const targetId = tagState.selectedId || tagState.working[0].id;
    tagState.selectedId = targetId;
    ensureFileDetail(targetId);
  }
}

async function ensureFileDetail(id){
  if(tagState.fileDetails[id]) return tagState.fileDetails[id];
  try{
    const res = await fetch(`/api/tagger/file/${encodeURIComponent(id)}`, { cache:'no-store' });
    if(!res.ok) throw new Error();
    const data = await res.json();
    tagState.fileDetails[id] = data;
    return data;
  }catch(e){
    return null;
  }
}
function toggleSelectId(id, checked){
  if(checked){ tagState.selectedIds.add(id); ensureFileDetail(id).then(renderAlbumForm); }
  else tagState.selectedIds.delete(id);
  updateSelectedCount();
  renderAlbumForm();
}
function renderAlbumForm(){
  const sel = tagState.working.map(w=>w.id);
  const empty = document.getElementById('tagAlbumEmpty');
  const form = document.getElementById('tagAlbumForm');
  if(!form || !empty) return;
  if(!sel.length){
    empty.style.display = 'block';
    form.style.display = 'none';
    updateDownloadState();
    return;
  }
  empty.style.display = 'none';
  form.style.display = 'flex';
  const tbody = document.getElementById('albTableBody');
  tbody.innerHTML = '';
  // artwork mixed status
  let artPresent = null;
  sel.forEach((id, idx)=>{
    const row = document.createElement('tr');
    row.dataset.id = id;
    const detail = tagState.fileDetails[id];
    if(!detail){
      ensureFileDetail(id).then(renderAlbumForm);
    }
    const tags = detail?.tags || {};
    const base = detail?.basename || detail?.display_title || id;
    const trackVal = tags.track || '';
    const titleVal = tags.title || (detail?.display_title) || base;
    const artistVal = tags.artist || '';
    const discVal = tags.disc || '';
    const tds = [
      `<button class="btnGhost trackDlBtn" type="button" data-id="${id}" title="Download track" style="padding:4px 8px;">⏬</button>`,
      `<input name="albTrack" style="width:80px;" value="${trackVal || ''}">`,
      `<input name="albTitle" style="width:100%;" value="${titleVal || ''}">`,
      `<input name="albArtist" style="width:120px;" value="${artistVal || ''}">`,
      `<div title="${base}" style="max-width:180px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${base}</div>`,
    ];
    tds.forEach(html=>{
      const td = document.createElement('td');
      td.style.padding = '6px';
      td.innerHTML = html;
      row.appendChild(td);
    });
    tbody.appendChild(row);
    const present = !!(tags.artwork && tags.artwork.present);
    if(artPresent === null) artPresent = present;
    else if(artPresent !== present) artPresent = 'mixed';
  });
  updateArtworkStatus(sel);
  document.querySelectorAll('.trackDlBtn').forEach(btn=>{
    btn.disabled = tagState.dirty;
    btn.onclick = ()=> downloadSingle(btn.dataset.id);
  });
  updateDownloadState();
  // mark dirty on field edits
  document.querySelectorAll('#tagAlbumForm input').forEach(inp=>{
    inp.oninput = markDirty;
  });
}

async function fetchArtInfo(ids){
  if(!ids || !ids.length) return;
  const missing = ids.filter(id => !tagState.artInfoCache[id]);
  await Promise.all(missing.map(async (id)=>{
    try{
      const res = await fetch(`/api/tagger/file/${encodeURIComponent(id)}/artwork-info`, { cache:'no-store' });
      if(!res.ok) throw new Error();
      const data = await res.json();
      tagState.artInfoCache[id] = data;
    }catch(_){
      tagState.artInfoCache[id] = { present:false, sha256:null, mime:null };
    }
  }));
}
function updateArtworkStatus(sel){
  const artStatus = document.getElementById('albArtStatus');
  const artNone = document.getElementById('albArtNone');
  const artThumb = document.getElementById('albArtThumb');
  const artImg = document.getElementById('albArtImg');
  const info = document.getElementById('albArtInfo');
  // If user uploaded new art, show preview and skip mixed check
  if(tagState.albumArt.preview){
    if(info) info.textContent = 'Uploaded (pending apply)';
    if(artImg && artThumb){
      artImg.src = URL.createObjectURL(tagState.albumArt.preview);
      artThumb.style.display = 'inline-block';
    }
    if(artStatus) artStatus.textContent = 'Uploaded (pending apply)';
    if(artNone) artNone.style.display = 'none';
    return;
  }
  if(!sel.length){
    if(artStatus) artStatus.textContent = 'No artwork';
    if(artNone) artNone.style.display = 'block';
    if(artThumb) artThumb.style.display = 'none';
    return;
  }
  if(sel.length === 1){
    const fid = sel[0];
    const detail = tagState.fileDetails[fid];
    const present = !!(detail?.tags?.artwork && detail.tags.artwork.present);
    if(present){
      if(artImg && artThumb){
        artImg.src = `/api/tagger/file/${encodeURIComponent(fid)}/artwork?cb=${Date.now()}`;
        artThumb.style.display = 'inline-block';
      }
      if(artStatus) artStatus.textContent = 'Present';
      if(artNone) artNone.style.display = 'none';
    }else{
      if(artStatus) artStatus.textContent = 'No artwork';
      if(artNone) artNone.style.display = 'block';
      if(artThumb) artThumb.style.display = 'none';
    }
    return;
  }
  // multi: fetch infos and compute state
  fetchArtInfo(sel).then(()=>{
    const infos = sel.map(id => tagState.artInfoCache[id]);
    const allNone = infos.length && infos.every(i => i && i.present === false);
    const allPresent = infos.length && infos.every(i => i && i.present);
    const sameHash = allPresent && infos.every(i => i.sha256 === infos[0].sha256);
    if(artThumb) artThumb.style.display = 'none';
    if(allNone){
      if(artStatus) artStatus.textContent = 'No artwork in current working set.';
      if(artNone) artNone.style.display = 'block';
    }else if(allPresent && sameHash){
      if(artStatus) artStatus.textContent = 'Artwork is consistent across working set.';
      if(artNone) artNone.style.display = 'none';
      if(artImg && artThumb && sel[0]){
        artImg.src = `/api/tagger/file/${encodeURIComponent(sel[0])}/artwork?cb=${Date.now()}`;
        artThumb.style.display = 'inline-block';
      }
    }else{
      if(artStatus) artStatus.textContent = 'Current working set artwork varies.';
      if(artNone) artNone.style.display = 'none';
    }
  });
}

// Artwork upload for album
document.getElementById('albArtFile').addEventListener('change', async (e)=>{
  const file = e.target.files[0];
  if(!file) return;
  const info = document.getElementById('albArtInfo');
  info.textContent = 'Uploading...';
  const fd = new FormData();
  fd.append('file', file, file.name);
  try{
    const res = await fetch('/api/tagger/artwork', { method:'POST', body: fd });
    if(res.status === 413){
      throw new Error('size_exceeded');
    }
    if(!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    tagState.albumArt = { mode:'apply', uploadId:data.upload_id || data.uploadId || data.uploadId, mime:data.mime, size:data.size, preview:file };
    info.textContent = `Ready to apply (${(data.size/1024).toFixed(1)} KB)`;
    document.getElementById('albArtStatus').textContent = 'Uploaded (pending apply)';
    const thumb = document.getElementById('albArtThumb');
    const img = document.getElementById('albArtImg');
    const none = document.getElementById('albArtNone');
    if(img){ img.src = URL.createObjectURL(file); }
    if(thumb){ thumb.style.display = 'inline-block'; }
    if(none){ none.style.display = 'none'; }
    markDirty();
  }catch(err){
    if(err && err.message === 'size_exceeded'){
      info.textContent = 'File size exceeded.';
    }else{
      info.textContent = 'Upload failed';
    }
  }finally{
    e.target.value = '';
  }
});
document.getElementById('albArtUploadBtn').addEventListener('click', ()=>{
  const input = document.getElementById('albArtFile');
  if(input) input.click();
});
document.getElementById('albArtClearBtn').addEventListener('click', ()=>{
  tagState.albumArt = { mode:'clear', uploadId:null, mime:null, size:0, preview:null };
  document.getElementById('albArtStatus').textContent = 'Will clear artwork';
  document.getElementById('albArtInfo').textContent = '';
  const thumb = document.getElementById('albArtThumb');
  const img = document.getElementById('albArtImg');
  const none = document.getElementById('albArtNone');
  if(img){ img.src=''; }
  if(thumb){ thumb.style.display = 'none'; }
  if(none){ none.style.display = 'block'; }
  markDirty();
});

// Album auto-number
document.getElementById('albAutoNumberBtn').addEventListener('click', ()=>{
  const ids = tagState.working.map(w=>w.id);
  ids.forEach((id, idx)=>{
    const row = document.querySelector(`tr[data-id="${id}"]`);
    if(row){
      const inp = row.querySelector('input[name="albTrack"]');
      if(inp) inp.value = `${idx+1}/${ids.length}`;
    }
  });
  markDirty();
});

// Album apply / save
document.getElementById('albApplyBtn').addEventListener('click', async ()=>{
  const status = document.getElementById('albStatus');
  const ids = tagState.working.map(w=>w.id);
  if(ids.length === 0){ status.textContent = 'No tracks selected.'; return; }
  status.textContent = 'Applying...';
  const shared = {
    album: document.getElementById('albAlbum').value,
    album_artist: document.getElementById('albAlbumArtist').value,
    artist: document.getElementById('albArtist').value,
    year: document.getElementById('albYear').value,
    genre: document.getElementById('albGenre').value,
    comment: document.getElementById('albComment').value,
    disc: document.getElementById('albDisc').value,
  };
  const tracks = [];
  document.querySelectorAll('#albTableBody tr').forEach(tr=>{
    const id = tr.dataset.id;
    const val = (sel)=>{ const el = tr.querySelector(sel); return el ? el.value : ''; };
    tracks.push({
      id,
      track: val('input[name="albTrack"]'),
      title: val('input[name="albTitle"]'),
      artist: val('input[name="albArtist"]'),
      disc: val('input[name="albDisc"]'),
    });
  });
  const payload = {
    file_ids: ids,
    shared,
    tracks,
    artwork: { mode: tagState.albumArt.mode || 'keep', upload_id: tagState.albumArt.uploadId }
  };
  try{
    const res = await fetch('/api/tagger/album/apply', {
      method:'POST',
      headers:{ 'Content-Type':'application/json' },
      body: JSON.stringify(payload),
    });
    if(!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    status.textContent = `Updated ${data.updated.length} files${data.errors?.length? ', errors: '+data.errors.length:''}`;
    renderTagList();
    tagState.dirty = false;
    tagState.albumArt = { mode:'keep', uploadId:null, mime:null, size:0, preview:null };
    updateDownloadState();
  }catch(err){
    status.textContent = 'Apply failed';
  }
});

// Album download
function downloadZip(){
  const ids = tagState.working.map(w=>w.id);
  if(!ids.length || tagState.dirty) return;
  const name = document.getElementById('albAlbum').value || 'album';
  const q = encodeURIComponent(ids.join(','));
  const n = encodeURIComponent(name);
  window.location.href = `/api/tagger/album/download?ids=${q}&name=${n}`;
}
function downloadSingle(id){
  if(tagState.dirty) return;
  if(!id) return;
  window.location.href = `/api/tagger/file/${encodeURIComponent(id)}/download`;
}
const albDlBtn = document.getElementById('albDownloadBtn');
if(albDlBtn) albDlBtn.addEventListener('click', downloadZip);
</script>
</body>
</html>
"""

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
    for fp in _preset_paths():
        if fp.stem == name:
            target = fp
            break
    if not target:
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
async def preset_generate(file: UploadFile = File(...)):
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
    return HTMLResponse(content=svg, media_type="image/svg+xml")
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

# Mount the new /ui routes if available
if new_ui_router:
    app.include_router(new_ui_router, prefix="/ui")
