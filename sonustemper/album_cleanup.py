"""Full-track repair analysis and the filters used by Album Studio repairs."""
from functools import lru_cache
from pathlib import Path
import math
import re
import subprocess
import tempfile

from sonustemper.tools import resolve_tool
from sonustemper import spectral_cleanup


FILTERS = {
    "rumble": ("lowpass=f=55", -10, 55),
    "presence": ("highpass=f=2500,lowpass=f=6000", -7, 3500),
    "highs": ("highpass=f=8000", -13, 9500),
}


def combine_noise_chains(chains: list[tuple[str, bool]]) -> tuple[str, bool] | None:
    if not chains:
        return None
    if not any(complex_graph for _, complex_graph in chains):
        return ",".join(chain for chain, _ in chains), False
    graph = []
    previous = "0:a"
    for index, (chain, complex_graph) in enumerate(chains):
        output = "out" if index == len(chains) - 1 else f"stack{index}"
        if complex_graph:
            # Scope the existing selection filter's internal labels to this pass.
            def label(match):
                name = match[1]
                new = previous if name == "0:a" else output if name == "out" else f"n{index}_{name}"
                return f"[{new}]"
            graph.append(re.sub(r"\[([\w:]+)\]", label, chain))
        else:
            graph.append(f"[{previous}]{chain}[{output}]")
        previous = output
    return ";".join(graph), True


def repair_filter(fix: dict) -> str:
    amount = fix["amount"]
    if amount == 0:
        return ""
    if fix["kind"] == "rumble":
        return f"lowshelf=f={fix['frequency']}:t=q:w=0.7:g={-amount}"
    if fix["kind"] == "highs":
        return f"highshelf=f={fix['frequency']}:t=q:w=0.7:g={-amount}"
    return f"equalizer=f={fix['frequency']}:t=q:w=1.4:g={-amount}"


def _frames(path: Path) -> list[dict]:
    frames = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("frame:"):
            frames.append({"time": float(line.split("pts_time:", 1)[1])})
        elif frames and "=" in line:
            key, value = line.split("=", 1)
            frames[-1][key.removeprefix("lavfi.astats.")] = float(value)
    return frames


def _findings(full: list[dict], bands: dict[str, list[dict]], duration: float) -> list[dict]:
    findings = []
    for kind, frames in bands.items():
        _, threshold, frequency = FILTERS[kind]
        points = []
        for base, measured in zip(full, frames):
            overall = base.get("Overall.RMS_level", -math.inf)
            power = measured.get("Overall.RMS_level", -math.inf)
            if overall > -46 and power - overall >= threshold:
                points.append((base["time"], power - overall))
        ranges: list[dict] = []
        for start, ratio in points:
            end = min(duration, start + .5)
            if ranges and start <= ranges[-1]["end"] + .51:
                ranges[-1]["end"] = end
                ranges[-1]["evidence_db"] = max(ranges[-1]["evidence_db"], ratio)
            else:
                ranges.append({"kind": kind, "start": start, "end": end, "evidence_db": ratio,
                               "frequency": frequency, "amount": 2.0})
        findings.extend(item for item in ranges if item["end"] - item["start"] >= (1.5 if kind == "rumble" else 1))
    for frame in full:
        peaks = [(key.split(".")[0], value) for key, value in frame.items()
                 if key.endswith(".Peak_level") and not key.startswith("Overall.") and value >= -.1]
        if peaks:
            start = frame["time"]
            end = min(duration, start + .5)
            if findings and findings[-1]["kind"] == "peak" and start <= findings[-1]["end"] + .01:
                findings[-1]["end"] = end
                findings[-1]["evidence_db"] = max(findings[-1]["evidence_db"], *(value for _, value in peaks))
            else:
                findings.append({"kind": "peak", "start": start, "end": end,
                                 "evidence_db": max(value for _, value in peaks)})
    for item in findings:
        item["evidence_db"] = round(item["evidence_db"], 1)
    return sorted(findings, key=lambda item: (item["start"], item["kind"]))


def analyze(path: Path, rate: int, duration: float) -> dict:
    path = path.absolute()
    stat = path.stat()
    return _analyze(path, stat.st_size, stat.st_mtime_ns, stat.st_ino, rate, duration)


@lru_cache(maxsize=64)
def _analyze(path: Path, size: int, modified: int, inode: int, rate: int, duration: float) -> dict:
    # Decode once and measure half-second windows from start to finish, including the last partial window.
    with tempfile.TemporaryDirectory(prefix="album-analysis-") as folder:
        filters = {"full": "", **{kind: chain + "," for kind, (chain, _, _) in FILTERS.items()
                                   if kind != "highs" or rate >= 20000}}
        labels = "".join(f"[{kind}]" for kind in filters)
        graph = [f"[0:a:0]asetnsamples=n={rate // 2}:p=0,asplit={len(filters)}{labels}"]
        for kind, chain in filters.items():
            per_channel = "Peak_level" if kind == "full" else "none"
            graph.append(f"[{kind}]{chain}astats=metadata=1:reset=1:measure_overall=RMS_level:"
                         f"measure_perchannel={per_channel},ametadata=mode=print:file={kind}.txt[out_{kind}]")
        # FFmpeg 7's scheduler requires an output sink with a muxer for every metrics branch.
        maps = [arg for kind in filters for arg in ("-map", f"[out_{kind}]")]
        result = subprocess.run([resolve_tool("ffmpeg"), "-nostdin", "-v", "error", "-i", str(path),
                                 "-filter_complex", ";".join(graph), *maps, "-f", "null", "-"],
                                cwd=folder, capture_output=True, text=True, timeout=900)
        if result.returncode:
            raise ValueError(result.stderr.strip()[-600:] or "Audio analysis failed")
        full = _frames(Path(folder) / "full.txt")
        if not full:
            raise ValueError("No audio could be analyzed")
        bands = {kind: _frames(Path(folder) / f"{kind}.txt") for kind in filters if kind != "full"}
        if any(len(frames) != len(full) for frames in bands.values()):
            raise ValueError("Analysis bands did not cover the same time windows")
    spectral = spectral_cleanup.detect(path)
    current = path.stat()
    if (current.st_size, current.st_mtime_ns, current.st_ino) != (size, modified, inode):
        raise ValueError("Source changed during analysis. Run it again.")
    findings = _findings(full, bands, duration) + spectral
    return {"duration": duration, "windows": len(full),
            "findings": sorted(findings, key=lambda item: (item["start"], item["kind"]))}
