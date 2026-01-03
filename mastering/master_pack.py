#!/usr/bin/env python3
import argparse, json, shlex, subprocess, sys, re, os
from pathlib import Path

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


def write_metrics(wav_out: Path, target_lufs: float, ceiling_db: float, width: float):
    m = measure_loudness(wav_out)
    if isinstance(m, dict) and 'error' not in m:
        m['target_I'] = float(target_lufs)
        m['target_TP'] = float(ceiling_db)
        m['width'] = float(width)
        if m.get('I') is not None:
            m['delta_I'] = float(m['I']) - float(target_lufs)
        if m.get('TP') is not None:
            m['tp_margin'] = float(ceiling_db) - float(m['TP'])
    wav_out.with_suffix('.metrics.json').write_text(json.dumps(m, indent=2), encoding='utf-8')

def write_playlist_html(folder: Path, title: str, source_name: str):
    mp3s = sorted([f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".mp3"])
    rows = []
    for f in mp3s:
        rows.append(f"""
        <div class="row">
          <div class="name">{f.name}</div>
          <audio controls preload="none" src="{f.name}"></audio>
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
  .row {{ display:flex; gap:16px; align-items:center; padding: 12px 0; border-bottom: 1px solid var(--line); }}
  .name {{ width: 520px; font-family: ui-monospace, Menlo, monospace; font-size: 13px; color:var(--text); }}
  audio {{ width: 420px; }}
  .pill {{ display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:12px; background:rgba(255,255,255,0.06); border:1px solid var(--line); color:var(--text); font-size:12px; }}
  .btn {{ display:inline-block; padding:8px 14px; border-radius:8px; background:var(--accent); color:#041019; font-weight:600; text-decoration:none; }}
  .header {{ display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:12px; }}
</style>
</head>
<body>
  <div class="header">
    <div>
      <h2>{title} — A/B Pack</h2>
      <div class="small">Source file: <span class="pill">{source_name}</span></div>
      <div class="small">MP3 previews generated locally. WAV masters are in the same folder.</div>
    </div>
    <div>
      <a class="btn" href="/">Return to SonusTemper</a>
    </div>
  </div>
  <div class="card">
    {''.join(rows) if rows else '<p class="small">No MP3 previews found.</p>'}
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
