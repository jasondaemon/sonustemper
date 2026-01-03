import json
import shutil
import subprocess
import shlex
import re
import threading
import sys
import tempfile
from pathlib import Path
from datetime import datetime
import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
IN_DIR = Path(os.getenv("IN_DIR", str(DATA_DIR / "in")))
OUT_DIR = Path(os.getenv("OUT_DIR", str(DATA_DIR / "out")))
PRESET_DIR = Path(os.getenv("PRESET_DIR", "/presets"))

APP_DIR = Path(__file__).resolve().parent
_default_master = APP_DIR / "mastering" / "master.py"
_default_pack = APP_DIR / "mastering" / "master_pack.py"
if not _default_master.exists():
    try:
        _default_master = APP_DIR.parents[2] / "mastering" / "master.py"
        _default_pack = APP_DIR.parents[2] / "mastering" / "master_pack.py"
    except Exception:
        pass

MASTER_ONE = Path(os.getenv("MASTER_ONE", str(_default_master)))
MASTER_PACK = Path(os.getenv("MASTER_PACK", str(_default_pack)))

app = FastAPI()

OUT_DIR.mkdir(parents=True, exist_ok=True)
IN_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/out", StaticFiles(directory=str(OUT_DIR), html=True), name="out")

def read_metrics_for_wav(wav: Path) -> dict | None:
    mp = wav.with_suffix(".metrics.json")
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "metrics_read_failed"}

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
    return read_metrics_for_wav(wavs[0])

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

def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

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

def basic_metrics(path: Path) -> dict:
    info = docker_ffprobe_json(path)
    duration = None
    try:
        duration = float(info.get("format", {}).get("duration"))
    except Exception:
        duration = None
    loud = measure_loudness(path)
    m = {
        "I": loud.get("I"),
        "TP": loud.get("TP"),
        "LRA": loud.get("LRA"),
        "short_term_max": None,
        "crest_factor": None,
        "stereo_corr": None,
        "duration_sec": duration,
    }
    return m

def find_input_file(song: str) -> Path | None:
    candidates = sorted([p for p in IN_DIR.iterdir() if p.is_file() and p.stem == song])
    return candidates[0] if candidates else None

def fill_input_metrics(song: str, m: dict, folder: Path) -> dict:
    if not m:
        return m
    if m.get("input") and m["input"].get("I") is not None:
        return m
    inp = find_input_file(song)
    if not inp:
        return m
    try:
        m["input"] = basic_metrics(inp)
        out = m.get("output") or {}
        deltas = {}
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
git_rev = None
try:
    git_rev = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
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
    .wrap{ max-width:1200px; margin:0 auto; padding:26px 18px 40px; }
    .top{ display:flex; gap:14px; align-items:flex-end; justify-content:space-between; flex-wrap:wrap; }
    h1{ font-size:20px; margin:0; letter-spacing:.2px; }
    .sub{ color:var(--muted); font-size:13px; margin-top:6px; }
    .grid{ display:grid; grid-template-columns: 1fr 1.2fr; gap:14px; margin-top:16px; }
    @media (max-width: 980px){ .grid{ grid-template-columns:1fr; } }
    .card{ background:rgba(18,26,35,.9); border:1px solid var(--line); border-radius:16px; padding:16px; }
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
    .manage-wrap{ padding:14px; border:1px solid var(--line); border-radius:14px; background:#0b121d; margin-top:10px; }
    .manage-list{ display:flex; flex-direction:column; gap:8px; }
    .manage-item{ display:flex; justify-content:space-between; align-items:center; padding:8px 10px; border:1px solid var(--line); border-radius:10px; }
    .smallBtn{ padding:6px 10px; font-size:12px; border-radius:10px; border:1px solid var(--line); background:#0f151d; color:#d7e6f5; cursor:pointer; }
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
.section-gap{ border-top:1px solid var(--line); margin:16px 0 0 0; padding-top:12px; }
.section-title{ margin:0 0 6px 0; font-size:14px; color:#cfe0f1; }
</style>

</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1>SonusTemper</h1>
        <div class="sub">
          <span class="pill">IN: <span class="mono">{{IN_DIR}}</span></span>
          <span class="pill">OUT: <span class="mono">{{OUT_DIR}}</span></span>
        </div>
      </div>
      <div class="row">
        <button class="btnGhost" onclick="refreshAll()">Refresh lists</button>
<div id="statusMsg" class="small" style="margin-top:8px;opacity:.85"></div>
      </div>
    </div>

<div class="grid">
      <div class="card" id="masterView">
        <h2>Upload</h2>
        <form id="uploadForm">
          <div class="row">
            <input type="file" id="file" name="files" accept=".wav,.mp3,.flac,.aiff,.aif" multiple required />
            <button class="btn2" type="submit">Upload</button>
            <button class="btnGhost" type="button" onclick="showManage()">Manage</button>
          </div>
        </form>
        <div id="uploadResult" class="small" style="margin-top:10px;"></div>

        <div class="section-gap"></div>

        <h2>Previous Runs</h2>
        <div class="small">Click a run to load outputs. Delete removes the entire song output folder.</div>
        <div id="recent" class="outlist" style="margin-top:10px;"></div>
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

      <div class="card">
        <h2>Master</h2>
        <div class="hidden"><select id="infile"></select></div>

        <div class="control-row" style="align-items:flex-start; margin-top:6px;">
          <label style="min-width:140px;">Input files</label>
          <div id="bulkFilesBox" class="small" style="flex:1; display:flex; flex-wrap:wrap; gap:8px;"></div>
          <div class="small" style="display:flex; flex-direction:column; gap:6px;">
            <button class="btnGhost" type="button" onclick="selectAllBulk()">Select all</button>
            <button class="btnGhost" type="button" onclick="clearAllBulk()">Clear</button>
          </div>
        </div>

        <div class="control-row" style="align-items:flex-start; margin-top:8px;">
          <label style="min-width:140px;">Presets</label>
          <div id="packPresetsBox" class="small" style="flex:1; display:flex; flex-wrap:wrap; gap:8px;"></div>
          <div class="small" style="display:flex; flex-direction:column; gap:6px;">
            <button class="btnGhost" type="button" onclick="selectAllPackPresets()">Select all</button>
            <button class="btnGhost" type="button" onclick="clearAllPackPresets()">Clear</button>
          </div>
        </div>

        <div class="control-row">
          <label>Strength</label>
          <input type="range" id="strength" min="0" max="100" value="80" oninput="strengthVal.textContent=this.value">
          <span class="pill">S=<span id="strengthVal">80</span></span>
        </div>

        <div class="hr"></div>

        <div class="control-row">
          <label>Loudness Mode</label>
          <div style="display:flex; align-items:center; gap:8px;">
            <select id="loudnessMode"></select>
            <button class="info-btn" type="button" data-info-type="loudness" aria-label="About loudness profiles">ⓘ</button>
          </div>
        </div>
        <div class="small" id="loudnessHint" style="margin-top:-6px; margin-bottom:6px; color:var(--muted);"></div>

        <div id="overrides">
          <div class="control-row">
            <label><input type="checkbox" id="useLufs"> Override Target LUFS</label>
            <input type="range" id="lufs" min="-20" max="-8" step="0.5" value="-14">
            <span class="pill" id="lufsVal">-14.0 LUFS</span>
          </div>

          <div class="control-row">
            <label><input type="checkbox" id="useTp"> Override True Peak (TP)</label>
            <input type="range" id="tp" min="-3.0" max="0.0" step="0.1" value="-1.0">
            <span class="pill" id="tpVal">-1.0 dBTP</span>
          </div>

          <div class="control-row">
            <label><input type="checkbox" id="ov_width"> Override Stereo Width</label>
            <input type="range" id="width" min="0.90" max="1.40" step="0.01" value="1.12">
            <span class="pill" id="widthVal">1.12</span>
          </div>

          <div class="control-row">
            <label><input type="checkbox" id="guardrails"> Enable Width Guardrails</label>
            <div class="small" style="color:var(--muted);">Keeps lows mono-ish and softly caps extreme width if risky.</div>
          </div>

          <div class="control-row">
            <label><input type="checkbox" id="ov_mono_bass"> Mono Bass Below (Hz)</label>
            <input type="range" id="mono_bass" min="60" max="200" step="5" value="120">
            <span class="pill" id="monoBassVal">120</span>
          </div>
        </div>

        <div class="row" style="margin-top:12px;">
          <button class="btn" id="runPackBtn" onclick="runPack()">Run Master</button>
          <button class="btnGhost" id="runBulkBtn" onclick="runBulk()">Run on selected files</button>
        </div>

        <div class="section-gap"></div>
        <h3 class="section-title">Job Output</h3>
        <div id="result" class="result">(waiting)</div>

        <div class="section-gap"></div>
        <h3 class="section-title">Metrics</h3>
        <div id="metricsPanel" class="small" style="margin-top:6px;"></div>

        <div id="links" class="links small" style="margin-top:10px;"></div>
        <div id="outlist" class="outlist"></div>
      </div>
    </div>
  </div>

<script>
function setStatus(msg) {
  const el = document.getElementById('statusMsg');
  if (el) el.textContent = msg;
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
const BULK_FILES_KEY = "bulkFilesSelected";
let suppressRecentDuringRun = false;

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
  const lufsInput = document.getElementById('lufs');
  const tpInput = document.getElementById('tp');
  const useLufs = document.getElementById('useLufs');
  const useTp = document.getElementById('useTp');

  if (lock) {
    if (lufsInput) { lufsInput.disabled = true; setSliderValue('lufs', cfg.lufs); }
    if (tpInput) { tpInput.disabled = true; setSliderValue('tp', cfg.tp); }
    if (useLufs) { useLufs.checked = true; useLufs.disabled = true; }
    if (useTp) { useTp.checked = true; useTp.disabled = true; }
  } else {
    if (lufsInput) lufsInput.disabled = false;
    if (tpInput) tpInput.disabled = false;
    if (useLufs) useLufs.disabled = false;
    if (useTp) useTp.disabled = false;

    const manual = loadManualLoudness();
    if (manual) {
      if (manual.lufs !== undefined && manual.lufs !== null) setSliderValue('lufs', manual.lufs);
      if (manual.tp !== undefined && manual.tp !== null) setSliderValue('tp', manual.tp);
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
      const m = it.metrics || {};
      const outMetrics = m.output || m;
      const summary = (outMetrics && (outMetrics.I !== undefined || outMetrics.TP !== undefined))
        ? `LUFS ${fmtMetric(outMetrics.I, '')} / TP ${fmtMetric(outMetrics.TP, ' dB')}`
        : 'metrics: —';
      div.innerHTML = `
        <div class="runRow">
          <div class="runLeft">
            <div class="mono"><a class="linkish" href="#" onclick="loadSong('${it.song}'); return false;">${it.song || it.name}</a></div>
            <div class="small" style="opacity:.8;">${summary}</div>
            <div class="small">
              ${it.folder ? `<a class="linkish" href="${it.folder}" target="_blank">folder</a>` : ''}
              ${it.ab ? `&nbsp;|&nbsp;<a class="linkish" href="${it.ab}" target="_blank">A/B page</a>` : ''}
            </div>
          </div>
          <div class="runBtns">
            <button class="btnGhost" onclick="loadSong('${it.song}')">Load</button>
            <button class="btnDanger" onclick="deleteSong('${it.song}')">Delete</button>
          </div>
        </div>
        ${it.mp3 ? `<audio controls preload="none" src="${it.mp3}"></audio>` : `<div class="small">No previews yet</div>`}
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
      const prevPack = new Set(((localStorage.getItem(PACK_PRESETS_KEY) || "")).split(",").filter(Boolean));
      const presets = data.presets || [];
      const havePrev = presets.some(p => prevPack.has(p));
      presets.forEach((pr, idx) => {
        const wrap = document.createElement('label');
        wrap.style = "display:flex; align-items:center; gap:6px; padding:4px 8px; border:1px solid var(--line); border-radius:10px;";
        const checked = havePrev ? prevPack.has(pr) : (idx === 0);
        wrap.innerHTML = `<input type="checkbox" value="${pr}" ${checked ? 'checked' : ''}> ${pr} <button class="info-btn" type="button" data-info-type="preset" data-id="${pr}" aria-label="About ${pr}">ⓘ</button>`;
        const input = wrap.querySelector('input');
        input.addEventListener('change', () => {
          try { localStorage.setItem(PACK_PRESETS_KEY, getSelectedPresets().join(",")); } catch {}
          updatePackButtonState();
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
  await refreshRecent();
}

function setResult(text){ const el=document.getElementById('result'); if(el) el.textContent = text || '(no output)'; }
function setResultHTML(html){ const el=document.getElementById('result'); if(el) el.innerHTML = html || ''; }
function setLinks(html){ document.getElementById('links').innerHTML = html || ''; }
function clearOutList(){ document.getElementById('outlist').innerHTML = ''; }
function setMetricsPanel(html){ document.getElementById('metricsPanel').innerHTML = html || '<span style="opacity:.7;">(none)</span>'; }
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
function cleanResultText(t){
  const lines = (t || '').split('\n').map(l=>l.trim()).filter(l => l && !l.toLowerCase().startsWith('script:'));
  return lines.join('\n') || '(running…)';
}
function selectAllPackPresets(){
  const box = document.getElementById('packPresetsBox');
  if (!box) return;
  const checks = [...box.querySelectorAll('input[type=checkbox]')];
  checks.forEach(c => { c.checked = true; });
  try { localStorage.setItem(PACK_PRESETS_KEY, checks.map(c=>c.value).join(",")); } catch {}
}
function clearAllPackPresets(){
  const box = document.getElementById('packPresetsBox');
  if (!box) return;
  const checks = [...box.querySelectorAll('input[type=checkbox]')];
  checks.forEach(c => { c.checked = false; });
  try { localStorage.setItem(PACK_PRESETS_KEY, ""); } catch {}
  updatePackButtonState();
}
function selectAllBulk(){
  const box = document.getElementById('bulkFilesBox');
  if (!box) return;
  box.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = true);
  try { localStorage.setItem(BULK_FILES_KEY, getSelectedBulkFiles().join(",")); } catch {}
}
function clearAllBulk(){
  const box = document.getElementById('bulkFilesBox');
  if (!box) return;
  box.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = false);
  try { localStorage.setItem(BULK_FILES_KEY, ""); } catch {}
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
  return [...box.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value);
}
function updatePackButtonState(){
  const btn = document.getElementById('runPackBtn');
  if (!btn) return;
  btn.disabled = getSelectedPresets().length === 0;
}
function showManage(){
  document.getElementById('masterView').classList.add('hidden');
  document.getElementById('manageView').classList.remove('hidden');
  renderManage();
}
function showMaster(){
  document.getElementById('masterView').classList.remove('hidden');
  document.getElementById('manageView').classList.add('hidden');
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

function wireUploadForm(){
  const form = document.getElementById('uploadForm');
  const uploadResult = document.getElementById('uploadResult');
  if (!form) return;
  form.addEventListener('submit', async (e)=>{
    e.preventDefault();
    const fileInput = document.getElementById('file');
    if (!fileInput || !fileInput.files.length) return;
    setResultHTML('<span class="spinner">Uploading…</span>');
    uploadResult.textContent = 'Uploading...';
    const fd = new FormData(form);
    const r = await fetch('/api/upload', { method:'POST', body: fd });
    const t = await r.text();
    uploadResult.textContent = r.ok ? 'Upload complete.' : `Upload failed: ${t}`;
    setResult(r.ok ? 'Upload complete.' : 'Upload failed.');
    try { await refreshAll(); } catch(_){}
  });
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
    }
  });
}

function fmtMetric(v, suffix=""){
  if (v === null || v === undefined) return "—";
  if (typeof v === "number" && Number.isFinite(v)) return `${v}${suffix}`;
  return String(v);
}

function renderMetricsTable(m){
  if (!m || typeof m !== 'object') return '<span style="opacity:.7;">(metrics unavailable)</span>';
  const output = m.output || m; // accept flat metrics as output-only
  const input = m.input || {};
  const deltas = m.deltas || {};
  const guard = m.guardrails || {};
  const rows = [
    ["Integrated LUFS", fmtMetric(input?.I, " LUFS"), fmtMetric(output?.I, " LUFS")],
    ["Δ LUFS (out-in)", "", fmtMetric(typeof deltas?.I === 'number' ? deltas.I : null, " LUFS")],
    ["True/Peak", fmtMetric(input?.TP, " dB"), fmtMetric(output?.TP, " dB")],
    ["Δ TP (out-in)", "", fmtMetric(typeof deltas?.TP === 'number' ? deltas.TP : null, " dB")],
    ["Short-term max", fmtMetric(input?.short_term_max, " LUFS"), fmtMetric(output?.short_term_max, " LUFS")],
    ["Crest factor", fmtMetric(input?.crest_factor, " dB"), fmtMetric(output?.crest_factor, " dB")],
    ["Stereo corr", fmtMetric(input?.stereo_corr), fmtMetric(output?.stereo_corr)],
    ["Duration", fmtMetric(input?.duration_sec, " s"), fmtMetric(output?.duration_sec, " s")],
    ["Guardrails", guard.enabled ? "on" : "off", guard.enabled ? `${fmtMetric(guard.width_requested)} → ${fmtMetric(guard.width_applied)}` : "—"],
    ["Guard reason", "", guard.reason ? guard.reason : "—"],
  ];
  return `
    <table style="width:100%; border-collapse:collapse; font-size:12px;">
      <thead>
        <tr style="text-align:left;">
          <th style="padding:4px 6px; color:#cfe0f1;">Metric</th>
          <th style="padding:4px 6px; color:#cfe0f1;">Input</th>
          <th style="padding:4px 6px; color:#cfe0f1;">Output</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => `<tr>
          <td style="padding:4px 6px; border-top:1px solid var(--line);">${r[0]}</td>
          <td style="padding:4px 6px; border-top:1px solid var(--line);">${r[1]}</td>
          <td style="padding:4px 6px; border-top:1px solid var(--line);">${r[2]}</td>
        </tr>`).join("")}
      </tbody>
    </table>
  `;
}

function appendOverrides(fd){
  const addIfChecked = (chkId, inputId, key) => {
    const chk = document.getElementById(chkId);
    const input = document.getElementById(inputId);
    if (chk && input && chk.checked) fd.append(key, input.value);
  };
  addIfChecked('useLufs', 'lufs', 'lufs');
  addIfChecked('useTp', 'tp', 'tp');
  addIfChecked('ov_width', 'width', 'width');
  addIfChecked('ov_mono_bass', 'mono_bass', 'mono_bass');
  const guardrails = document.getElementById('guardrails');
  if (guardrails && guardrails.checked) fd.append('guardrails', '1');
}

let runPollTimer = null;
let runPollFiles = [];
let runPollSeen = new Set();
let runPollDone = new Set();
function stopRunPolling() {
  if (runPollTimer) {
    clearInterval(runPollTimer);
    runPollTimer = null;
  }
  runPollFiles = [];
  runPollSeen = new Set();
  runPollDone = new Set();
  suppressRecentDuringRun = false;
}

function startRunPolling(files) {
  stopRunPolling();
  const arr = Array.isArray(files) ? files : [];
  if (!arr.length) return;
  runPollFiles = [...arr];
  runPollSeen = new Set();
  setStatus(`Processing ${arr.join(', ')}`);
  runPollTimer = setInterval(async () => {
    try {
      let anyProcessing = false;
      let pending = new Set(runPollFiles);
      for (const f of runPollFiles) {
        const res = await loadSong(f, { skipEmpty: true, quiet: true });
        if (res && res.processing) {
          anyProcessing = true;
          runPollSeen.add(f);
        }
        if (res && res.hasPlayable && !res.processing) {
          if (!runPollDone.has(f)) {
            appendJobLog(`Finished ${f}`);
            runPollDone.add(f);
          }
          pending.delete(f);
          runPollSeen.add(f);
        }
      }
      if (!anyProcessing && pending.size === 0) {
        stopRunPolling();
        setStatus("");
        appendJobLog("Job complete.");
        suppressRecentDuringRun = false;
        try { await refreshRecent(true); } catch(e) { console.debug('recent refresh after polling stop failed', e); }
      }
    } catch (e) {
      console.debug("poll error", e);
    }
  }, 3000);
}

async function loadSong(song, skipEmpty=false){
  let opts = { skipEmpty: false, quiet: false };
  if (typeof skipEmpty === 'object') {
    opts.skipEmpty = !!skipEmpty.skipEmpty;
    opts.quiet = !!skipEmpty.quiet;
  } else {
    opts.skipEmpty = !!skipEmpty;
    opts.quiet = !!skipEmpty; // boolean true from polling implies quiet
  }

  if (!opts.quiet) {
    localStorage.setItem("lastSong", song);
    setLinks(`
      Output folder: <a href="/out/${song}/" target="_blank">/out/${song}/</a>
      &nbsp;|&nbsp;
      A/B page: <a href="/out/${song}/index.html" target="_blank">index.html</a>
    `);
  }

  const r = await fetch(`/api/outlist?song=${encodeURIComponent(song)}`);
  const j = await r.json();
  const hasItems = j.items && j.items.length > 0;
  if (opts.skipEmpty && !hasItems) return { hasItems:false, hasPlayable:false, processing:false };
  let processing = false;
  let markerMtime = 0;
  try {
    const pr = await fetch(`/out/${song}/.processing`, { method:'GET', cache:'no-store' });
    processing = pr.ok;
    if (pr.ok) {
      const head = await fetch(`/out/${song}/.processing`, { method:'HEAD', cache:'no-store' });
      const lm = head.headers.get("last-modified");
      if (lm) markerMtime = Date.parse(lm) || 0;
    }
  } catch(e){}

  let hasPlayable = false;
  let anyMetricsStrings = false;
  if (!opts.quiet) {
    const out = document.getElementById('outlist');
    out.innerHTML = '';
    j.items.forEach(it => {
      const audioSrc = it.mp3 || it.wav || null;
      if (audioSrc) hasPlayable = true;
      if (it.metrics) anyMetricsStrings = true;
      const div = document.createElement('div');
      div.className = 'outitem';
      div.innerHTML = `
        <div class="mono">${it.name}</div>
        ${it.metrics ? `<div class="small">${it.metrics}</div>` : `<div class="small">metrics: (not available yet)</div>`}
        ${audioSrc ? `<audio controls preload="none" src="${audioSrc}"></audio>` : ''}
        <div class="small">
          ${it.wav ? `<a class="linkish" href="${it.wav}" target="_blank">WAV</a>` : ''}
          ${it.mp3 ? `&nbsp;|&nbsp;<a class="linkish" href="${it.mp3}" target="_blank">MP3</a>` : ''}
          ${it.ab ? `&nbsp;|&nbsp;<a class="linkish" href="${it.ab}" target="_blank">A/B</a>` : ''}
        </div>
      `;
      out.appendChild(div);
    });
  } else {
    j.items.forEach(it => {
      const audioSrc = it.mp3 || it.wav || null;
      if (audioSrc) hasPlayable = true;
      if (it.metrics) anyMetricsStrings = true;
    });
  }

  if (processing && hasPlayable && markerMtime && (Date.now() - markerMtime > 15000)) {
    // marker looks stale; drop it so UI can advance
    try { await fetch(`/out/${song}/.processing`, { method:'DELETE' }); } catch(_){ }
    processing = false;
  }

  // Fetch run-level metrics (only when user explicitly loads)
  if (!opts.quiet && hasPlayable && !processing) {
    try {
      const mr = await fetch(`/api/metrics?song=${encodeURIComponent(song)}`, { cache: 'no-store' });
      if (mr.ok) {
        const mjson = await mr.json();
        setMetricsPanel(renderMetricsTable(mjson));
      } else {
        setMetricsPanel('<span style="opacity:.7;">(metrics unavailable)</span>');
      }
    } catch (e) {
      console.error(e);
      setMetricsPanel('<span style="opacity:.7;">(metrics unavailable)</span>');
    }
  }
  if (!opts.quiet && hasPlayable && processing) {
    setResult("Outputs updating...");
  }
  return { hasItems, hasPlayable, processing };
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

  const files = getSelectedBulkFiles();
  const presets = getSelectedPresets();
  if (!files.length || !presets.length) {
    alert("Select at least one input file and one preset.");
    suppressRecentDuringRun = false;
    return;
  }
  const song = (files[0] || '').replace(/\.[^.]+$/, '') || files[0];
  const strength = document.getElementById('strength').value;
  const pollFiles = files.map(f => f.replace(/\.[^.]+$/, '') || f);

  const fd = new FormData();
  fd.append('infiles', files.join(","));
  fd.append('strength', strength);
  fd.append('presets', presets.join(","));
  appendOverrides(fd);

  presets.forEach(p => files.forEach(f => appendJobLog(`Queued ${f} with preset ${p}`)));
  startRunPolling(pollFiles);
  const r = await fetch('/api/master-bulk', { method:'POST', body: fd });
  const t = await r.text();
  try {
    const j = JSON.parse(t);
    appendJobLog(j.message || 'Bulk submitted');
  } catch {
    appendJobLog(cleanResultText(t));
  }
  try { await refreshAll(); } catch (e) { console.error(e); }
}

async function runPack(){
  suppressRecentDuringRun = true;
  clearOutList(); setLinks(''); setMetricsPanel('(waiting)');
  setStatus("A/B pack running...");
  startJobLog('Processing...');
  try { localStorage.setItem("packInFlight", String(Date.now())); } catch {}

  const files = getSelectedBulkFiles();
  const presets = getSelectedPresets();
  if (!files.length || !presets.length) {
    alert("Select at least one input file and one preset.");
    suppressRecentDuringRun = false;
    return;
  }
  const strength = document.getElementById('strength').value;
  const pollFiles = files.map(f => f.replace(/\.[^.]+$/, '') || f);

  const fd = new FormData();
  fd.append('infiles', files.join(","));
  fd.append('strength', strength);
  fd.append('presets', presets.join(","));
  appendOverrides(fd);

  presets.forEach(p => files.forEach(f => appendJobLog(`Queued ${f} with preset ${p}`)));
  startRunPolling(pollFiles);
  const r = await fetch('/api/master-bulk', { method:'POST', body: fd });
  const t = await r.text();
  try {
    const j = JSON.parse(t);
    appendJobLog(j.message || 'Bulk submitted');
  } catch {
    appendJobLog(cleanResultText(t));
  }
  try { await refreshAll(); } catch (e) { console.error('post-job refreshAll failed', e); }
}

async function runBulk(){
  suppressRecentDuringRun = true;
  const files = getSelectedBulkFiles();
  const presets = getSelectedPresets();
  if (!files.length || !presets.length) {
    alert("Select at least one file and one preset.");
    suppressRecentDuringRun = false;
    return;
  }
  clearOutList(); setLinks(''); setMetricsPanel('(waiting)');
  setStatus("Bulk run starting...");
  startJobLog('Processing...');

  const song = (files[0] || '').replace(/\.[^.]+$/, '') || files[0];
  const strength = document.getElementById('strength').value;
  const pollFiles = files.map(f => f.replace(/\.[^.]+$/, '') || f);

  const fd = new FormData();
  fd.append('infiles', files.join(","));
  fd.append('strength', strength);
  fd.append('presets', presets.join(","));
  appendOverrides(fd);

  presets.forEach(p => files.forEach(f => appendJobLog(`Queued ${f} with preset ${p}`)));
  startRunPolling(pollFiles);
  const r = await fetch('/api/master-bulk', { method:'POST', body: fd });
  const t = await r.text();
  try {
    const j = JSON.parse(t);
    if (j && typeof j === 'object') {
      appendJobLog(j.message || 'Bulk submitted');
    } else {
      appendJobLog(cleanResultText(t));
    }
  } catch {
    appendJobLog(cleanResultText(t));
  }
  await refreshAll();
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
  document.getElementById('uploadResult').textContent = j.message;
  
  try { await refreshAll(); } catch (e) { console.error('post-upload refreshAll failed', e); }
});

document.addEventListener('DOMContentLoaded', () => {
  try {
    wireUI();
    initLoudnessMode();
    setMetricsPanel('(none)');
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
  } catch(e){
    console.error(e);
    setStatus("UI init error (open console)");
  }
});
</script>

<script>
window.PRESET_META = {{ preset_meta_json }};
window.LOUDNESS_PROFILES = {{ loudness_profiles_json }};
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
    html = html.replace("{{ preset_meta_json }}", json.dumps(load_preset_meta()))
    html = html.replace("{{ loudness_profiles_json }}", json.dumps(LOUDNESS_PROFILES))
    html = html.replace("{{IN_DIR}}", str(IN_DIR))
    html = html.replace("{{OUT_DIR}}", str(OUT_DIR))
    return HTMLResponse(html)

@app.get("/api/files")
def list_files():
    IN_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted([p.name for p in IN_DIR.iterdir()
                    if p.is_file() and p.suffix.lower() in [".wav",".mp3",".flac",".aiff",".aif"]])
    presets = sorted([p.stem for p in PRESET_DIR.iterdir()
                      if p.is_file() and p.suffix.lower()==".json"]) if PRESET_DIR.exists() else []
    return {"files": files, "presets": presets}


@app.get("/api/presets")
def presets():
    # Return list of preset names derived from preset files on disk
    if not PRESET_DIR.exists():
        return []
    names = sorted([p.stem for p in PRESET_DIR.glob("*.txt")])
    return names



@app.get("/api/recent")
def recent(limit: int = 30):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    folders = [d for d in OUT_DIR.iterdir() if d.is_dir()]
    folders.sort(key=lambda d: d.stat().st_mtime, reverse=True)

    items = []
    for d in folders[:limit]:
        mp3s = sorted([f.name for f in d.iterdir() if f.is_file() and f.suffix.lower()==".mp3"])
        metrics = wrap_metrics(d.name, read_run_metrics(d) or read_first_wav_metrics(d))
        items.append({
            "song": d.name,
            "folder": f"/out/{d.name}/",
            "ab": f"/out/{d.name}/index.html",
            "mp3": f"/out/{d.name}/{mp3s[0]}" if mp3s else None,
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
    folder = OUT_DIR / song
    items = []
    if folder.exists() and folder.is_dir():
        wavs = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower()==".wav"])
        mp3s = {p.stem: p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower()==".mp3"}

        for w in wavs:
            stem = w.stem
            m = read_metrics_for_wav(w)
            items.append({
                "name": stem,
                "wav": f"/out/{song}/{w.name}",
                "mp3": f"/out/{song}/{mp3s[stem]}" if stem in mp3s else None,
                "ab": f"/out/{song}/index.html",
                "metrics": fmt_metrics(m),
            })
    return {"items": items}

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

@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    IN_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for file in files:
        dest = IN_DIR / Path(file.filename).name
        dest.write_bytes(await file.read())
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
    cmd = [str(MASTER_ONE), "--preset", preset, "--infile", infile, "--strength", str(strength)]
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
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        msg = e.output or ""
        if guardrails and "unrecognized arguments: --guardrails" in msg:
            fallback_cmd = [c for c in cmd if c != "--guardrails"]
            try:
                return subprocess.check_output(fallback_cmd, text=True, stderr=subprocess.STDOUT)
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
    presets: str | None = Form(None),
):
    # Always prefer repo master_pack.py so we run the updated logic even if a host version is stale
    repo_pack = Path(__file__).resolve().parent.parent / "mastering" / "master_pack.py"
    if repo_pack.exists():
        chosen = repo_pack
        base_cmd = ["python3", str(repo_pack)]
    else:
        chosen = MASTER_PACK
        base_cmd = [str(MASTER_PACK)]
    base_cmd += ["--infile", infile, "--strength", str(strength)]
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
    def run_pack():
        try:
            subprocess.check_output(base_cmd, text=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            # Log to stderr; UI will refresh Previous Runs anyway.
            print(f"[master-pack] failed: {e.output or e}", file=sys.stderr)
        else:
            print(f"[master-pack] started infile={infile} presets={presets} strength={strength}", file=sys.stderr)

    threading.Thread(target=run_pack, daemon=True).start()
    return JSONResponse({"message": "pack started (async); outputs will appear in Previous Runs", "script": str(chosen)})

@app.post("/api/master-bulk")
def master_bulk(
    infiles: str = Form(...),
    strength: int = Form(80),
    lufs: float | None = Form(None),
    tp: float | None = Form(None),
    width: float | None = Form(None),
    mono_bass: float | None = Form(None),
    guardrails: int = Form(0),
    presets: str | None = Form(None),
):
    files = [f.strip() for f in infiles.split(",") if f.strip()]
    if not files:
        raise HTTPException(status_code=400, detail="no_files")

    repo_pack = Path(__file__).resolve().parent.parent / "mastering" / "master_pack.py"
    chosen = repo_pack if repo_pack.exists() else MASTER_PACK
    base_cmd = ["python3", str(chosen)] if repo_pack.exists() else [str(chosen)]

    results = []
    def run_all():
        for f in files:
            cmd = base_cmd + ["--infile", f, "--strength", str(strength)]
            if presets:
                cmd += ["--presets", presets]
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
                print(f"[master-bulk] start file={f} presets={presets}", file=sys.stderr)
                subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
                results.append({"file": f, "status": "ok"})
                print(f"[master-bulk] done file={f}", file=sys.stderr)
            except subprocess.CalledProcessError as e:
                results.append({"file": f, "status": "error", "error": e.output})
                print(f"[master-bulk] failed file={f}: {e.output or e}", file=sys.stderr)

    threading.Thread(target=run_all, daemon=True).start()
    return JSONResponse({"message": f"bulk started for {len(files)} file(s)", "script": str(chosen)})
