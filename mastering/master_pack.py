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

def run_cmd(cmd: list[str]) -> str:
    p = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    # ffmpeg (astats) emits metrics on stderr
    return (p.stderr or "") + "
" + (p.stdout or "")
def write_source_metrics(song_dir: Path, infile: Path, target_lufs: float | None, ceiling_db: float | None):
    """Compute and cache source (input) metrics once per run.
    Writes <song>/<song>.source.metrics.json
    """
    try:
        # Loudness (existing helper)
        m = measure_loudness(infile) or {}

        # Astats (overall)
        a = measure_astats_overall(infile) or {}
        for k in ("peak_level","rms_level","dynamic_range","noise_floor","crest_factor"):
            if k in a:
                m[k] = a.get(k)

        # Clamp noise floor if -inf
        if m.get("noise_floor") is None:
            m["noise_floor"] = -120.0

        if target_lufs is not None:
            m["target_I"] = float(target_lufs)
        if ceiling_db is not None:
            m["target_TP"] = float(ceiling_db)

        outp = song_dir / f"{infile.stem}.source.metrics.json"
        outp.write_text(json.dumps(m, indent=2), encoding="utf-8")
        return m
    except Exception:
        return None


