#!/usr/bin/env python3
import argparse, json, shlex, subprocess, sys, re, os
from pathlib import Path
import shutil


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

def run_ffmpeg_wav(input_path: Path, output_path: Path, af: str):
    r = run_cmd([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-af", af,
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le",
        str(output_path)
    ])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffmpeg failed")

def make_mp3(wav_path: Path, mp3_path: Path):
    r = run_cmd([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(wav_path),
        "-c:a", "libmp3lame", "-b:a", "320k",
        str(mp3_path)
    ])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "mp3 encode failed")

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

def write_metrics(wav_out: Path, target_lufs: float, ceiling_db: float, width: float):
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
    wavs = sorted([f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".wav"])
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
    for wav in wavs:
        mp3 = wav.with_suffix(".mp3")
        metrics = read_metrics_file(wav.with_suffix(".metrics.json"))
        rows.append(f"""
        <div class="entry">
          <div class="entry-left">
            <div class="name">{wav.name}</div>
            <div class="small metrics">{compact_metrics(metrics)}</div>
          </div>
          <div class="audioCol">
            <audio controls preload="none" src="{wav.name}"></audio>
            {'<div class="small"><a class="linkish" href="'+wav.name+'" download>Download WAV</a></div>' if wav.exists() else ''}
            {'<div class="small"><a class="linkish" href="'+mp3.name+'" download>Download MP3</a></div>' if mp3.exists() else ''}
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
    args = ap.parse_args()

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
    marker = song_dir / ".processing"
    try:
        marker.write_text("running", encoding="utf-8")
    except Exception:
        marker = None

    presets = [p.strip() for p in args.presets.split(",") if p.strip()]
    outputs = []

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
            wav_out = song_dir / f"{infile.stem}__{p}_S{int(strength*100)}.wav"

            print(f"[pack] start file={infile.name} preset={p} strength={int(strength*100)} width={width_applied}", file=sys.stderr, flush=True)
            run_ffmpeg_wav(infile, wav_out, af)
            make_mp3(wav_out, wav_out.with_suffix(".mp3"))
            write_metrics(wav_out, target_lufs, ceiling_db, width_applied)
            print(f"[pack] done file={infile.name} preset={p}", file=sys.stderr, flush=True)

            outputs.append(str(wav_out))

        write_playlist_html(song_dir, infile.stem, infile.name)
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