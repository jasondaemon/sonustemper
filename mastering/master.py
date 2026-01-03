#!/usr/bin/env python3
import argparse, json, shlex, subprocess, sys, re, datetime, os, shutil
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
IN_DIR = Path(os.getenv("IN_DIR", str(DATA_DIR / "in")))
OUT_DIR = Path(os.getenv("OUT_DIR", str(DATA_DIR / "out")))
PRESET_DIR = Path(os.getenv("PRESET_DIR", "/presets"))

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def db_to_lin(db: float) -> float:
    return 10 ** (db / 20.0)

def build_filters(preset: dict, strength: float, lufs_override, tp_override, width: float | None) -> str:
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
    if width is not None and abs(width - 1.0) > 1e-3:
        # gently widen/narrow via side level scaling
        chain.append(f"stereotools=slev={width}")
    chain.append(loud_f)
    return ",".join(chain)

def run_cmd(cmd: list[str]):
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


def basic_metrics(path: Path) -> dict:
    info = docker_ffprobe_json(path)
    duration = None
    try:
        duration = float(info.get("format", {}).get("duration"))
    except Exception:
        duration = None

    loud = measure_loudness(path)
    m = {
        "I": loud.get("I") if isinstance(loud, dict) else None,
        "TP": loud.get("TP") if isinstance(loud, dict) else None,
        "LRA": loud.get("LRA") if isinstance(loud, dict) else None,
        "short_term_max": None,
        "crest_factor": None,
        "stereo_corr": None,
        "duration_sec": duration,
    }
    if isinstance(loud, dict) and "error" in loud:
        m["error"] = loud.get("error")
    return m


def write_metrics(wav_out: Path, target_lufs: float, ceiling_db: float, width: float):
    m = measure_loudness(wav_out)
    if isinstance(m, dict) and 'error' not in m:
        m['target_I'] = float(target_lufs)
        m['target_TP'] = float(ceiling_db)
        m['width'] = float(width)
        if m.get('I') is not None:
            m['delta_I'] = float(m['I']) - float(target_lufs)
        if m.get('TP') is not None:
            # margin: how far below ceiling we are (positive is safer)
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
    if m.get("width") is not None:
        extras.append(f"W {fmt(m.get('width'))}")
    if m.get("duration_sec") is not None:
        extras.append(f"Dur {fmt(m.get('duration_sec'),'s')}")
    return i_line + ((" • " + " • ".join(extras)) if extras else "")

def write_playlist_html(folder: Path, title: str, source_name: str):
    wavs = sorted([f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".wav"])
    source_file = folder / source_name
    source_audio = ""
    if source_file.exists():
        source_audio = f"""
        <div class="entry">
          <div class="entry-left">
            <div class="name">{source_file.name}</div>
            <div class="small metrics">Original source</div>
          </div>
          <div class="audioCol">
            <audio controls preload="none" src="{source_file.name}"></audio>
            <div class="small"><a class="linkish" href="{source_file.name}" download>Download source</a></div>
          </div>
        </div>
        """
    rows = []
    for wav in wavs:
        mp3 = wav.with_suffix(".mp3")
        metrics = read_metrics_file(wav.with_suffix('.metrics.json'))
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
<title>{title} - Masters</title>
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
      <h2>{title} — Masters</h2>
      <div class="small">Source file: <span class="pill">{source_name}</span></div>
      <div class="small">Source preview:</div>
      <div class="card" style="padding:10px; margin-top:6px; max-width: 700px;">{source_audio if source_audio else '<div class="small">Source not available.</div>'}</div>
      <div class="small" style="margin-top:8px;">MP3 previews generated locally. WAV masters are in this folder too.</div>
    </div>
    <div>
      <a class="btn" href="/">Return to SonusTemper</a>
    </div>
  </div>
  <div class="card">
    {''.join(rows) if rows else '<p class="small">No previews found.</p>'}
  </div>
</body>
</html>"""
    (folder / "index.html").write_text(html, encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", required=True)
    ap.add_argument("--infile", required=True)
    ap.add_argument("--strength", type=int, default=80)
    ap.add_argument("--lufs", type=float, default=None)
    ap.add_argument("--tp", type=float, default=None)
    ap.add_argument("--width", type=float, default=None, help="Stereo width multiplier (extrastereo). 1.0 = unchanged")
    ap.add_argument("--guardrails", action="store_true", help="Enable width guardrails")
    ap.add_argument("--guard_max_width", type=float, default=1.1, help="Maximum width when guardrails engaged")
    args = ap.parse_args()

    IN_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    strength = clamp(args.strength, 0, 100) / 100.0

    preset_path = PRESET_DIR / f"{args.preset}.json"
    if not preset_path.exists():
        print(f"Preset not found: {preset_path}", file=sys.stderr); sys.exit(2)

    infile = Path(args.infile)
    if not infile.is_absolute():
        infile = IN_DIR / infile
    if not infile.exists():
        print(f"Input not found: {infile}", file=sys.stderr); sys.exit(3)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    preset = json.loads(preset_path.read_text(encoding="utf-8"))

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
    marker = song_dir / ".processing"
    try:
        marker.write_text("running", encoding="utf-8")
    except Exception:
        marker = None

    try:
        # Targets (from preset unless overridden)
        target_lufs = float(args.lufs) if args.lufs is not None else float(preset.get('lufs', -14))
        lim = preset.get('limiter', {}) or {}
        ceiling_db = float(args.tp) if args.tp is not None else float(lim.get('ceiling', -1.0))
        # Width: override > preset > 1.0
        width_requested = float(args.width) if getattr(args, 'width', None) is not None else float(preset.get('width', 1.0))
        width_applied = width_requested
        guardrails_info = {"enabled": bool(args.guardrails), "width_requested": width_requested, "width_applied": width_applied}
        if args.guardrails:
            guard_max = float(args.guard_max_width or 1.1)
            if width_applied > guard_max:
                width_applied = guard_max
                guardrails_info["width_applied"] = width_applied
                guardrails_info["guard_max_width"] = guard_max
                guardrails_info["reason"] = "clamped_max"
            else:
                guardrails_info["guard_max_width"] = guard_max
                guardrails_info["reason"] = "none"
        else:
            guardrails_info["reason"] = "disabled"
        af = build_filters(preset, strength, args.lufs, args.tp, width_applied)

    wav_out = song_dir / f"{infile.stem}__{args.preset}_S{int(strength*100)}.wav"
    print(f"[master] start infile={infile} preset={args.preset} strength={int(strength*100)} width={width_applied}", file=sys.stderr, flush=True)
    run_ffmpeg_wav(infile, wav_out, af)

        mp3_out = wav_out.with_suffix(".mp3")
        make_mp3(wav_out, mp3_out)

        write_metrics(wav_out, target_lufs, ceiling_db, width_applied)

        # Run-level metrics (input + output)
        run_metrics = {
            "version": 1,
            "run_id": song_dir.name,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "preset": args.preset,
            "strength": int(strength * 100),
            "overrides": {
                "lufs": args.lufs,
                "tp": args.tp,
                "width": width_requested,
                "mono_bass": None,  # not used in this script
            },
            "guardrails": guardrails_info,
            "input": basic_metrics(infile),
            "output": basic_metrics(wav_out),
        }
        try:
            i = run_metrics.get("input") or {}
            o = run_metrics.get("output") or {}
            deltas = {}
            if isinstance(i.get("I"), (int, float)) and isinstance(o.get("I"), (int, float)):
                deltas["I"] = o["I"] - i["I"]
            if isinstance(i.get("TP"), (int, float)) and isinstance(o.get("TP"), (int, float)):
                deltas["TP"] = o["TP"] - i["TP"]
            if deltas:
                run_metrics["deltas"] = deltas
        except Exception:
            pass
        (song_dir / "metrics.json").write_text(json.dumps(run_metrics, indent=2), encoding="utf-8")

        write_playlist_html(song_dir, infile.stem, infile.name)

    print(str(wav_out))
    print(f"[master] done infile={infile} preset={args.preset}", file=sys.stderr, flush=True)
    finally:
        if marker:
            try:
                marker.unlink(missing_ok=True)
            except Exception:
                pass
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
