#!/usr/bin/env python3
import argparse, json, shlex, subprocess, sys, re
from pathlib import Path

PRESET_DIR = Path("/mnt/external-ssd/mastering/presets")
IN_DIR = Path("/nfs/mastering/in")
OUT_DIR = Path("/nfs/mastering/out")

DEFAULT_PRESETS = [
    "clean","warm","rock","loud","acoustic","modern",
    "foe_metal","foe_acoustic","blues_country"
]

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def db_to_lin(db: float) -> float:
    return 10 ** (db / 20.0)

def docker_ffmpeg(cmdline: str):
    cmd = ["docker","exec","-i","preset-master","bash","-lc", cmdline]
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
<title>{title} - A/B Pack</title>
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
  <h2>{title} — A/B Pack</h2>
  <div class="small">MP3 previews generated locally. WAV masters are in the same folder.</div>
  {''.join(rows) if rows else '<p>No MP3 previews found.</p>'}
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
    args = ap.parse_args()

    strength = clamp(args.strength, 0, 100) / 100.0

    infile = Path(args.infile)
    if not infile.is_absolute():
        infile = IN_DIR / infile
    if not infile.exists():
        print(f"Input not found: {infile}", file=sys.stderr); sys.exit(3)

    song_dir = OUT_DIR / infile.stem
    song_dir.mkdir(parents=True, exist_ok=True)

    presets = [p.strip() for p in args.presets.split(",") if p.strip()]
    outputs = []

    for p in presets:
        preset_path = PRESET_DIR / f"{p}.json"
        if not preset_path.exists():
            print(f"Skipping missing preset: {p}", file=sys.stderr)
            continue

        with open(preset_path, "r") as f:
            preset = json.load(f)

        af = build_filters(preset, strength, args.lufs, args.tp, width)
        wav_out = song_dir / f"{infile.stem}__{p}_S{int(strength*100)}.wav"

        run_ffmpeg_wav(infile, wav_out, af)
        make_mp3(wav_out, wav_out.with_suffix(".mp3"))
        write_metrics(wav_out, target_lufs, ceiling_db, width)

        outputs.append(str(wav_out))

    write_playlist_html(song_dir, infile.stem)
    print("\n".join(outputs))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
