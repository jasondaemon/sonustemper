#!/usr/bin/env python3
import argparse, json, shlex, subprocess, sys, re, os, time, hashlib
from pathlib import Path
import shutil
import json
from logging_util import log_error, log_summary, log_debug

def _safe_tag(s: str, max_len: int = 80) -> str:
    """Make a filesystem-safe tag chunk."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] if max_len and len(s) > max_len else s

def _hash_descriptor(text: str, length: int = 10) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]

def build_variant_tag(descriptor: dict, *, base_stem: str = "", max_tag_len: int = 120, max_filename_len: int = 200) -> tuple[str, str]:
    """
    Deterministic tag describing processing options so multiple runs won't clobber each other.
    Returns (tag, descriptor_string_for_hash).
    """
    stages = descriptor.get("stages", {}) or {}
    parts: list[str] = []
    preset = descriptor.get("preset") or "run"
    strength = descriptor.get("strength")
    loud_mode = descriptor.get("loudness_mode")
    target_I = descriptor.get("target_I")
    target_TP = descriptor.get("target_TP")
    width = descriptor.get("width")
    mono_bass = descriptor.get("mono_bass")
    guardrails = descriptor.get("guardrails")
    outputs = descriptor.get("outputs", {}) or {}

    parts.append(_safe_tag(str(preset)))
    if strength is not None:
        parts.append(f"S{int(round(strength))}")

    if stages.get("loudness"):
        if loud_mode:
            parts.append(f"LM{_safe_tag(str(loud_mode), 24)}")
        if target_I is not None:
            parts.append(f"TI{float(target_I):g}")
        if target_TP is not None:
            parts.append(f"TTP{float(target_TP):g}")

    if stages.get("stereo"):
        if width is not None:
            parts.append(f"W{float(width):g}")
        if mono_bass is not None:
            parts.append(f"MB{int(mono_bass)}")
        if guardrails is not None:
            parts.append(f"GR{1 if guardrails else 0}")

    if stages.get("output"):
        wav = outputs.get("wav") or {}
        if wav.get("enabled"):
            sr = wav.get("sample_rate")
            bd = wav.get("bit_depth")
            chunk = "WAV"
            if sr: chunk += f"{int(sr)//1000}k"
            if bd: chunk += f"_{int(bd)}"
            parts.append(chunk)
        mp3 = outputs.get("mp3") or {}
        if mp3.get("enabled"):
            mode = mp3.get("mode")
            parts.append(f"MP3_{_safe_tag(mode,20)}" if mode else "MP3")
        aac = outputs.get("aac") or {}
        if aac.get("enabled"):
            b = aac.get("bitrate")
            codec = aac.get("codec")
            tag = f"AAC_{b}" if b else "AAC"
            if codec and codec != "aac":
                tag += f"_{_safe_tag(codec,8)}"
            parts.append(tag)
        ogg = outputs.get("ogg") or {}
        if ogg.get("enabled"):
            q = ogg.get("quality")
            parts.append(f"OGG_Q{q}" if q is not None else "OGG")
        flac = outputs.get("flac") or {}
        if flac.get("enabled"):
            lvl = flac.get("level")
            chunk = f"FLAC_L{lvl}" if lvl is not None else "FLAC"
            fr = flac.get("sample_rate")
            fb = flac.get("bit_depth")
            if fr: chunk += f"_SR{int(fr)//1000}k"
            if fb: chunk += f"_BD{int(fb)}"
            parts.append(chunk)

    # Sort any "extra" keys for stability
    extra = descriptor.get("extra") or {}
    for k in sorted(extra.keys()):
        v = extra[k]
        if v is None:
            continue
        parts.append(f"{_safe_tag(str(k),20)}{_safe_tag(str(v),20)}")

    tag = _safe_tag("_".join(parts), max_tag_len)

    descriptor_str = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))

    def hashed_fallback(prefix_parts: list[str]) -> str:
        prefix = _safe_tag("_".join(prefix_parts), max_tag_len//2)
        return f"{prefix}__{_hash_descriptor(descriptor_str)}"

    if len(tag) > max_tag_len:
        tag = hashed_fallback(parts[:3] if len(parts) >= 3 else parts)

    # Enforce overall filename length safety (stem__tag.ext <= max_filename_len)
    # Use a conservative extension length budget (16) to cover metrics/run json.
    ext_budget = 16
    if base_stem and (len(base_stem) + 2 + len(tag) + ext_budget) > max_filename_len:
        tag = hashed_fallback(parts[:3] if len(parts) >= 3 else parts[:1])
        tag = _safe_tag(tag, max_tag_len)

    return tag, descriptor_str


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
IN_DIR = Path(os.getenv("IN_DIR", os.getenv("MASTER_IN_DIR", str(DATA_DIR / "mastering" / "in"))))
OUT_DIR = Path(os.getenv("OUT_DIR", os.getenv("MASTER_OUT_DIR", str(DATA_DIR / "mastering" / "out"))))
PRESET_DIR = Path(os.getenv("PRESET_DIR", os.getenv("PRESET_USER_DIR", str(DATA_DIR / "presets" / "user"))))
ANALYSIS_TMP = Path(os.getenv("ANALYSIS_TMP_DIR", str(DATA_DIR / "analysis" / "tmp")))
GEN_PRESET_DIR = Path(os.getenv("GEN_PRESET_DIR", str(DATA_DIR / "presets" / "generated")))

DEFAULT_PRESETS = [
    "clean","warm","rock","loud","acoustic","modern",
    "foe_metal","foe_acoustic","blues_country"
]
VOICINGS = ["universal","airlift","ember","detail","glue","wide","cinematic","punch"]

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def db_to_lin(db: float) -> float:
    return 10 ** (db / 20.0)

# Event logging level: debug < summary < error
_EVENT_LEVELS = {"debug": 0, "summary": 1, "error": 2}
EVENT_LOG_LEVEL = _EVENT_LEVELS.get(os.getenv("EVENT_LOG_LEVEL", "error").lower(), 2)

def _should_log(level: str) -> bool:
    return _EVENT_LEVELS.get(level, 1) >= EVENT_LOG_LEVEL

def analyze_reference(path: Path) -> dict:
    """Extract basic spectral/loudness cues from a reference file to seed a preset."""
    info = docker_ffprobe_json(path)
    duration = None
    try:
        duration = float(info.get("format", {}).get("duration"))
    except Exception:
        duration = None
    loud = measure_loudness(path)
    cf_corr = measure_astats_overall(path)
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

def run_ffmpeg(cmd: list[str], *, stage: str = "ffmpeg", capture: bool = True):
    """Run ffmpeg (or similar) with logging and optional capture."""
    log_debug(stage, "exec", args=cmd)
    res = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if res.returncode != 0:
        stderr_tail = (res.stderr or "")[-1000:] if capture else ""
        log_error(stage, "returncode", returncode=res.returncode, stderr=stderr_tail)
    return res

def extract_json_from_stderr(stderr: str) -> dict:
    """Extract first JSON blob from stderr/stdout combined."""
    if not stderr:
        return {}
    start = stderr.find("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("json_not_found")
    blob = stderr[start:end+1]
    log_debug("ffmpeg", "json_extract", blob=blob[:600])
    return json.loads(blob)

def build_filters(preset: dict, strength: float, lufs_override, tp_override, width: float) -> str:
    eq = preset.get("eq", [])
    comp = preset.get("compressor", {})

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

    chain = []
    chain.extend(eq_terms)
    chain.append(comp_f)
    return ",".join(chain)

def _pcm_codec_for_depth(bit_depth: int) -> str:
    if bit_depth >= 32:
        return "pcm_s32le"
    if bit_depth >= 24:
        return "pcm_s24le"
    return "pcm_s16le"

def run_ffmpeg_wav(input_path: Path, output_path: Path, af: str, sample_rate: int, bit_depth: int):
    r = run_ffmpeg([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-af", af,
        "-ar", str(sample_rate), "-ac", "2", "-c:a", _pcm_codec_for_depth(bit_depth),
        str(output_path)
    ], stage="tone_render")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffmpeg failed")

def measure_loudness_stats(path: Path, target_I: float, target_TP: float) -> dict:
    """First-pass loudnorm measure; returns measured I/TP."""
    r = run_ffmpeg([
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(path),
        "-af", f"loudnorm=I={target_I}:TP={target_TP}:print_format=json:dual_mono=true",
        "-f", "null", "-"
    ], stage="loudnorm_measure")
    txt = (r.stderr or "") + "\n" + (r.stdout or "")
    stats = {}
    try:
        j = extract_json_from_stderr(txt)
        stats = {
            "input_i": float(j.get("input_i")) if j.get("input_i") is not None else None,
            "input_tp": float(j.get("input_tp")) if j.get("input_tp") is not None else None,
            "measured": j,
        }
    except Exception:
        stats = {}
    return stats

def render_with_static_loudness(source: Path, tone_filters: str, final_wav: Path, sample_rate: int, bit_depth: int,
                                target_I: float | None, target_TP: float | None, do_loudness: bool, log_label: str = "") -> dict:
    """
    Two-pass ffmpeg loudnorm (measure + apply) after the tone/EQ/comp chain.
    If loudness is disabled, we simply render the tone chain.
    """
    target_I = target_I if target_I is not None else -14.0
    target_TP = target_TP if target_TP is not None else -1.0
    target_LRA = 11.0

    # Bypass loudness entirely
    if not do_loudness:
        run_ffmpeg_wav(source, final_wav, tone_filters, sample_rate, bit_depth)
        return {"action": "bypass", "measured_I": None, "output_I": None}

    # Pass 1: measure
    try:
        stats = loudnorm_measure_json(source, tone_filters or "anull", target_I, target_TP, target_LRA)
    except Exception as exc:
        print(f"[loudness] {log_label} measure failed: {exc}", file=sys.stderr, flush=True)
        # fallback to tone render without loudnorm
        run_ffmpeg_wav(source, final_wav, tone_filters, sample_rate, bit_depth)
        return {"action": "measure_failed", "error": str(exc)}

    # Pass 2: apply loudnorm with measured values
    ln_apply = (
        f"loudnorm=I={target_I}:TP={target_TP}:LRA={target_LRA}:"
        f"measured_I={stats['input_i']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:linear=true:print_format=json:dual_mono=true"
    )
    af = tone_filters.strip() if tone_filters else ""
    if af:
        af = f"{af},{ln_apply}"
    else:
        af = ln_apply

    r = run_ffmpeg([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-af", af,
        "-ar", str(sample_rate), "-ac", "2", "-c:a", _pcm_codec_for_depth(bit_depth),
        str(final_wav)
    ], stage="loudnorm_apply")
    txt = (r.stderr or "") + "\n" + (r.stdout or "")
    out_stats = {}
    try:
        j = extract_json_from_stderr(txt)
        out_stats["output_i"] = float(j.get("output_i")) if j.get("output_i") is not None else None
        out_stats["output_tp"] = float(j.get("output_tp")) if j.get("output_tp") is not None else None
    except Exception:
        log_debug("loudnorm", "apply_parse_failed")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffmpeg loudnorm apply failed")
    merged = {
        "action": "loudnorm",
        "measured_I": stats.get("input_i"),
        "measured_TP": stats.get("input_tp"),
        "output_I": out_stats.get("output_i"),
        "output_TP": out_stats.get("output_tp"),
    }
    print(f"[loudnorm] {log_label} measured_I={merged.get('measured_I')} output_I={merged.get('output_I')} target_I={target_I} tp={target_TP}", file=sys.stderr, flush=True)
    return merged

# --- voicing chains (alternative to presets) ---
def _voicing_filters(slug: str, strength_pct: int, width: float | None, do_stereo: bool, do_loudness: bool,
                     target_I: float | None, target_TP: float | None) -> str:
    s = clamp(strength_pct, 0, 100) / 100.0
    eq_terms = []
    comp = None
    stere = None
    # helper shelves
    def shelf(freq, gain, shelf="high"):
        tval = "l" if shelf == "low" else "h"
        # equalizer with shelf mode (t=h/l), width_type=o uses octaves
        return f"equalizer=f={freq}:t={tval}:width_type=o:width=1.0:g={gain:.3f}"
    def peak(freq, gain, q=1.2):
        return f"equalizer=f={freq}:width_type=q:width={q}:g={gain:.3f}"

    if slug == "universal":
        eq_terms.append(peak(120, -0.8*s, q=1.0))
        eq_terms.append(shelf(9000, 1.2*s, "high"))
        comp = f"acompressor=threshold={db_to_lin(-22+6*s)}:ratio={1.2+0.3*s}:attack=12:release=140"
    elif slug == "airlift":
        eq_terms.append(peak(250, -1.5*s, q=1.1))
        eq_terms.append(shelf(9500, 2.5*s, "high"))
        comp = f"acompressor=threshold={db_to_lin(-24+4*s)}:ratio={1.1+0.4*s}:attack=8:release=100"
    elif slug == "ember":
        eq_terms.append(peak(180, 1.8*s, q=0.9))
        eq_terms.append(peak(350, 1.2*s, q=1.0))
        eq_terms.append(shelf(8500, -0.8*s, "high"))
        comp = f"acompressor=threshold={db_to_lin(-20)}:ratio={1.4+0.4*s}:attack=18:release=180"
    elif slug == "detail":
        eq_terms.append(peak(240, -2.2*s, q=1.0))
        eq_terms.append(peak(3200, 1.4*s, q=1.0))
        eq_terms.append(shelf(11000, 1.0*s, "high"))
        comp = f"acompressor=threshold={db_to_lin(-20)}:ratio={1.2+0.4*s}:attack=10:release=150"
    elif slug == "glue":
        eq_terms.append(peak(90, -0.8*s, q=0.8))
        eq_terms.append(peak(2000, 0.8*s, q=1.1))
        comp = f"acompressor=threshold={db_to_lin(-22)}:ratio={1.6+0.4*s}:attack=25:release={180+60*s}"
    elif slug == "wide":
        eq_terms.append(peak(220, -1.0*s, q=1.0))
        eq_terms.append(peak(8000, 1.2*s, q=1.0))
        comp = f"acompressor=threshold={db_to_lin(-18)}:ratio={1.1+0.3*s}:attack=12:release=120"
        if do_stereo and width is not None:
            # Use stereotools with side gain to widen (portable with available opts)
            w = max(0.01, min(1.5, float(width)))
            stere = f"stereotools=mode=lr>lr:slev={w:.3f}:mlev=1"
    elif slug == "cinematic":
        eq_terms.append(peak(70, 1.5*s, q=0.7))
        eq_terms.append(peak(240, -1.2*s, q=1.0))
        eq_terms.append(shelf(9500, 1.2*s, "high"))
        comp = f"acompressor=threshold={db_to_lin(-21)}:ratio={1.3+0.4*s}:attack=18:release=180"
    elif slug == "punch":
        eq_terms.append(peak(90, -1.2*s, q=1.0))
        eq_terms.append(peak(1800, 1.6*s, q=1.0))
        eq_terms.append(shelf(7500, 1.0*s, "high"))
        comp = f"acompressor=threshold={db_to_lin(-18)}:ratio={1.3+0.5*s}:attack=8:release=110"
    else:
        # fallback to minimal processing
        comp = f"acompressor=threshold={db_to_lin(-22)}:ratio=1.5:attack=15:release=180"

    chain = []
    chain.extend(eq_terms)
    if comp:
        chain.append(comp)
    if do_stereo and stere:
        chain.append(stere)
    return ",".join([f for f in chain if f])

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
        # flac supports up to 24-bit; use s32 sample_fmt for 24-bit to keep encoder happy
        bd = clamp(int(bit_depth), 16, 24)
        cmd += ["-sample_fmt", "s32" if bd >= 24 else "s16"]
    cmd.append(str(flac_path))
    r = run_cmd(cmd)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "flac encode failed")

def write_provenance(base_path: Path, payload: dict):
    """Write a sibling .run.json with effective settings for traceability."""
    try:
        dest = base_path.with_suffix(".run.json")
        dest.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass

def generate_preset_from_reference(ref_path: Path, name_slug: str) -> Path:
    """
    Analyze reference audio and produce a simple preset JSON (heuristic).
    """
    ANALYSIS_TMP.mkdir(parents=True, exist_ok=True)
    metrics = analyze_reference(ref_path)
    # Basic heuristic mapping
    target_lufs = metrics.get("I", -14.0)
    tp = metrics.get("TP", -1.0)
    eq = []
    # Adjust low-mid if rms vs peak suggests mud
    cf = metrics.get("crest_factor")
    if cf is not None and cf < 10:
        eq.append({"freq": 250, "gain": -1.5, "q": 1.0})
    # Add mild air if needed
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
            "source_file": ref_path.name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    }
    target_dir = PRESET_DIR if PRESET_DIR.exists() and os.access(PRESET_DIR, os.W_OK) else GEN_PRESET_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / f"{name_slug}.json"
    dest.write_text(json.dumps(preset, indent=2), encoding="utf-8")
    return dest

def measure_loudness(wav_path: Path) -> dict:
    r = run_ffmpeg([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(wav_path),
        "-filter_complex", "ebur128=peak=true", "-f", "null", "-"
    ], stage="ebur128")
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
    r = run_ffmpeg([
        "ffmpeg", "-hide_banner", "-v", "verbose", "-nostats", "-i", str(wav_path),
        "-af", f"astats=measure_overall={want}:measure_perchannel=none:reset=0",
        "-f", "null", "-"
    ], stage="astats")
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

def loudnorm_measure_json(input_path: Path, pre_filters: str, target_I: float, target_TP: float, target_LRA: float = 11.0) -> dict:
    """Run loudnorm in measure mode after the provided filter chain and return parsed stats."""
    ln = f"loudnorm=I={target_I}:TP={target_TP}:LRA={target_LRA}:print_format=json:dual_mono=true"
    af = pre_filters.strip()
    if af:
        af = f"{af},{ln}"
    else:
        af = ln
    r = run_cmd([
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(input_path),
        "-af", af,
        "-f", "null", "-"
    ])
    txt = (r.stderr or "") + "\n" + (r.stdout or "")
    start = txt.find("{")
    end = txt.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("loudnorm_measure_parse_failed")
    try:
        j = json.loads(txt[start:end+1])
    except Exception as exc:
        raise RuntimeError("loudnorm_measure_json_load_failed") from exc
    def _pick(key: str):
        return j.get(key) if key in j else j.get(key.upper()) if key.upper() in j else j.get(key.lower())
    stats = {
        "input_i": float(_pick("input_i")) if _pick("input_i") is not None else None,
        "input_tp": float(_pick("input_tp")) if _pick("input_tp") is not None else None,
        "input_lra": float(_pick("input_lra")) if _pick("input_lra") is not None else None,
        "input_thresh": float(_pick("input_thresh")) if _pick("input_thresh") is not None else None,
        "target_offset": float(_pick("target_offset")) if _pick("target_offset") is not None else None,
        "raw": j,
    }
    if any(v is None for v in [stats["input_i"], stats["input_tp"], stats["input_lra"], stats["input_thresh"], stats["target_offset"]]):
        raise RuntimeError("loudnorm_measure_missing_fields")
    return stats

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

def write_input_metrics(src: Path, folder: Path):
    """Analyze the source file to populate input metrics for comparison."""
    try:
        m = measure_loudness(src) or {}
        # ensure keys exist even if analysis fails
        m.setdefault("crest_factor", None)
        m.setdefault("stereo_corr", None)
        m.setdefault("peak_level", None)
        m.setdefault("rms_level", None)
        m.setdefault("dynamic_range", None)
        m.setdefault("noise_floor", None)
        # add astats-derived fields
        try:
            a = measure_astats_overall(src)
            if isinstance(a, dict):
                for k in ("peak_level","rms_level","dynamic_range","noise_floor","crest_factor"):
                    if k in a and m.get(k) is None:
                        m[k] = a.get(k)
        except Exception:
            pass
        # add duration
        try:
            info = docker_ffprobe_json(src)
            dur = float(info.get("format", {}).get("duration")) if info else None
            if dur is not None:
                m["duration_sec"] = dur
        except Exception:
            pass
        metrics_fp = folder / "metrics.json"
        payload = {
            "version": 1,
            "run_id": folder.name,
            "created_at": None,
            "preset": None,
            "strength": None,
            "overrides": {},
            "input": m,
        }
        if metrics_fp.exists():
            try:
                existing = json.loads(metrics_fp.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    existing["input"] = m
                    payload = existing
            except Exception:
                pass
        metrics_fp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass

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
def append_status(folder: Path, stage: str, detail: str = "", preset: str | None = None, level: str = "summary"):
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
        if _should_log(level):
            msg = f"[status] stage={stage} preset={preset or ''} detail={detail}"
            print(msg, file=sys.stderr, flush=True)
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
    ap.add_argument("--voicing_mode", choices=["presets","voicing"], default="presets", help="Use presets or voicing chain")
    ap.add_argument("--voicing_name", type=str, default=None, help="Voicing slug when voicing_mode=voicing")
    args = ap.parse_args()

    # Normalize voicing mode/name early so downstream logic always sees a value
    voicing_mode = args.voicing_mode or "presets"
    if voicing_mode not in ("presets", "voicing"):
        voicing_mode = "presets"
    voicing_name = args.voicing_name.strip() if args.voicing_name else None
    server_mode = os.getenv("SERVER_MODE") == "1"

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
    if flac_depth is not None:
        flac_depth = clamp(flac_depth, 16, 24)  # FLAC supports up to 24-bit
    flac_rate = int(args.flac_sample_rate) if args.flac_sample_rate else None
    loudness_mode = os.getenv("LOUDNESS_MODE", "Custom")
    mp3_mode = None
    if out_mp3:
        vbr = str(args.mp3_vbr or "none").upper()
        if vbr in ("V0", "V2"):
            mp3_mode = vbr
        else:
            try:
                mp3_mode = f"CBR{int(args.mp3_bitrate or 320)}"
            except Exception:
                mp3_mode = "CBR"
    outputs_cfg = {
        "wav": {"enabled": out_wav, "sample_rate": wav_rate if out_wav else None, "bit_depth": wav_depth if out_wav else None},
        "mp3": {"enabled": out_mp3, "mode": mp3_mode, "bitrate": args.mp3_bitrate if out_mp3 else None, "vbr": args.mp3_vbr if out_mp3 else None},
        "aac": {"enabled": out_aac, "bitrate": args.aac_bitrate if out_aac else None, "codec": args.aac_codec if out_aac else None, "container": args.aac_container if out_aac else None},
        "ogg": {"enabled": out_ogg, "quality": args.ogg_quality if out_ogg else None},
        "flac": {"enabled": out_flac, "level": args.flac_level if out_flac else None, "bit_depth": flac_depth if out_flac else None, "sample_rate": flac_rate if out_flac else None},
    }

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
    if infile.is_absolute() and server_mode:
        print(f"[guard] Absolute paths are disallowed in server mode: {infile}", file=sys.stderr)
        sys.exit(3)
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
    if voicing_mode == "voicing":
        voicing_label = voicing_name or "voicing"
        append_status(song_dir, "start", f"Job started for {infile.name} with voicing: {voicing_label}")
    else:
        append_status(song_dir, "start", f"Job started for {infile.name} with presets: {', '.join(presets) if presets else '(none)'}")
    # Source analysis (input metrics) for comparison
    if do_analyze:
        append_status(song_dir, "metrics_source_start", f"Analyzing source metrics for {infile.name}", preset="source")
        write_input_metrics(infile, song_dir)
        append_status(song_dir, "metrics_source_done", f"Source metrics written for {infile.name}", preset="source")

    # If mastering is disabled:
    # - If output is disabled too: analyze-only run.
    # - If output is enabled: allow passthrough conversions from source (no presets applied).
    if not do_master and not do_output:
        print('[master_pack] master stage disabled; analyze-only run (no outputs).', file=sys.stderr)
    if do_master and not do_output:
        print('[master_pack] output stage disabled; skipping run (no outputs generated).', file=sys.stderr)
    if do_output and not (out_wav or out_mp3 or out_aac or out_ogg or out_flac):
        print('[master_pack] no output formats selected; skipping run.', file=sys.stderr)

    job_completed = False
    try:
        stages = {
            "master": do_master,
            "loudness": do_loudness,
            "stereo": do_stereo,
            "output": do_output,
            "analyze": do_analyze,
        }
        if do_master and voicing_mode == "presets":
            for p in presets:
                preset_path = PRESET_DIR / f"{p}.json"
                if not preset_path.exists():
                    alt = GEN_PRESET_DIR / f"{p}.json"
                    if alt.exists():
                        preset_path = alt
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
                descriptor = {
                    "preset": p,
                    "strength": strength_pct,
                    "stages": stages,
                    "loudness_mode": loudness_mode if do_loudness else None,
                    "target_I": target_lufs if do_loudness else None,
                    "target_TP": ceiling_db if do_loudness else None,
                    "width": width_applied if do_stereo else None,
                    "mono_bass": args.mono_bass if do_stereo else None,
                    "guardrails": args.guardrails if do_stereo else None,
                    "outputs": outputs_cfg if do_output else {},
                }
                variant_tag, descriptor_str = build_variant_tag(descriptor, base_stem=infile.stem)
                wav_out = song_dir / f"{infile.stem}__{variant_tag}.wav"

                print(f"[pack] variant tag={variant_tag} preset={p}", file=sys.stderr, flush=True)
                append_status(song_dir, "preset_start", f"Applying preset '{p}' (S={strength_pct}, width={width_applied})", preset=p)
                print(f"[pack] start file={infile.name} preset={p} strength={int(strength*100)} width={width_applied}", file=sys.stderr, flush=True)
                render_with_static_loudness(
                    infile,
                    af,
                    wav_out,
                    wav_rate,
                    wav_depth,
                    target_lufs,
                    ceiling_db,
                    do_loudness,
                    log_label=f"preset={p}"
                )
                append_status(song_dir, "preset_done", f"Finished preset '{p}' render (WAV base)", preset=p)
                if out_mp3:
                    make_mp3(wav_out, wav_out.with_suffix(".mp3"), args.mp3_bitrate, args.mp3_vbr)
                    append_status(song_dir, "mp3_done", f"MP3 ready for '{p}'", preset=p)
                if out_aac:
                    ext = ".m4a" if str(args.aac_container).lower() == "m4a" else ".aac"
                    make_aac(wav_out, wav_out.with_suffix(ext), args.aac_bitrate, args.aac_codec)
                    append_status(song_dir, "aac_done", f"AAC ready for '{p}' ({ext[1:].upper()})", preset=p)
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
                prov = {
                    "input": infile.name,
                    "preset": p,
                    "variant_tag": variant_tag,
                    "stages": stages,
                    "resolved": {
                        "strength_pct": strength_pct,
                        "target_I": target_lufs if do_loudness else None,
                        "target_TP": ceiling_db if do_loudness else None,
                        "width": width_applied if do_stereo else None,
                        "mono_bass": args.mono_bass if do_stereo else None,
                        "guardrails": args.guardrails if do_stereo else None,
                        "loudness_mode": loudness_mode if do_loudness else None,
                        "outputs": outputs_cfg if do_output else {},
                    },
                    "descriptor": json.loads(descriptor_str) if descriptor_str else descriptor,
                }
                write_provenance(wav_out, prov)
                print(f"[pack] done file={infile.name} preset={p}", file=sys.stderr, flush=True)

                if out_wav:
                    outputs.append(str(wav_out))
                else:
                    try:
                        wav_out.unlink(missing_ok=True)
                    except Exception:
                        pass
        elif do_master and voicing_mode == "voicing":
            slug = voicing_name or "universal"
            width_req = float(args.width) if args.width is not None else 1.0
            width_applied = width_req
            if args.guardrails:
                guard_max = float(args.guard_max_width or 1.1)
                if width_applied > guard_max:
                    width_applied = guard_max
            target_lufs = float(args.lufs) if args.lufs is not None else -14.0
            ceiling_db = float(args.tp) if args.tp is not None else -1.0
            strength_pct = int(strength * 100)
            descriptor = {
                "preset": f"V_{slug}",
                "strength": strength_pct,
                "stages": stages,
                "voicing": slug,
                "loudness_mode": loudness_mode if do_loudness else None,
                "target_I": target_lufs if do_loudness else None,
                "target_TP": ceiling_db if do_loudness else None,
                "width": width_applied if do_stereo else None,
                "mono_bass": args.mono_bass if do_stereo else None,
                "guardrails": args.guardrails if do_stereo else None,
                "outputs": outputs_cfg if do_output else {},
            }
            variant_tag, descriptor_str = build_variant_tag(descriptor, base_stem=infile.stem)
            wav_out = song_dir / f"{infile.stem}__{variant_tag}.wav"
            print(f"[pack] variant tag={variant_tag} voicing={slug}", file=sys.stderr, flush=True)
            append_status(song_dir, "preset_start", f"Voicing '{slug}' (S={strength_pct}, width={width_applied})", preset=slug)
            af = _voicing_filters(slug, strength_pct, width_applied if do_stereo else None, do_stereo, do_loudness, target_lufs, ceiling_db)
            render_with_static_loudness(
                infile,
                af,
                wav_out,
                wav_rate,
                wav_depth,
                target_lufs,
                ceiling_db,
                do_loudness,
                log_label=f"voicing={slug}"
            )
            append_status(song_dir, "preset_done", f"Voicing '{slug}' render complete", preset=slug)
            if out_mp3:
                make_mp3(wav_out, wav_out.with_suffix(".mp3"), args.mp3_bitrate, args.mp3_vbr)
                append_status(song_dir, "mp3_done", f"MP3 ready for '{slug}'", preset=slug)
            if out_aac:
                ext = ".m4a" if str(args.aac_container).lower() == "m4a" else ".aac"
                make_aac(wav_out, wav_out.with_suffix(ext), args.aac_bitrate, args.aac_codec)
                append_status(song_dir, "aac_done", f"AAC ready for '{slug}' ({ext[1:].upper()})", preset=slug)
            if out_ogg:
                make_ogg(wav_out, wav_out.with_suffix(".ogg"), args.ogg_quality)
                append_status(song_dir, "ogg_done", f"OGG ready for '{slug}'", preset=slug)
            if out_flac:
                make_flac(wav_out, wav_out.with_suffix(".flac"), args.flac_level, flac_rate or wav_rate, flac_depth or wav_depth)
                append_status(song_dir, "flac_done", f"FLAC ready for '{slug}'", preset=slug)
            if do_analyze:
                append_status(song_dir, "metrics_start", f"Analyzing metrics for '{slug}'", preset=slug)
            write_metrics(wav_out, target_lufs, ceiling_db, width_applied if do_stereo else 1.0, write_file=do_analyze)
            if do_analyze:
                append_status(song_dir, "metrics_done", f"Metrics written for '{slug}'", preset=slug)
            prov = {
                "input": infile.name,
                "preset": f"voicing:{slug}",
                "variant_tag": variant_tag,
                "stages": stages,
                "resolved": {
                    "strength_pct": strength_pct,
                    "target_I": args.lufs if do_loudness else None,
                    "target_TP": args.tp if do_loudness else None,
                    "width": width_applied if do_stereo else None,
                    "mono_bass": args.mono_bass if do_stereo else None,
                    "guardrails": args.guardrails if do_stereo else None,
                    "loudness_mode": loudness_mode if do_loudness else None,
                    "voicing": slug,
                    "outputs": outputs_cfg if do_output else {},
                },
                "descriptor": json.loads(descriptor_str) if descriptor_str else descriptor,
            }
            write_provenance(wav_out, prov)
            if out_wav:
                outputs.append(str(wav_out))
            else:
                try:
                    wav_out.unlink(missing_ok=True)
                except Exception:
                    pass
        elif do_output:
            # Passthrough: no mastering, but output requested (e.g., source -> mp3)
            target_lufs = float(args.lufs) if args.lufs is not None else -14.0
            ceiling_db = float(args.tp) if args.tp is not None else -1.0
            descriptor = {
                "preset": "source",
                "strength": None,
                "stages": stages,
                "loudness_mode": None,
                "target_I": None,
                "target_TP": None,
                "width": None,
                "mono_bass": None,
                "guardrails": None,
                "outputs": outputs_cfg,
            }
            base_tag, descriptor_str = build_variant_tag(descriptor, base_stem=infile.stem)
            wav_out = song_dir / f"{infile.stem}__{base_tag}.wav"
            print(f"[pack] variant tag={base_tag} preset=source", file=sys.stderr, flush=True)
            append_status(song_dir, "preset_start", "Passthrough (no mastering)", preset="source")
            # Identity filter + optional static loudness guard/TP ceiling
            render_with_static_loudness(
                infile,
                "anull",
                wav_out,
                wav_rate,
                wav_depth,
                target_lufs,
                ceiling_db,
                do_loudness,
                log_label="passthrough"
            )
            append_status(song_dir, "preset_done", "Passthrough render complete", preset="source")
            if out_mp3:
                make_mp3(wav_out, wav_out.with_suffix(".mp3"), args.mp3_bitrate, args.mp3_vbr)
                append_status(song_dir, "mp3_done", "MP3 ready (passthrough)", preset="source")
            if out_aac:
                ext = ".m4a" if str(args.aac_container).lower() == "m4a" else ".aac"
                make_aac(wav_out, wav_out.with_suffix(ext), args.aac_bitrate, args.aac_codec)
                append_status(song_dir, "aac_done", f"AAC ready (passthrough {ext[1:].upper()})", preset="source")
            if out_ogg:
                make_ogg(wav_out, wav_out.with_suffix(".ogg"), args.ogg_quality)
                append_status(song_dir, "ogg_done", "OGG ready (passthrough)", preset="source")
            if out_flac:
                make_flac(wav_out, wav_out.with_suffix(".flac"), args.flac_level, flac_rate or wav_rate, flac_depth or wav_depth)
                append_status(song_dir, "flac_done", "FLAC ready (passthrough)", preset="source")
            if do_analyze:
                append_status(song_dir, "metrics_start", "Analyzing metrics (passthrough)", preset="source")
            write_metrics(wav_out, target_lufs, ceiling_db, 1.0, write_file=do_analyze)
            if do_analyze:
                append_status(song_dir, "metrics_done", "Metrics written (passthrough)", preset="source")
            prov = {
                "input": infile.name,
                "preset": "source",
                "variant_tag": base_tag,
                "stages": stages,
                "resolved": {
                    "strength_pct": None,
                    "target_I": None,
                    "target_TP": None,
                    "width": None,
                    "mono_bass": None,
                    "guardrails": None,
                    "loudness_mode": None,
                    "outputs": outputs_cfg,
                },
                "descriptor": json.loads(descriptor_str) if descriptor_str else descriptor,
            }
            write_provenance(wav_out, prov)
            if out_wav:
                outputs.append(str(wav_out))
            else:
                try:
                    wav_out.unlink(missing_ok=True)
                except Exception:
                    pass

        if do_output:
            write_playlist_html(song_dir, infile.stem, infile.name)
        if outputs:
            print("\n".join(outputs))
        append_status(song_dir, "playlist", "Playlist generated" if do_output else "Playlist skipped (no outputs)")
        append_status(song_dir, "complete", "Job complete")
        job_completed = True
    except Exception as exc:
        append_status(song_dir, "error", f"Job failed: {exc}", level="error")
        log_error("master", "job_failed", error=str(exc))
        job_completed = True  # allow marker cleanup so UI can refresh
        raise
    finally:
        if marker and job_completed:
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
