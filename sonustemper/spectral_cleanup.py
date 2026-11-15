"""Time-frequency detection and non-destructive spectral cuts for Album Studio."""
from pathlib import Path
import json
import subprocess
import tempfile

import numpy as np

from sonustemper.tools import resolve_tool

SIZE = 4096
HOP = SIZE // 4
WINDOW = np.hanning(SIZE + 1)[:-1]
WEIGHT = WINDOW ** 2
OFFSETS = np.arange(SIZE)


def _run(tool, *args):
    result = subprocess.run([resolve_tool(tool), *args], capture_output=True, timeout=900)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip()[-800:] or "Spectral audio processing failed")
    return result.stdout


def _decode(source, raw, *, rate=None, channels=None):
    options = [arg for option, value in (("-ar", rate), ("-ac", channels)) if value for arg in (option, str(value))]
    _run("ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", "0:a:0",
         *options, "-f", "f32le", str(raw))


def _frames(audio, starts, *, pad=False):
    indices = starts[:, None] + OFFSETS
    data = audio[np.clip(indices, 0, len(audio) - 1)]
    if pad:
        data[(indices < 0) | (indices >= len(audio))] = 0
    return data


def _runs(flags):
    edges = np.flatnonzero(np.diff(np.r_[False, flags, False]))
    return zip(edges[::2], edges[1::2])


def detect(source: Path, match: tuple[float, float] | None = None) -> list[dict]:
    rate = 24000
    step = SIZE // 2
    with tempfile.TemporaryDirectory(prefix="album-spectrum-") as folder:
        raw = Path(folder) / "track.f32"
        # Keep stereo channels separate; a side-only whistle can vanish in a mono sum.
        _decode(source, raw, rate=rate, channels=2)
        if raw.stat().st_size < SIZE * 8:
            return []
        audio = np.memmap(raw, mode="r", dtype="<f4").reshape(-1, 2)
        try:
            return detect_regions(audio, rate, step, match)
        finally:
            del audio


def detect_regions(audio, rate, step, match=None):
    starts = np.arange(0, len(audio) - SIZE + 1, step)
    if len(starts) < 4:
        return []
    eps = 1e-20
    frequencies = np.fft.rfftfreq(SIZE, 1 / rate)
    band = (frequencies >= 250) & (frequencies <= 9500)
    tone_bins = np.flatnonzero((frequencies >= 120) & (frequencies <= min(10000, rate / 2 - 100)))
    if not len(tone_bins) or not np.any(band):
        return []
    db, flatness, tonal_bin, tonal_db, shape = [], [], [], [], []
    edges = np.searchsorted(frequencies, np.geomspace(250, 9500, 13))
    for offset in range(0, len(starts), 64):
        frames = _frames(audio, starts[offset:offset + 64])
        power = np.abs(np.fft.rfft(frames * WINDOW[None, :, None], axis=1)) ** 2
        db.extend(10 * np.log10(np.max(np.mean(frames ** 2, axis=1), axis=1) + eps))
        broadband = power[:, band].mean(axis=2) + eps
        flatness.extend(np.exp(np.mean(np.log(broadband), axis=1)) / np.mean(broadband, axis=1))
        if match:
            combined = power.mean(axis=2)
            shape.extend(np.stack([10 * np.log10(combined[:, a:b].mean(axis=1) + eps)
                                   for a, b in zip(edges[:-1], edges[1:])], axis=1))
            continue
        # A narrow line should be conspicuous against both nearby frequencies and its own floor.
        mono = np.max(power, axis=2)
        local = (mono[:, tone_bins - 12] + mono[:, tone_bins - 6] +
                 mono[:, tone_bins + 6] + mono[:, tone_bins + 12]) / 4 + eps
        core = mono[:, tone_bins]
        prominence = 10 * np.log10((core + eps) / local)
        level = 10 * np.log10((core + eps) / (WINDOW.sum() / 2) ** 2)
        peaks = (core > mono[:, tone_bins - 1]) & (core >= mono[:, tone_bins + 1])
        candidates = peaks & (level > -64) & (prominence > 19)
        top = np.argmax(np.where(candidates, core, -1), axis=1)
        rows = np.arange(len(top))
        tonal_bin.extend(np.where(candidates[rows, top], tone_bins[top], -1))
        tonal_db.extend(prominence[rows, top])
    db, flatness, tonal_bin, tonal_db = map(np.asarray, (db, flatness, tonal_bin, tonal_db))
    duration = len(audio) / rate
    peak_music = np.percentile(db, 85)
    findings = []
    floor = (db > -76) & (db < min(-44, peak_music - 20)) & (flatness > .36)
    if match:
        left, right = match
        sample = (starts >= round(left * rate)) & (starts + SIZE <= round(right * rate))
        if not np.any(sample) or right > duration + .01 or np.median(db[sample]) > -38 or np.median(flatness[sample]) < .25:
            raise ValueError("Choose at least 0.3 seconds of steady, quiet fuzz with no music")
        shape = np.asarray(shape)
        shape -= shape.mean(axis=1, keepdims=True)
        similar = np.sqrt(np.mean((shape - np.median(shape[sample], axis=0)) ** 2, axis=1)) < 4
        floor = (db > -88) & (db < -42) & (np.abs(db - np.median(db[sample])) <= 10) & (flatness > .30) & similar
        floor &= (starts + SIZE <= round(left * rate)) | (starts >= round(right * rate))
    for a, b in _runs(floor):
        if (b - a) * step / rate < .42 or np.ptp(db[a:b]) > 5:
            continue
        context = np.r_[db[max(0, a - 24):a], db[b:b + 24]]
        if not match and (not context.size or np.max(context) < np.median(db[a:b]) + 21):
            continue
        start, end = starts[a] / rate, min(duration, (starts[b - 1] + SIZE) / rate)
        findings.append(_finding("quiet_fuzz", start, end, 20, 20000, np.median(db[a:b]), 8, "profile"))

    if match:
        return findings[:12]

    # Fixed-frequency ridges are only suggestions: a sustained musical note can also look like one.
    a = 0
    while a < len(starts):
        bin_no = tonal_bin[a]
        b = a + 1
        if bin_no > 0:
            while b < len(starts) and abs(tonal_bin[b] - bin_no) <= 1:
                b += 1
            if (b - a) * step / rate >= .65 and np.median(db[a:b]) < -23:
                frequency = np.median(tonal_bin[a:b]) * rate / SIZE
                lo, hi = max(20, round(frequency - 2 * rate / SIZE)), round(frequency + 2 * rate / SIZE)
                start, end = starts[a] / rate, min(duration, (starts[b - 1] + SIZE) / rate)
                findings.append(_finding("narrow_tone", start, end, lo, hi, np.median(tonal_db[a:b]), 9, "spectral"))
        a = b
    # Keep the review queue useful even in long or intentionally noisy tracks.
    quiet = sorted((item for item in findings if item["kind"] == "quiet_fuzz"), key=lambda item: -item["evidence_db"])[:12]
    tones = sorted((item for item in findings if item["kind"] == "narrow_tone"), key=lambda item: -item["evidence_db"])[:12]
    return sorted(quiet + tones, key=lambda item: item["start"])


def _finding(kind, start, end, low, high, evidence, depth, method):
    return {"kind": kind, "start": round(start, 3), "end": round(end, 3), "evidence_db": round(float(evidence), 1),
            "f_low": low, "f_high": high, "amount": depth, "method": method}


def _profile(audio, begin, end):
    if end - begin < SIZE:
        starts = np.array([begin])
        frame = np.zeros((1, SIZE, audio.shape[1]))
        frame[0, :end - begin] = audio[begin:end]
    else:
        steps = min(96, (end - begin - SIZE) // HOP + 1)
        starts = np.linspace(begin, end - SIZE, steps, dtype=np.int64)
        frame = _frames(audio, starts)
    power = np.abs(np.fft.rfft(frame * WINDOW[None, :, None], axis=1)) ** 2
    return np.percentile(power, 65, axis=0)


def _regions(audio, rate, selections):
    bins = np.fft.rfftfreq(SIZE, 1 / rate)
    for item in selections:
        begin, end = (0, len(audio)) if item["apply_scope"] == "global" else (
            min(len(audio), round(item["t0_sec"] * rate)), min(len(audio), round(item["t1_sec"] * rate)))
        depth = item["band_depth_db"]
        if end <= begin or depth <= 0:
            continue
        low, high = item["f_low"], item["f_high"]
        width = max(rate / SIZE, min((high - low) * .12, 160))
        # Taper the band edges instead of abruptly zeroing bins.
        band = np.minimum(np.clip((bins - low + width) / width, 0, 1), np.clip((high + width - bins) / width, 0, 1))
        profile = None
        if item["method"] == "profile":
            profile = _profile(audio, begin, end)
        yield begin, end, band[:, None], 10 ** (-depth / 20), profile


def _envelope(indices, begin, end, fade):
    return np.clip(np.minimum((indices - begin) / fade, (end - indices) / fade), 0, 1)


def apply_regions(audio, rate, selections, output):
    """Write float PCM in small blocks; reconstruct only the difference from the original."""
    regions = list(_regions(audio, rate, selections))
    for start in range(0, len(audio), 32 * HOP):
        end = min(len(audio), start + 32 * HOP)
        active = [region for region in regions if region[0] < end + SIZE and start - SIZE < region[1]]
        dry = audio[start:end]
        if not any(begin < end and start < stop for begin, stop, *_ in active):
            output.write(dry.astype("<f4", copy=False).tobytes())
            continue
        positions = np.arange(start - SIZE + HOP, end, HOP)
        frames = _frames(audio, positions, pad=True)
        spectrum = np.fft.rfft(frames * WINDOW[None, :, None], axis=1)
        power = np.abs(spectrum) ** 2 if any(region[-1] is not None for region in active) else None
        all_indices = np.arange(start, end)
        gain = np.ones(spectrum.shape, dtype=np.float64)
        total = np.zeros(dry.shape)
        weights = np.zeros(end - start)
        slices = []
        for position in positions:
            left, right = max(position, start), min(position + SIZE, end)
            block = slice(left-start, right-start)
            window = slice(left-position, right-position)
            slices.append((block, window))
            weights[block] += WEIGHT[window]
        for begin, stop, band, minimum, profile in active:
            sf = (positions < stop) & (positions + SIZE > begin)
            if profile is None:
                reduction = band * (1 - minimum)
            else:
                filtered = np.sqrt(np.clip(1 - profile / np.maximum(power[sf], 1e-20), minimum**2, 1))
                reduction = band * (1 - filtered)
            if begin < end and start < stop:
                # Each box has its own sample-accurate envelope, even when two boxes touch.
                delta = -spectrum[sf] * gain[sf] * reduction
                residual = np.fft.irfft(delta, n=SIZE, axis=1) * WINDOW[None, :, None]
                fade = max(1, min(round(rate * .035), (stop - begin) // 2))
                wet = _envelope(all_indices, begin, stop, fade)
                for frame, index in zip(residual, np.flatnonzero(sf)):
                    block, window = slices[index]
                    total[block] += frame[window] * wet[block, None]
            gain[sf] *= 1 - reduction
        output.write((dry + total / weights[:, None]).astype("<f4").tobytes())


def render(source: Path, target: Path, selections: list[dict]) -> None:
    info = json.loads(_run("ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                           "stream=sample_rate,channels,channel_layout", "-of", "json", str(source)))["streams"][0]
    rate, channels = int(info["sample_rate"]), int(info["channels"])
    with tempfile.TemporaryDirectory(prefix="album-spectral-") as folder:
        dry, wet = (Path(folder) / name for name in ("dry.f32", "wet.f32"))
        _decode(source, dry)
        audio = np.memmap(dry, mode="r", dtype="<f4").reshape(-1, channels)
        try:
            with wet.open("wb") as output:
                apply_regions(audio, rate, selections, output)
        finally:
            del audio
        layout = ["-channel_layout", info["channel_layout"]] if info.get("channel_layout") else []
        _run("ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate), "-ac", str(channels),
             *layout, "-i", str(wet), "-map_metadata", "-1", "-c:a", "pcm_f32le", str(target))
