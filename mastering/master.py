#!/usr/bin/env python3
import argparse, json, shlex, subprocess, sys, re, datetime
from pathlib import Path

PRESET_DIR = Path("/mnt/external-ssd/mastering/presets")
IN_DIR = Path("/nfs/mastering/in")
OUT_DIR = Path("/nfs/mastering/out")

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def db_to_lin(db: float) -> float:
    return 10 ** (db / 20.0)

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

def docker_ffmpeg(cmdline: str):
    cmd = ["docker","exec","-i","preset-master","bash","-lc", cmdline]
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

def docker_ffprobe_json(path: Path) -> dict:
    r = docker_ffmpeg(
        f"ffprobe -v quiet -print_format json -show_format -show_streams {shlex.quote(str(path))}"
    )
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
    r = docker_ffmpeg(
        f"ffmpeg -y -hide_banner -loglevel error "
        f"-i {shlex.quote(str(input_path))} "
        f"-af {shlex.quote(af)} "
        f"-ar 48000 -ac 2 -c:a pcm_s24le "
        f"{shlex.quote(str(output_path))}"
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffmpeg failed")

def make_mp3(wav_path: Path, mp3_path: Path):
    r = docker_ffmpeg(
        f"ffmpeg -y -hide_banner -loglevel error "
        f"-i {shlex.quote(str(wav_path))} "
        f"-c:a libmp3lame -b:a 320k "
        f"{shlex.quote(str(mp3_path))}"
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "mp3 encode failed")

def measure_loudness(wav_path: Path) -> dict:
    r = docker_ffmpeg(
        f"ffmpeg -hide_banner -nostats -i {shlex.quote(str(wav_path))} "
        f"-filter_complex ebur128=peak=true -f null -"
    )
    txt = (r.stderr or "") + "\n" + (r.stdout or "")

    flags = re.IGNORECASE

    # Your build prints lots of per-block lines containing:
    # I: -14.1 LUFS   LRA: 5.4 LU   TPK: -1.2 dBFS
    mI   = re.findall(r"\bI:\s*([-\d\.]+)\s*LUFS\b", txt, flags)
    mLRA = re.findall(r"\bLRA:\s*([-\d\.]+)\s*LU\b", txt, flags)

    # Prefer TPK (true peak-ish) when present; otherwise use Peak from Summary.
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

def write_playlist_html(folder: Path, title: str):
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
<title>{title} - Masters</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 24px; max-width: 980px; }}
  h2 {{ margin: 0 0 6px 0; }}
  .small {{ color:#666; margin-bottom: 16px; }}
  .row {{ display:flex; gap:16px; align-items:center; padding: 10px 0; border-bottom: 1px solid #eee; }}
  .name {{ width: 520px; font-family: ui-monospace, Menlo, monospace; font-size: 13px; }}
  audio {{ width: 420px; }}
</style>
</head>
<body>
  <h2>{title} — Masters</h2>
  <div class="small">MP3 previews generated locally. WAV masters are in this folder too.</div>
  {''.join(rows) if rows else '<p>No MP3 previews found.</p>'}
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
    args = ap.parse_args()

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

    # Targets (from preset unless overridden)
    target_lufs = float(args.lufs) if args.lufs is not None else float(preset.get('lufs', -14))
    lim = preset.get('limiter', {}) or {}
    ceiling_db = float(args.tp) if args.tp is not None else float(lim.get('ceiling', -1.0))
    # Width: override > preset > 1.0
    width = float(args.width) if getattr(args, 'width', None) is not None else float(preset.get('width', 1.0))
    af = build_filters(preset, strength, args.lufs, args.tp, width)

    wav_out = song_dir / f"{infile.stem}__{args.preset}_S{int(strength*100)}.wav"
    run_ffmpeg_wav(infile, wav_out, af)

    mp3_out = wav_out.with_suffix(".mp3")
    make_mp3(wav_out, mp3_out)

    write_metrics(wav_out, target_lufs, ceiling_db, width)

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
            "width": args.width,
            "mono_bass": None,  # not used in this script
        },
        "input": basic_metrics(infile),
        "output": basic_metrics(wav_out),
    }
    (song_dir / "metrics.json").write_text(json.dumps(run_metrics, indent=2), encoding="utf-8")

    write_playlist_html(song_dir, infile.stem)

    print(str(wav_out))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
