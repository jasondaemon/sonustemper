#!/usr/bin/env python3
import argparse, json, shlex, subprocess, sys, re, os, time
from pathlib import Path
import shutil
import json

def _safe_tag(s: str, max_len: int = 80) -> str:
    """Make a filesystem-safe tag chunk."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] if max_len and len(s) > max_len else s

def build_variant_tag(*, preset_name: str, strength: float | None, stages: dict | None,
                      target_I: float | None, target_TP: float | None, width: float | None,
                      mono_bass: int | None, guardrails: bool | None,
                      extra: dict | None = None) -> str:
    """
    Deterministic tag describing processing options so multiple runs won't clobber each other.
    Keeps tags reasonably short; falls back to a hash if needed.
    """
    parts: list[str] = []
    parts.append(_safe_tag(preset_name))

    if strength is not None:
        parts.append(f"S{int(round(strength))}")

    # Only include overrides when they are intended to be applied (stage enabled)
    st = stages or {}
    loud = st.get("loudness", True)
    ster = st.get("stereo", True)

    if loud and target_I is not None:
        parts.append(f"TI{target_I:g}")
    if loud and target_TP is not None:
        parts.append(f"TTP{target_TP:g}")

    if ster and width is not None:
        parts.append(f"W{width:g}")
    if ster and mono_bass is not None:
        parts.append(f"MB{mono_bass}")
    if ster and guardrails is not None:
        parts.append(f"GR{1 if guardrails else 0}")

    if extra:
        for k in sorted(extra.keys()):
            v = extra[k]
            if v is None:
                continue
            parts.append(f"{_safe_tag(str(k),20)}{_safe_tag(str(v),20)}")

    tag = "_".join(parts)
    tag = _safe_tag(tag, 120)

    # Ensure tag isn't ridiculously long; if it is, hash the full descriptor.
    if len(tag) > 120:
        h = hashlib.sha1(tag.encode("utf-8")).hexdigest()[:10]
        tag = _safe_tag("_".join(parts[:3]), 60) + f"__{h}"
    return tag
import hashlib


import re

# ---- astats parsing helpers (ffmpeg output varies by build) ----
_ASTATS_FLOAT = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
def _parse_astats_overall(text: str) -> dict:
    """Parse ffmpeg astats 'Overall' lines from stderr output.

    This project targets Debian ffmpeg builds that emit lines like:
      Peak level dB: -0.990217
      RMS level dB:  -18.296874
      Noise floor dB: -inf
      Dynamic range dB: 12.3   (may be absent)
      Crest factor: 17.3       (may be absent)

    Returns keys: peak_level, rms_level, noise_floor, dynamic_range, crest_factor.
    Values are floats or None.
    """
    peak = rms = noise = dr = cf = None
    in_overall = False

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        # Some builds prefix with "[Parsed_astats_0 ...] Overall"
        if re.search(r"\bOverall\b$", line) or re.search(r"\bOverall\b", line) and "Parsed_astats" in line:
            in_overall = True
            continue

        if not in_overall:
            continue

        m = re.search(rf"\bPeak\s+level\s+dB:\s*({_ASTATS_FLOAT})\b", line, flags=re.I)
        if m:
            peak = float(m.group(1))
            continue

        m = re.search(rf"\bRMS\s+level\s+dB:\s*({_ASTATS_FLOAT})\b", line, flags=re.I)
        if m:
            rms = float(m.group(1))
            continue

        # noise floor can be "-inf"
        m = re.search(r"\bNoise\s+floor\s+dB:\s*([^-\s]+|[-+]?\w+|"+_ASTATS_FLOAT+r")\b", line, flags=re.I)
        if m:
            val = m.group(1)
            if val.lower() == "-inf" or val.lower() == "inf":
                noise = None
            else:
                try:
                    noise = float(val)
                except ValueError:
                    noise = None
            continue

        m = re.search(rf"\bDynamic\s+range\s+dB:\s*({_ASTATS_FLOAT})\b", line, flags=re.I)
        if m:
            dr = float(m.group(1))
            continue

        # Some builds emit "Crest factor: <float>" without "dB"
        m = re.search(rf"\bCrest\s+factor\s*:\s*({_ASTATS_FLOAT})\b", line, flags=re.I)
        if m:
            cf = float(m.group(1))
            continue

        # Once we've collected the common fields, we can stop if desired
        # (keep scanning; output is small)
    # Derive crest factor if missing but peak/rms present
    if cf is None and peak is not None and rms is not None:
        cf = peak - rms

    return {
        "peak_level": peak,
        "rms_level": rms,
        "noise_floor": noise,
        "dynamic_range": dr,
        "crest_factor": cf,
    }
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
IN_DIR = Path(os.getenv("IN_DIR", str(DATA_DIR / "in")))
OUT_DIR = Path(os.getenv("OUT_DIR", str(DATA_DIR / "out")))
PRESET_DIR = Path(os.getenv("PRESET_DIR", "/presets"))

DEFAULT_PRESETS = [
    "clean","warm","rock","loud","acoustic","modern",
    "foe_metal","foe_acoustic","blues_country"
]

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def db_to_lin(db: float) -> float:
    return 10 ** (db / 20.0)

def run_cmd(cmd: list[str]):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

def build_filters(preset: dict, strength: float, lufs_override, tp_override, width: float) -> str:
    eq = preset.get("eq", [])
    comp = preset.get("compressor", {})
    lim = preset.get("limiter", {})

    target_lufs = float(lufs_override) if lufs_override is not None else float(preset.get("lufs", -14))
    ceiling_db = float(tp_override) if tp_override is not None else float(lim.get("ceiling", -1.0))
    ceiling_lin = db_to_lin(ceiling_db)

    eq_terms = []
    for band in eq:
        f = float(band["freq"])
        g = float(band["gain"]) * strength
        q = float(band.get("q", 1.0))
        eq_terms.append(f"equalizer=f={f}:width_type=q:width={q}:g={g}")

    thr_db = float(comp.get("threshold", -20))
    ratio = float(comp.get("ratio", 2.0))
    attack = float(comp.get("attack", 30))
    release = float(comp.get("release", 250))

    ratio = 1.0 + (ratio - 1.0) * (0.5 + 0.5 * strength)
    thr_db = thr_db * (0.7 + 0.3 * strength)
    thr_lin = db_to_lin(thr_db)

    comp_f = f"acompressor=threshold={thr_lin}:ratio={ratio}:attack={attack}:release={release}"
    lim_f = f"alimiter=limit={ceiling_lin}:level=disabled"
    loud_f = f"loudnorm=I={target_lufs}:TP={ceiling_db}:LRA=11"

    chain = []
    chain.extend(eq_terms)
    chain.append(comp_f)
    chain.append(lim_f)
    chain.append(loud_f)
    return ",".join(chain)

def _pcm_codec_for_depth(bit_depth: int) -> str:
    if bit_depth >= 32:
        return "pcm_s32le"
    if bit_depth >= 24:
        return "pcm_s24le"
    return "pcm_s16le"

def run_ffmpeg_wav(input_path: Path, output_path: Path, af: str, sample_rate: int, bit_depth: int):
    r = run_cmd([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-af", af,
        "-ar", str(sample_rate), "-ac", "2", "-c:a", _pcm_codec_for_depth(bit_depth),
        str(output_path)
    ])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffmpeg failed")

def make_mp3(wav_path: Path, mp3_path: Path, bitrate_kbps: int, vbr_mode: str):
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(wav_path),
        "-c:a", "libmp3lame",
    ]
    if vbr_mode and vbr_mode.lower() in ("v0", "v2"):
        q = 0 if vbr_mode.lower() == "v0" else 2
        cmd += ["-qscale:a", str(q)]
    else:
        cmd += ["-b:a", f"{int(bitrate_kbps)}k"]
    cmd.append(str(mp3_path))
    r = run_cmd(cmd)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "mp3 encode failed")

def make_aac(wav_path: Path, out_path: Path, bitrate_kbps: int, codec: str = "aac"):
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(wav_path),
        "-c:a", codec,
        "-b:a", f"{int(bitrate_kbps)}k",
    ]
    if out_path.suffix.lower() == ".m4a":
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(out_path))
    r = run_cmd(cmd)
    if r.returncode != 0 and codec != "aac":
        # Fallback to native AAC if preferred codec is missing
        cmd = [c if c != codec else "aac" for c in cmd]
        r = run_cmd(cmd)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "aac encode failed")

def make_ogg(wav_path: Path, ogg_path: Path, quality: float = 5.0):
    q = max(-1.0, min(10.0, quality))
    r = run_cmd([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(wav_path),
        "-c:a", "libvorbis", "-q:a", str(q),
        str(ogg_path)
    ])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ogg encode failed")

def make_flac(wav_path: Path, flac_path: Path, level: int = 5, sample_rate: int | None = None, bit_depth: int | None = None):
    lvl = clamp(int(level), 0, 8)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(wav_path),
        "-c:a", "flac",
        "-compression_level", str(lvl),
    ]
    if sample_rate:
        cmd += ["-ar", str(int(sample_rate))]
    if bit_depth:
        cmd += ["-sample_fmt", "s32" if int(bit_depth) >= 32 else ("s24" if int(bit_depth) >= 24 else "s16")]
    cmd.append(str(flac_path))
    r = run_cmd(cmd)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "flac encode failed")

def measure_loudness(wav_path: Path) -> dict:
    r = run_cmd([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(wav_path),
        "-filter_complex", "ebur128=peak=true", "-f", "null", "-"
    ])
    txt = (r.stderr or "") + "\n" + (r.stdout or "")

    flags = re.IGNORECASE

    mI   = re.findall(r"\bI:\s*([-\d\.]+)\s*LUFS\b", txt, flags)
    mLRA = re.findall(r"\bLRA:\s*([-\d\.]+)\s*LU\b", txt, flags)
    mTPK = re.findall(r"\bTPK:\s*([-\d\.]+)\s*dBFS\b", txt, flags) or re.findall(r"\bTPK:\s*([-\d\.]+)\b", txt, flags)
    mPeak = re.findall(r"\bPeak:\s*([-\d\.]+)\s*dBFS\b", txt, flags)

    I   = float(mI[-1]) if mI else None
    LRA = float(mLRA[-1]) if mLRA else None
    TP  = float((mTPK[-1] if mTPK else (mPeak[-1] if mPeak else None))) if (mTPK or mPeak) else None

    if I is None and LRA is None and TP is None:
        return {"error":"ebur128_parse_failed","raw_tail":txt[-2500:]}
    return {"I": I, "LRA": LRA, "TP": TP}



def measure_astats_overall(wav_path: Path) -> dict:
    """Extract a small set of useful mastering metrics via ffmpeg astats (overall section).
    This ffmpeg build uses measure_overall/measure_perchannel as flag-sets (not booleans).
    """
    # Include RMS_peak so we can compute a useful DR fallback
    want = "Peak_level+RMS_level+RMS_peak+Noise_floor+Crest_factor"
    r = run_cmd([
        "ffmpeg", "-hide_banner", "-v", "verbose", "-nostats", "-i", str(wav_path),
        "-af", f"astats=measure_overall={want}:measure_perchannel=none:reset=0",
        "-f", "null", "-"
    ])
    txt = (r.stderr or "") + "\n" + (r.stdout or "")
    out = {
        "peak_level": None,
        "rms_level": None,
        "dynamic_range": None,
        "noise_floor": None,
        "crest_factor": None,
    }
    rms_peak = None
    section = None
    for raw in txt.splitlines():
        line = raw.strip()
        # Drop ffmpeg prefix like: [Parsed_astats_0 @ ...]
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
        # Normalize keys like "peak_level_db" -> "peak_level"
        if k.endswith("_db"):
            k = k[:-3]
        v = v.strip()
        # Handle -inf/inf for noise floor
        if k == "noise_floor" and v.lower().startswith("-inf"):
            out["noise_floor"] = -120.0
            continue
        # value is usually like "-18.23 dB" or "0.0002"
        m = re.match(r"^([\-0-9\.]+)", v)
        if not m:
            continue
        try:
            num = float(m.group(1))
        except Exception:
            continue
        if k == "rms_peak":
            rms_peak = num
            continue
        if k in ("peak_level", "rms_level", "dynamic_range", "noise_floor", "crest_factor"):
            out[k] = num
    # If dynamic_range wasn't reported by this build, compute a useful fallback:
    # DR ~= (RMS_peak - RMS_level) when available
    if out["dynamic_range"] is None and rms_peak is not None and out["rms_level"] is not None:
        out["dynamic_range"] = rms_peak - out["rms_level"]

    # If crest_factor wasn't reported, compute it from peak/rms if possible
    if out["crest_factor"] is None and out["peak_level"] is not None and out["rms_level"] is not None:
        out["crest_factor"] = out["peak_level"] - out["rms_level"]
    return out

def write_metrics(wav_out: Path, target_lufs: float, ceiling_db: float, width: float, write_file: bool = True):
    m = measure_loudness(wav_out)
    if not isinstance(m, dict):
        m = {}
    # ensure keys exist even if analysis fails
    m.setdefault("crest_factor", None)
    m.setdefault("stereo_corr", None)
    m.setdefault("peak_level", None)
    m.setdefault("rms_level", None)
    m.setdefault("dynamic_range", None)
    m.setdefault("noise_floor", None)
    # add duration
    try:
        info = docker_ffprobe_json(wav_out)
        dur = float(info.get("format", {}).get("duration")) if info else None
        if dur is not None:
            if isinstance(m, dict):
                m["duration_sec"] = dur
    except Exception:
        pass
   # crest factor / correlation (+ additional astats metrics)
    try:
        a = measure_astats_overall(wav_out)
        if isinstance(a, dict):
            for k in ("peak_level","rms_level","dynamic_range","noise_floor","crest_factor"):
                if k in a and m.get(k) is None:
                    m[k] = a.get(k)
    except Exception:
        pass
    if isinstance(m, dict) and 'error' not in m:
        m['target_I'] = float(target_lufs)
        m['target_TP'] = float(ceiling_db)
        m['width'] = float(width)
        if m.get('I') is not None:
            m['delta_I'] = float(m['I']) - float(target_lufs)
        if m.get('TP') is not None:
            m['tp_margin'] = float(ceiling_db) - float(m['TP'])
    if write_file:
        wav_out.with_suffix('.metrics.json').write_text(json.dumps(m, indent=2), encoding='utf-8')

def read_metrics_file(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def compact_metrics(m: dict | None):
    if not m or not isinstance(m, dict):
        return "metrics: —"
    def fmt(v, suffix=""):
        return "—" if v is None else f"{v:.1f}{suffix}"
    def delta(v, target):
        if v is None or target is None:
            return ""
        d = v - target
        return f" ({'+' if d>0 else ''}{d:.1f})"
    i_line = f"I {fmt(m.get('I'), ' LUFS')}{delta(m.get('I'), m.get('target_I'))} • TP {fmt(m.get('TP'), ' dB')}"
    extras = []
    if m.get("LRA") is not None:
        extras.append(f"LRA {fmt(m.get('LRA'))}")
    if m.get("crest_factor") is not None:
        extras.append(f"CF {fmt(m.get('crest_factor'), ' dB')}")
    if m.get("stereo_corr") is not None:
        extras.append(f"Corr {fmt(m.get('stereo_corr'))}")
    if m.get("width") is not None:
        extras.append(f"W {fmt(m.get('width'))}")
    if m.get("duration_sec") is not None:
        extras.append(f"Dur {fmt(m.get('duration_sec'),'s')}")
    return i_line + ((" • " + " • ".join(extras)) if extras else "")

def write_playlist_html(folder: Path, title: str, source_name: str):
    audio_exts = {
        ".wav": "WAV",
        ".mp3": "MP3",
        ".m4a": "M4A",
        ".aac": "AAC",
        ".ogg": "OGG",
        ".flac": "FLAC",
    }
    audio_files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in audio_exts]
    stems = sorted(set(f.stem for f in audio_files))
    source_audio = ""
    source_copy = folder / source_name if (folder / source_name).exists() else None
    if source_copy and source_copy.is_file():
        source_audio = f"""
        <div class="entry">
          <div class="entry-left">
            <div class="name">{source_copy.name}</div>
            <div class="small metrics">Original source</div>
          </div>
          <div class="audioCol">
            <audio controls preload="none" src="{source_copy.name}"></audio>
            <div class="small"><a class="linkish" href="{source_copy.name}" download>Download source</a></div>
          </div>
        </div>
        """

    rows = []
    pref_order = [".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav"]
    for stem in stems:
        files_for_stem = {f.suffix.lower(): f for f in audio_files if f.stem == stem}
        primary = None
        for ext in pref_order:
            if ext in files_for_stem:
                primary = files_for_stem[ext]
                break
        metrics = read_metrics_file((folder / stem).with_suffix(".metrics.json"))
        rows.append(f"""
        <div class="entry">
          <div class="entry-left">
            <div class="name">{stem}</div>
            <div class="small metrics">{compact_metrics(metrics)}</div>
          </div>
          <div class="audioCol">
            {f'<audio controls preload="none" src="{primary.name}"></audio>' if primary else ''}
            <div class="small">
              {" | ".join([f'<a class="linkish" href="{files_for_stem[e].name}" download>{audio_exts[e]}</a>' for e in pref_order if e in files_for_stem])}
            </div>
          </div>
        </div>
        """)
    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title} - A/B Pack</title>
<style>
  :root {{
    --bg: #0c1118;
    --card: #0f1621;
    --text: #e7eef6;
    --muted: #a9b7c8;
    --line: #1e2633;
    --accent: #38bdf8;
  }}
  body {{
    font-family: -apple-system, system-ui, sans-serif;
    margin: 24px;
    max-width: 1100px;
    background: radial-gradient(circle at 20% 20%, rgba(56,189,248,0.06), transparent 32%), var(--bg);
    color: var(--text);
  }}
  h2 {{ margin: 0 0 6px 0; }}
  .small {{ color:var(--muted); margin-bottom: 16px; }}
  .card {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 16px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.35);
  }}
  .entry {{ display:flex; gap:16px; align-items:center; padding: 12px 0; border-bottom: 1px solid var(--line); }}
  .entry-left {{ flex:1; display:flex; flex-direction:column; gap:4px; }}
  .name {{ font-family: ui-monospace, Menlo, monospace; font-size: 13px; color:var(--text); }}
  audio {{ width: 420px; }}
  .pill {{ display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:12px; background:rgba(255,255,255,0.06); border:1px solid var(--line); color:var(--text); font-size:12px; }}
  .btn {{ display:inline-block; padding:8px 14px; border-radius:8px; background:var(--accent); color:#041019; font-weight:600; text-decoration:none; }}
  .header {{ display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:12px; }}
  .linkish {{ color: var(--accent); text-decoration: none; }}
  .linkish:hover {{ text-decoration: underline; }}
  .audioCol {{ display:flex; flex-direction:column; gap:6px; align-items:flex-start; }}
</style>
</head>
<body>
  <div class="header">
    <div>
      <h2>{title} — A/B Pack</h2>
      <div class="small">Source file: <span class="pill">{source_name}</span></div>
      <div class="small" style="margin-top:8px;">MP3 previews generated locally. WAV masters are in the same folder.</div>
    </div>
    <div>
      <a class="btn" href="/">Return to SonusTemper</a>
    </div>
  </div>
  <div class="card">
    {source_audio if source_audio else ''}
    {''.join(rows) if rows else '<p class="small">No previews found.</p>'}
  </div>
</body>
</html>"""
    (folder / "index.html").write_text(html, encoding="utf-8")

# --- status logging (for UI progress) ---
def append_status(folder: Path, stage: str, detail: str = "", preset: str | None = None):
    """Append a lightweight status entry to .status.json in the run folder."""
    try:
        status_fp = folder / ".status.json"
        payload = {"entries": []}
        if status_fp.exists():
            try:
                existing = json.loads(status_fp.read_text(encoding="utf-8"))
                if isinstance(existing, dict) and isinstance(existing.get("entries"), list):
                    payload = existing
                elif isinstance(existing, list):
                    payload["entries"] = existing
            except Exception:
                pass
        entry = {
            "ts": round(time.time(), 3),
            "stage": stage,
            "detail": detail,
            "preset": preset,
        }
        payload["entries"].append(entry)
        # Keep file small
        if len(payload["entries"]) > 300:
            payload["entries"] = payload["entries"][-300:]
        status_fp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--infile", required=True)
    ap.add_argument("--strength", type=int, default=80)
    ap.add_argument("--presets", default=",".join(DEFAULT_PRESETS))
    ap.add_argument("--lufs", type=float, default=None)
    ap.add_argument("--tp", type=float, default=None)
    ap.add_argument("--width", type=float, default=None, help="Stereo width multiplier (extrastereo). 1.0 = unchanged")
    ap.add_argument("--mono_bass", type=float, default=None, help="(unused in pack) accepted for compatibility")
    ap.add_argument("--guardrails", action="store_true", help="Enable width guardrails")
    ap.add_argument("--guard_max_width", type=float, default=1.1, help="Maximum width when guardrails engaged")
    ap.add_argument("--no_analyze", action="store_true", help="Skip metric analysis")
    ap.add_argument("--no_master", action="store_true", help="Skip applying presets (analyze-only)")
    ap.add_argument("--no_loudness", action="store_true", help="Ignore loudness/TP targets")
    ap.add_argument("--no_stereo", action="store_true", help="Ignore stereo/width controls")
    ap.add_argument("--no_output", action="store_true", help="Do not write mastered audio outputs (metrics only)")
    ap.add_argument("--out_wav", type=int, choices=[0,1], default=1, help="Enable WAV output")
    ap.add_argument("--out_mp3", type=int, choices=[0,1], default=0, help="Enable MP3 output")
    ap.add_argument("--out_aac", type=int, choices=[0,1], default=0, help="Enable AAC/M4A output")
    ap.add_argument("--out_ogg", type=int, choices=[0,1], default=0, help="Enable OGG output")
    ap.add_argument("--out_flac", type=int, choices=[0,1], default=0, help="Enable FLAC output")
    ap.add_argument("--wav_bit_depth", type=int, default=24, help="WAV bit depth (16/24/32)")
    ap.add_argument("--wav_sample_rate", type=int, default=48000, help="WAV sample rate (44100/48000/96000)")
    ap.add_argument("--mp3_bitrate", type=int, default=320, help="MP3 bitrate kbps (used when VBR is none)")
    ap.add_argument("--mp3_vbr", type=str, default="none", help="MP3 VBR mode: none|V0|V2")
    ap.add_argument("--aac_bitrate", type=int, default=256, help="AAC bitrate kbps")
    ap.add_argument("--aac_codec", type=str, default="aac", help="AAC codec (aac|libfdk_aac if available)")
    ap.add_argument("--aac_container", type=str, default="m4a", help="AAC container/extension (m4a|aac)")
    ap.add_argument("--ogg_quality", type=float, default=5.0, help="OGG quality (-1..10)")
    ap.add_argument("--flac_level", type=int, default=5, help="FLAC compression level (0-8)")
    ap.add_argument("--flac_bit_depth", type=int, default=None, help="FLAC bit depth (16/24/32, optional)")
    ap.add_argument("--flac_sample_rate", type=int, default=None, help="FLAC sample rate (optional)")
    args = ap.parse_args()

    # Stage gates
    do_analyze = not args.no_analyze
    do_master = not args.no_master
    do_loudness = not args.no_loudness
    do_stereo = not args.no_stereo
    do_output = not args.no_output
    out_wav = bool(args.out_wav)
    out_mp3 = bool(args.out_mp3)
    out_aac = bool(args.out_aac)
    out_ogg = bool(args.out_ogg)
    out_flac = bool(args.out_flac)
    wav_depth = clamp(int(args.wav_bit_depth or 24), 16, 32)
    wav_rate = int(args.wav_sample_rate or 48000)
    if wav_rate not in (44100, 48000, 88200, 96000):
        wav_rate = 48000
    flac_depth = int(args.flac_bit_depth) if args.flac_bit_depth else None
    flac_rate = int(args.flac_sample_rate) if args.flac_sample_rate else None

    if not do_loudness:
        args.lufs = None
        args.tp = None
    if not do_stereo:
        args.width = None
        args.guardrails = False
        args.mono_bass = None

    IN_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    strength = clamp(args.strength, 0, 100) / 100.0

    infile = Path(args.infile)
    if not infile.is_absolute():
        infile = IN_DIR / infile
    if not infile.exists():
        print(f"Input not found: {infile}", file=sys.stderr); sys.exit(3)

    song_dir = OUT_DIR / infile.stem
    song_dir.mkdir(parents=True, exist_ok=True)
    source_copy = song_dir / infile.name
    if not source_copy.exists():
        try:
            shutil.copy2(infile, source_copy)
        except Exception:
            pass
    presets = [p.strip() for p in args.presets.split(",") if p.strip()]
    outputs = []

    marker = song_dir / ".processing"
    try:
        marker.write_text("running", encoding="utf-8")
    except Exception:
        marker = None
    # reset status for this run
    try:
        (song_dir / ".status.json").unlink(missing_ok=True)
    except Exception:
        pass
    append_status(song_dir, "start", f"Job started for {infile.name} with presets: {', '.join(presets)}")

    # If mastering is disabled, we do an analyze-only run (no outputs).
    if not do_master:
        print('[master_pack] master stage disabled; analyze-only run (no outputs).', file=sys.stderr)
        sys.exit(0)
    # Output gating (not yet supporting master-without-output).
    if do_master and not do_output:
        print('[master_pack] output stage disabled; skipping run (no outputs generated).', file=sys.stderr)
        sys.exit(0)
    if do_output and not (out_wav or out_mp3 or out_aac or out_ogg or out_flac):
        print('[master_pack] no output formats selected; skipping run.', file=sys.stderr)
        sys.exit(0)

    try:
        for p in presets:
            preset_path = PRESET_DIR / f"{p}.json"
            if not preset_path.exists():
                print(f"Skipping missing preset: {p}", file=sys.stderr)
                continue

            with open(preset_path, "r") as f:
                preset = json.load(f)

            target_lufs = float(args.lufs) if args.lufs is not None else float(preset.get("lufs", -14))
            lim = preset.get("limiter", {}) or {}
            ceiling_db = float(args.tp) if args.tp is not None else float(lim.get("ceiling", -1.0))
            width_req = float(args.width) if args.width is not None else float(preset.get("width", 1.0))
            width_applied = width_req
            if args.guardrails:
                guard_max = float(args.guard_max_width or 1.1)
                if width_applied > guard_max:
                    width_applied = guard_max

            af = build_filters(preset, strength, args.lufs, args.tp, width_applied)
            strength_pct = int(strength * 100)
            variant_tag = build_variant_tag(
                preset_name=p,
                strength=strength_pct,
                stages={
                    "loudness": do_loudness,
                    "stereo": do_stereo,
                },
                target_I=target_lufs if do_loudness else None,
                target_TP=ceiling_db if do_loudness else None,
                width=width_applied if do_stereo else None,
                mono_bass=args.mono_bass if do_stereo else None,
                guardrails=args.guardrails if do_stereo else None,
            )
            wav_out = song_dir / f"{infile.stem}__{variant_tag}.wav"

            append_status(song_dir, "preset_start", f"Applying preset '{p}' (S={strength_pct}, width={width_applied})", preset=p)
            print(f"[pack] start file={infile.name} preset={p} strength={int(strength*100)} width={width_applied}", file=sys.stderr, flush=True)
            run_ffmpeg_wav(infile, wav_out, af, wav_rate, wav_depth)
            append_status(song_dir, "preset_done", f"Finished preset '{p}' render", preset=p)
            if out_mp3:
                make_mp3(wav_out, wav_out.with_suffix(".mp3"), args.mp3_bitrate, args.mp3_vbr)
                append_status(song_dir, "mp3_done", f"MP3 ready for '{p}'", preset=p)
            if out_aac:
                ext = ".m4a" if str(args.aac_container).lower() == "m4a" else ".aac"
                make_aac(wav_out, wav_out.with_suffix(ext), args.aac_bitrate, args.aac_codec)
                append_status(song_dir, "aac_done", f"AAC ready for '{p}'", preset=p)
            if out_ogg:
                make_ogg(wav_out, wav_out.with_suffix(".ogg"), args.ogg_quality)
                append_status(song_dir, "ogg_done", f"OGG ready for '{p}'", preset=p)
            if out_flac:
                make_flac(wav_out, wav_out.with_suffix(".flac"), args.flac_level, flac_rate or wav_rate, flac_depth or wav_depth)
                append_status(song_dir, "flac_done", f"FLAC ready for '{p}'", preset=p)
            if do_analyze:
                append_status(song_dir, "metrics_start", f"Analyzing metrics for '{p}'", preset=p)
            write_metrics(wav_out, target_lufs, ceiling_db, width_applied, write_file=do_analyze)
            if do_analyze:
                append_status(song_dir, "metrics_done", f"Metrics written for '{p}'", preset=p)
            print(f"[pack] done file={infile.name} preset={p}", file=sys.stderr, flush=True)

            if out_wav:
                outputs.append(str(wav_out))
            else:
                try:
                    wav_out.unlink(missing_ok=True)
                except Exception:
                    pass

        write_playlist_html(song_dir, infile.stem, infile.name)
        append_status(song_dir, "playlist", "Playlist generated")
        append_status(song_dir, "complete", "Job complete")
        print("\n".join(outputs))
    finally:
        if marker:
            try:
                marker.unlink(missing_ok=True)
            except Exception:
                pass

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
