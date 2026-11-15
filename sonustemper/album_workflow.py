"""Album delivery from configured, filesystem-backed production locations."""
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from functools import lru_cache
from itertools import groupby
from pathlib import Path
from threading import Lock
from time import time

from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TPE2, TRCK, TYER, USLT

from sonustemper.master_pack import measure_loudness
from sonustemper.tools import resolve_tool


def child(parent: Path, name: str) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Invalid path component")
    path = parent / name
    if path.is_symlink():
        raise ValueError("Symbolic links are unavailable in production albums")
    return path


def audio_files(album: Path, stage: str, suffix: str) -> list[Path]:
    folder = child(album, stage)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                  and not p.name.startswith((".", "#", "@")) and p.suffix.lower() == suffix)


def wavs(album: Path, stage: str) -> list[Path]:
    return audio_files(album, stage, ".wav")


DELIVERY_SUFFIX = re.compile(
    r" \(Integrated -?\d+(?:\.\d+)? LUFS, True Peak -?\d+(?:\.\d+)? dB, Channel [^)]+\)$"
)
ARTWORK_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".bmp", ".tiff", ".tif"}
ARTWORK_SIZE = 3000


def album_stages(album: Path) -> dict[str, list[str]]:
    return {stage: [path.name for path in audio_files(album, stage, suffix)]
            for stage, suffix in (("raw", ".wav"), ("mastered", ".wav"), ("clean", ".wav"),
                                  ("normalized", ".wav"), ("mp3s", ".mp3"))}


def track_delivery(stages: dict[str, list[str]]) -> dict[str, list[str]]:
    clean = {Path(name).stem for name in stages["clean"]}
    mp3s = {Path(name).stem for name in stages["mp3s"]}
    masters: dict[str, set[str]] = {}
    for name in stages["normalized"]:
        stem = Path(name).stem
        title = DELIVERY_SUFFIX.sub("", stem)
        masters.setdefault(title, set()).add(stem)

    missing = {}
    for name in dict.fromkeys(stages["raw"] + stages["mastered"]):
        stem = Path(name).stem
        outputs = masters.get(stem, set())
        missing[name] = (["Clean WAV"] if stem not in clean else [])
        if not outputs:
            missing[name].append("Final WAV")
        if not outputs.intersection(mp3s) and (outputs or stem not in mp3s):
            missing[name].append("Matching MP3" if outputs else "MP3")
    return missing


def delivery_complete(missing: list[str]) -> bool:
    return all(stage == "Clean WAV" for stage in missing)


def delivery_paths(album: Path, tracks: list[Path]) -> set[str]:
    stems = {track.stem for track in tracks}
    return {f"{stage}/{path.name}" for stage, suffix in (("normalized", ".wav"), ("mp3s", ".mp3"), ("mp3s", ".lrc"))
            for path in audio_files(album, stage, suffix) if path.stem in stems or DELIVERY_SUFFIX.sub("", path.stem) in stems}


def require_full_album(album: Path, stage: str, tracks: list[Path]) -> None:
    if tracks != wavs(album, stage):
        raise ValueError("The album track list changed or is incomplete. Refresh and review the full album.")
    if stage == "mastered":
        raw = {path.stem for path in wavs(album, "raw")}
        if raw and raw != {path.stem for path in tracks}:
            raise ValueError("Existing masters do not match the full raw album. Finalize from Raw WAVs.")


def lyrics_draft(text: str) -> str:
    match = re.search(r"^\s*(#{1,6})[ \t]+Lyrics\b[^\n]*\n", text, re.I | re.M)
    if match:
        text = text[match.end():]
        text = re.split(rf"(?m)^ {{0,3}}#{{{len(match[1])}}}[ \t]+", text, maxsplit=1)[0]
    lines = []
    numbers = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
    for line in text.splitlines():
        line = line.strip()
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        if (line.startswith(("#", "```", "~~~", "http://", "https://"))
                or re.fullmatch(r"[- _]{3,}|svg", line, re.I)
                or re.match(r"^(?:artist|album|title|source|status|style|negative prompt|suno song):", line, re.I)):
            continue
        if re.fullmatch(r"\[(?:intro|verse|(?:pre|post|final)[- ]?chorus|chorus|bridge|hook|outro|breakdown|refrain)\b[^\]]*\]", line, re.I):
            lines.append("")
            continue
        if re.fullmatch(r"\((?:repeat|instrumental|silence|pause)\b[^)]*\)|(?:repeat\s+(?:chorus|verse|hook|[x×]?\s*\d+).*|chorus\s*[x×]\s*\d+)", line, re.I):
            continue
        # Suno's bracketed production cues can appear anywhere in a lyric line.
        stripped = re.sub(r"\[[^\]]*\]", "", line).strip()
        if line and not stripped:
            continue
        line = re.sub(r"(?<![\w.])(?:10|[1-9])(?!\w|\.\d)", lambda m: numbers[int(m[0]) - 1], stripped)
        line = re.sub(r"\s+", " ", line).rstrip(".,:;…").rstrip()
        line = re.sub(r"(\(\s*)([^\W\d_])", lambda m: m[1] + m[2].upper(), line)
        line = re.sub(r'^([^\w]*)([^\W\d_])', lambda m: m[1] + m[2].upper(), line, count=1)
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def ffmpeg(source: Path, target: Path, *options: str) -> None:
    process = subprocess.run(
        [resolve_tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-y", "-i", str(source), "-map", "0:a:0", "-map_metadata", "-1",
         "-map_chapters", "-1", *options, str(target)],
        capture_output=True, text=True, timeout=900,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.strip()[-800:] or "FFmpeg failed")


def metrics(path: Path) -> tuple[float, float]:
    result = measure_loudness(path)
    loudness, peak = result.get("I"), result.get("TP")
    if loudness is None or peak is None or not all(map(math.isfinite, (loudness, peak))):
        raise ValueError(f"Could not measure loudness for {path.name}")
    return loudness, peak


def _stamp(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


@lru_cache(maxsize=512)
def file_loudness(path: Path, stamp: tuple[int, int, int]) -> dict:
    measured = measure_loudness(path)
    if _stamp(path) != stamp:
        raise ValueError("Audio changed during measurement")
    if measured.get("I") is None:
        raise ValueError("Loudness could not be measured")
    return {"lufs": measured["I"] if measured.get("TP") is not None else None, "lra": measured.get("LRA")}


def track_loudness(album: Path, track: Path) -> dict:
    matches = [path for path in wavs(album, "normalized") if DELIVERY_SUFFIX.sub("", path.stem) == track.stem]
    source = file_loudness(track, _stamp(track))
    if len(matches) > 1:
        return {"source": source, "final": None, "final_status": "Multiple final WAVs"}
    if not matches:
        return {"source": source, "final": None, "final_status": "No final WAV"}
    final = matches[0]
    try:
        return {"source": source, "final": file_loudness(final, _stamp(final))}
    except (OSError, RuntimeError, ValueError):
        return {"source": source, "final": None, "final_status": "Final WAV unavailable"}


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        value = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
        return value.hexdigest()


def text_asset(album: Path, stage: str, stem: str, suffix: str) -> str:
    path = child(child(album, stage), stem + suffix)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


TRACK_TEXT_FILES = {
    "style": ("styles", ".md"),
    "prompt": ("lyric-prompts", ".md"),
    "lyrics": ("lyrics", ".txt"),
}
NEGATIVE_HEADING = re.compile(r"^##[ \t]+Negative Prompt[ \t]*$", re.I | re.M)
STYLE_WRITE_LOCK = Lock()
LYRICS_WRITE_LOCK = Lock()


def split_style_prompts(text: str) -> tuple[str, str]:
    heading = NEGATIVE_HEADING.search(text)
    if heading is None:
        return text, ""
    style = text[:heading.start()]
    style = style[:-2] if style.endswith("\n\n") else style.removesuffix("\n")
    return style, text[heading.end():].removeprefix("\n")


def read_track_text(album: Path, stem: str) -> dict[str, str]:
    fields = {kind: text_asset(album, stage, stem, suffix)
              for kind, (stage, suffix) in TRACK_TEXT_FILES.items()}
    fields["style"], fields["negative"] = split_style_prompts(fields["style"])
    return fields


def track_text_status(album: Path, tracks: list[str]) -> dict:
    stems = {Path(name).stem for name in tracks}
    by_kind: dict[str, set[str]] = {kind: set() for kind in (*TRACK_TEXT_FILES, "negative")}
    for kind, (stage, suffix) in TRACK_TEXT_FILES.items():
        try:
            folder = child(album, stage)
            files = list(folder.iterdir()) if folder.is_dir() else []
        except (OSError, ValueError):
            continue
        for path in files:
            if path.suffix == suffix and path.stem in stems:
                try:
                    value = text_asset(album, stage, path.stem, suffix)
                    if kind == "style":
                        value, negative = split_style_prompts(value)
                        if negative.strip():
                            by_kind["negative"].add(path.stem)
                    if value.strip():
                        by_kind[kind].add(path.stem)
                except (OSError, ValueError):
                    continue
    return {name: {kind: Path(name).stem in saved for kind, saved in by_kind.items()} for name in tracks}


def write_track_text(album: Path, stem: str, kind: str, text: str) -> str:
    if kind in {"style", "negative"}:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        stage, suffix = TRACK_TEXT_FILES["style"]
        if kind == "style" and NEGATIVE_HEADING.search(text):
            raise ValueError("Edit the Negative Prompt using its own button")
        # The two editors update sections of the same file, not separate files.
        with STYLE_WRITE_LOCK:
            style, negative = split_style_prompts(text_asset(album, stage, stem, suffix))
            if kind == "style":
                style = text
            else:
                negative = text
            combined = style
            if negative.strip():
                combined += ("\n\n" if style else "") + "## Negative Prompt\n" + negative
            save_text_asset(album, stage, stem, suffix, combined)
    else:
        stage, suffix = TRACK_TEXT_FILES[kind]
        if kind == "lyrics":
            text = text.rstrip()
            with LYRICS_WRITE_LOCK:
                old = text_asset(album, stage, stem, suffix).strip()
                companion = child(child(album, stage), stem + ".lrc")
                generated = (bool(old) and companion.is_file() and
                             companion.read_text(encoding="utf-8", errors="replace") == old + "\n")
                save_text_asset(album, stage, stem, suffix, text + "\n" if text else "")
                if text and (generated or not companion.exists()):
                    save_text_asset(album, stage, stem, ".lrc", text.strip() + "\n")
                elif not text and generated:
                    companion.unlink(missing_ok=True)
        else:
            save_text_asset(album, stage, stem, suffix, text)
    return text


def save_text_asset(album: Path, stage: str, stem: str, suffix: str, text: str) -> None:
    folder = child(album, stage)
    folder.mkdir(exist_ok=True)
    target = child(folder, stem + suffix)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, delete=False, mode="w", encoding="utf-8") as stream:
            temporary = Path(stream.name)
            if target.is_file():
                os.chmod(temporary, target.stat().st_mode & 0o777)
            stream.write(text)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record(database: Path, job: str, *, expected_status: str | None = None, **changes) -> dict:
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS album_jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
        if changes:
            conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state FROM album_jobs WHERE id = ?", (job,)).fetchone()
        state = json.loads(row[0]) if row else {}
        if expected_status is not None and state.get("status") != expected_status:
            return {}
        if changes:
            state.update(changes)
            conn.execute("INSERT OR REPLACE INTO album_jobs VALUES (?, ?)", (job, json.dumps(state)))
        return state


def job_states(database: Path, statuses: tuple[str, ...]) -> list[tuple[str, dict]]:
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS album_jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
        placeholders = ",".join("?" for _ in statuses)
        return [(job, json.loads(state)) for job, state in conn.execute(
            f"SELECT id, state FROM album_jobs WHERE json_extract(state, '$.status') IN ({placeholders}) ORDER BY rowid DESC",
            statuses,
        )]


def published_tracks(database: Path, album_key: str) -> dict[str, dict]:
    tracks = {}
    for _, state in job_states(database, ("complete",)):
        if state.get("album_key") == album_key:
            for result in state.get("results", []):
                tracks.setdefault(result["track"], result)
    return tracks


def owned_outputs(album: Path, tracks: list[Path], previous: dict[str, dict]) -> set[str]:
    owned = set()
    for track in tracks:
        result = previous.get(track.name, {})
        for relative, fingerprint in result.get("output_sha256", {}).items():
            path = publication_path(album, relative)
            if path.is_file() and digest(path) == fingerprint:
                owned.add(relative)
    return owned


def publication_path(album: Path, relative: str) -> Path:
    stage, name = relative.split("/", 1)
    if stage not in {"mastered", "clean", "normalized", "mp3s"} and not (stage == "lyrics" and name.endswith(".lrc")):
        raise ValueError("Invalid output stage")
    return child(child(album, stage), name)


def rollback_publication(album: Path, work: Path, entries: list[dict]) -> None:
    for index, entry in reversed(list(enumerate(entries))):
        target = publication_path(album, entry["path"])
        backup = work / "backup" / str(index)
        if backup.exists():
            os.replace(backup, target)
        elif entry["write"] and not entry["existed"] and not (work / entry["path"]).exists():
            target.unlink(missing_ok=True)


def publish(album: Path, work: Path, paths: set[str], previous: set[str], replace: bool, report, results: list[dict]) -> None:
    entries = []
    for relative in sorted(paths | previous):
        target = publication_path(album, relative)
        exists = target.exists()
        if exists and not target.is_file():
            raise ValueError(f"Output is not a regular file: {relative}")
        if exists and not replace:
            raise FileExistsError(f"Existing output requires replacement approval: {relative}")
        entries.append({"path": relative, "existed": exists, "write": relative in paths})
    (work / "backup").mkdir()
    journal = {"entries": entries}
    report(stage="Publishing", track="", track_number=None, publication=journal)
    try:
        for index, entry in enumerate(entries):
            target = publication_path(album, entry["path"])
            if entry["existed"]:
                os.replace(target, work / "backup" / str(index))
            if entry["write"]:
                os.replace(work / entry["path"], target)
        report(status="complete", stage="Complete", results=results, publication=None)
    except Exception:
        rollback_publication(album, work, entries)
        report(publication=None)
        raise


def process_track(source: Path, target: Path, filters: str, noise) -> None:
    if not noise:
        ffmpeg(source, target, "-af", filters or "anull", "-c:a", "pcm_f32le")
        return
    intermediate = target.with_name(target.stem + ".before-noise.wav")
    ffmpeg(source, intermediate, "-af", filters or "anull", "-c:a", "pcm_f32le")
    chain, complex_graph = noise
    try:
        if complex_graph:
            subprocess.run([
                resolve_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(intermediate),
                "-filter_complex", chain, "-map", "[out]", "-map_metadata", "-1",
                "-c:a", "pcm_f32le", str(target),
            ], check=True, capture_output=True, timeout=900)
        else:
            ffmpeg(intermediate, target, "-af", chain, "-c:a", "pcm_f32le")
    finally:
        intermediate.unlink(missing_ok=True)


def repair_batches(fixes: list[dict], skip_fix: str = ""):
    enabled = (fix for fix in fixes if fix["id"] != skip_fix)
    for (_, whole_song), group in groupby(enabled, key=lambda fix: (fix["filter"], fix["end"] is None)):
        batch = list(group)
        if whole_song:
            yield from ([fix] for fix in batch)
            continue
        ordered = sorted(batch, key=lambda fix: fix["start"])
        if any(a["end"] > b["start"] for a, b in zip(ordered, ordered[1:])):
            yield from ([fix] for fix in batch)
        else:
            for start in range(0, len(ordered), 24):
                yield ordered[start:start + 24]


def apply_repair_chain(source: Path, target: Path, plan: dict, skip_fix: str = "") -> None:
    graph = [f"[0:a:0]{plan['global'] or 'anull'}[repair0]"]
    current = "repair0"
    for index, batch in enumerate(repair_batches(plan["fixes"], skip_fix), 1):
        fix = batch[0]
        following = f"repair{index}"
        if fix["end"] is None:
            graph.append(f"[{current}]{fix['filter']}[{following}]")
        else:
            # Identical filters with disjoint time windows can reuse one wet path.
            fades = []
            for item in batch:
                start, end = item["start"], item["end"]
                fade = min(.045, (end - start) / 2)
                fades.append(f"clip(min((t-{start:.9f})/{fade:.9f},({end:.9f}-t)/{fade:.9f}),0,1)")
            wet = "+".join(fades)
            graph.extend([
                f"[{current}]asetnsamples=n=128:p=0,asplit[dry{index}][wet{index}]",
                f"[dry{index}]volume='1-({wet})':eval=frame[d{index}]",
                f"[wet{index}]{fix['filter']},volume='{wet}':eval=frame[w{index}]",
                f"[d{index}][w{index}]amix=inputs=2:duration=first:normalize=0[{following}]",
            ])
        current = following
    graph.append(f"[{current}]{plan['tone'] or 'anull'}[out]")
    command = [resolve_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(source),
               "-filter_complex", ";".join(graph), "-map", "[out]", "-map_metadata", "-1",
               "-map_chapters", "-1", "-c:a", "pcm_f32le", str(target)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-800:] or "Could not render album corrections")


def process_album_track(source: Path, target: Path, settings: dict, *, skip_fix: str = "") -> None:
    filters = settings.get("track_filters", {}).get(source.name, settings.get("filters")) or "anull"
    noise = settings.get("track_noise", {}).get(source.name, settings.get("noise_chain"))
    plan = settings.get("track_repairs", {}).get(source.name, settings.get("repair_chain"))
    spectral = settings.get("track_spectral_noise", {}).get(source.name, settings.get("spectral_noise"))
    if spectral:
        from sonustemper import spectral_cleanup

        with tempfile.TemporaryDirectory(prefix="album-spectral-stage-") as folder:
            stage = Path(folder) / "input.wav"
            if plan is None or noise:
                process_track(source, stage, filters if plan is None else "", noise)
            else:
                stage = source
            if plan is None:
                spectral_cleanup.render(stage, target, spectral)
            else:
                cleaned = Path(folder) / "cleaned.wav"
                spectral_cleanup.render(stage, cleaned, spectral)
                apply_repair_chain(cleaned, target, plan, skip_fix)
        return
    if plan is None:
        process_track(source, target, filters, noise)
    elif noise:
        cleaned = target.with_name(target.stem + ".pre-repair.wav")
        try:
            process_track(source, cleaned, "", noise)
            apply_repair_chain(cleaned, target, plan, skip_fix)
        finally:
            cleaned.unlink(missing_ok=True)
    else:
        apply_repair_chain(source, target, plan, skip_fix)


def compare_noise(track: Path, before: dict, after: dict, folder: Path) -> None:
    dry, wet, removed = (folder / f"{name}.wav" for name in ("before", "after", "removed"))
    process_album_track(track, dry, before)
    process_album_track(track, wet, after)
    subprocess.run([
        resolve_tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(dry), "-i", str(wet),
        "-filter_complex", "[1:a]volume=-1[inverted];[0:a][inverted]amix=inputs=2:duration=first:normalize=0",
        "-map_metadata", "-1", "-c:a", "pcm_f32le", str(removed),
    ], check=True, capture_output=True, timeout=900)


def compare_repair(track: Path, settings: dict, repair_id: str, start: float, length: float) -> tuple[bytes, bytes]:
    with tempfile.TemporaryDirectory(prefix="album-compare-") as folder:
        work = Path(folder)
        # Include the whole track in both renders so filter state and regional timing match export.
        versions = []
        for name, skip in (("before", repair_id), ("after", "")):
            rendered, excerpt = work / "track.wav", work / f"{name}.wav"
            process_album_track(track, rendered, settings, skip_fix=skip)
            ffmpeg(rendered, excerpt, "-ss", str(start), "-t", str(length), "-ac", "2", "-ar", "48000", "-c:a", "pcm_f32le")
            versions.append((excerpt, *metrics(excerpt)))
        level = comparison_level(versions)
        output = []
        for path, lufs, _ in versions:
            target = work / "audition.wav"
            ffmpeg(path, target, "-af", f"volume={level - lufs:.6f}dB", "-c:a", "pcm_s16le", "-bitexact")
            output.append(target.read_bytes())
        return output[0], output[1]


def comparison_level(versions: list[tuple[Path, float, float]]) -> float:
    return min(min(lufs, lufs - peak - 2) for _, lufs, peak in versions)


def compare_song_repairs(track: Path, settings: dict, bypass: set[str], folder: Path) -> None:
    plan = settings.get("track_repairs", {}).get(track.name, settings.get("repair_chain"))
    before = {**settings, "track_repairs": {track.name: {
        **plan, "fixes": [fix for fix in plan["fixes"] if fix["id"] not in bypass],
    }}}
    versions = []
    for name, render_settings in (("before", before), ("after", settings)):
        rendered = folder / f"{name}-float.wav"
        process_album_track(track, rendered, render_settings)
        versions.append((rendered, *metrics(rendered)))
    write_matched_pair(versions, folder)


def write_matched_pair(versions: list[tuple[Path, float, float]], folder: Path, *, native_format=False) -> float:
    level = comparison_level(versions)
    format_options = [] if native_format else ["-ac", "2", "-ar", "48000"]
    for name, (path, lufs, _) in zip(("before", "after"), versions):
        ffmpeg(path, folder / f"{name}.wav", "-af", f"volume={level - lufs:.6f}dB",
               *format_options, "-c:a", "pcm_s24le", "-bitexact")
        path.unlink()
    return level


def compare_tone(track: Path, off: dict, on: dict, folder: Path, *, match_stereo: bool = False) -> dict:
    cleaned = folder / "post-cleanup.wav"
    render_cleanup_audio(track, cleaned, on)
    versions = []
    for side, data in (("before", off), ("after", on)):
        rendered = folder / f"{side}-float.wav"
        # Width can produce stereo from a mono input. Measure both sides in the same layout.
        ffmpeg(cleaned, rendered, "-af", data["repair_chain"]["tone"] or "anull",
               *(["-ac", "2"] if match_stereo else []), "-c:a", "pcm_f32le")
        versions.append((rendered, *metrics(rendered)))
    cleaned.unlink()
    level = write_matched_pair(versions, folder, native_format=True)
    return {"off_peak": versions[0][2], "on_peak": versions[1][2], "matched_lufs": level}


def render_cleanup_audio(track: Path, target: Path, settings: dict) -> None:
    plan = settings.get("track_repairs", {}).get(track.name, settings.get("repair_chain"))
    if plan is None:
        raise ValueError("Use the repair-before-EQ chain to render cleanup")
    data = {**settings, "track_repairs": {track.name: {**plan, "tone": ""}}}
    process_album_track(track, target, data)


def normalize(clean: Path, output: Path, gain: float, ceiling: float) -> tuple[float, float, float | None]:
    # Write a fresh PCM WAV, without source tags or generated ancillary metadata.
    ffmpeg(clean, output, "-af", f"volume={gain:.6f}dB", "-c:a", "pcm_s24le", "-bitexact",
           "-write_bext", "0", "-write_peak", "0")
    measured = measure_loudness(output)
    lufs, peak = measured.get("I"), measured.get("TP")
    if lufs is None or peak is None or not all(map(math.isfinite, (lufs, peak))):
        raise ValueError(f"Could not measure loudness for {output.name}")
    if peak > ceiling + 0.1:
        raise ValueError(f"{clean.name} exceeds the WAV true-peak ceiling")
    return lufs, peak, measured.get("LRA")


def encode_mp3(source: Path, output: Path) -> None:
    ffmpeg(source, output, "-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3")


def artwork_size(path: Path) -> tuple[int, int] | None:
    try:
        probe = subprocess.run([resolve_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=width,height", "-of", "json", str(path)],
                               check=True, capture_output=True, timeout=8)
        stream = json.loads(probe.stdout)["streams"][0]
        return stream["width"], stream["height"]
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return None


def cover_options(album: Path) -> list[dict]:
    covers = []
    for path in sorted(album.iterdir(), key=lambda item: item.name.casefold()):
        if path.name.startswith((".", "#", "@")) or path.suffix.lower() not in ARTWORK_SUFFIXES or path.is_symlink():
            continue
        try:
            if path.is_file() and artwork_size(path) == (ARTWORK_SIZE, ARTWORK_SIZE):
                covers.append({"name": path.name, "size": path.stat().st_size})
        except OSError:
            continue
    return covers


def artwork_jpeg(source: Path, target: Path, *, thumbnail: bool = False) -> None:
    scale = ("-vf", "scale=360:360") if thumbnail else ()
    subprocess.run([resolve_tool("ffmpeg"), "-nostdin", "-v", "error", "-i", str(source),
                    *scale, "-frames:v", "1", "-q:v", "3", str(target)], check=True, timeout=60)


def render_artwork(work: Path, cover: Path | None) -> bytes | None:
    if cover is None:
        return None
    artwork = work / "cover.jpg"
    artwork_jpeg(cover, artwork)
    return artwork.read_bytes()


def delivery_cover(album: Path, name: str) -> Path | None:
    if not name:
        return None
    cover = child(album, name)
    if (not cover.is_file() or cover.suffix.lower() not in ARTWORK_SUFFIXES
            or artwork_size(cover) != (ARTWORK_SIZE, ARTWORK_SIZE)):
        raise ValueError("Choose an existing 3000 x 3000 cover image")
    return cover


def tag_delivery(album: Path, mp3: Path, track: str, settings: dict, cover_data: bytes | None,
                 track_order: dict[str, int]) -> Path | None:
    stem = Path(track).stem
    number = re.match(r"^(\d+)\.\s*", stem)
    overrides = settings.get("tags", {}).get(track, {})
    title = overrides.get("title") or (stem[number.end():] if number else stem)
    track_number = overrides.get("number") or (number[1] if number else track_order.get(stem, 1))
    artist = settings.get("artist") or album.parent.name
    tags = ID3()
    for frame, value in [(TIT2, title), (TPE1, artist), (TPE2, settings.get("album_artist") or artist),
                         (TALB, settings.get("album") or album.name),
                         (TRCK, f"{track_number}/{len(track_order)}"), (TYER, settings["year"])]:
        tags.add(frame(encoding=1, text=[str(value)]))
    if cover_data:
        tags.add(APIC(encoding=1, mime="image/jpeg", type=3, desc="Front cover", data=cover_data))
    lyrics = text_asset(album, "lyrics", stem, ".txt").strip()
    if lyrics:
        tags.add(USLT(encoding=1, lang="eng", desc="", text=lyrics))
    tags.save(mp3, v2_version=3)
    if lyrics:
        sidecar = mp3.with_suffix(".lrc")
        sidecar.write_text(lyrics + "\n", encoding="utf-8")
        return sidecar
    return None


def stage_missing_lyrics(album: Path, work: Path, track: str, sidecar: Path | None) -> str | None:
    if sidecar is None:
        return None
    relative = f"lyrics/{Path(track).stem}.lrc"
    if publication_path(album, relative).exists():
        return None
    staged = publication_path(work, relative)
    staged.parent.mkdir(exist_ok=True)
    shutil.copyfile(sidecar, staged)
    return relative


def album_session(database: Path, key: str, draft: dict | None = None) -> dict | None:
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS album_sessions (id TEXT PRIMARY KEY, draft TEXT NOT NULL)")
        if draft is not None:
            conn.execute("INSERT OR REPLACE INTO album_sessions VALUES (?, ?)", (key, json.dumps(draft)))
            return draft
        row = conn.execute("SELECT draft FROM album_sessions WHERE id = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None


REVIEW_TTL = 4 * 60 * 60


def review_assets(album: Path, tracks: list[Path], cover: Path | None) -> dict:
    return {"cover": digest(cover) if cover else None, "lyrics": {
        track.name: hashlib.sha256(text_asset(album, "lyrics", track.stem, ".txt").encode("utf-8")).hexdigest()
        for track in tracks
    }}


def integrated_album(files: list[Path], work: Path) -> float:
    if len(files) == 1:
        return metrics(files[0])[0]
    joined = work / "album.txt"
    combined = work / "album.wav"
    try:
        joined.write_text("".join("file '" + str(path).replace("'", "'\\''") + "'\n"
                                  for path in files), encoding="utf-8")
        subprocess.run([resolve_tool("ffmpeg"), "-nostdin", "-v", "error", "-f", "concat",
                        "-safe", "0", "-i", str(joined), "-c", "copy", "-rf64", "auto",
                        str(combined)], check=True, timeout=900)
        return metrics(combined)[0]
    finally:
        joined.unlink(missing_ok=True)
        combined.unlink(missing_ok=True)


def peak_time(path: Path) -> float | None:
    # EBU meter metadata reports its running true-peak maximum on 100 ms frames.
    result = subprocess.run([
        resolve_tool("ffmpeg"), "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-af", "ebur128=peak=true:metadata=1,ametadata=mode=print:key=lavfi.r128.true_peak:file=-",
        "-f", "null", "-",
    ], capture_output=True, text=True, timeout=900)
    points = re.findall(r"pts_time:([-+\d.eE]+)\s+lavfi\.r128\.true_peak=([-+\d.eE]+)", result.stdout)
    if result.returncode == 0 and points:
        return max(0., float(max(points, key=lambda point: float(point[1]))[0]))
    return None


class AlbumProgress:
    def __init__(self, report, total: int):
        self.report = report
        self.total = total
        self.started = 0

    def step(self, stage: str, track: str = "", track_number: int | None = None) -> None:
        self.report(stage=stage, track=track, track_number=track_number,
                    progress={"completed": self.started, "total": self.total})
        self.started += 1

    def finish(self, **changes) -> None:
        if changes.get("status") in {"ready", "complete"}:
            changes.update(track="", track_number=None, progress={"completed": self.total, "total": self.total})
        self.report(**changes)


def confirm_review(album: Path, tracks: list[Path], settings: dict, work: Path, assets: dict, outputs: list[dict],
                   report, previous: dict[str, dict]) -> None:
    progress = AlbumProgress(report, len(tracks) + 2)
    progress.step("Checking album, cover and lyrics")
    require_full_album(album, settings["source"], tracks)
    if [track.name for track in tracks] != [result["track"] for result in outputs]:
        raise ValueError("The album track list changed. Review it again.")
    cover = delivery_cover(album, settings.get("cover", ""))
    if review_assets(album, tracks, cover) != assets:
        raise ValueError("Cover art or lyrics changed. Review the album again.")
    for number, (track, row) in enumerate(zip(tracks, outputs), 1):
        progress.step("Verifying reviewed audio", track.name, number)
        if digest(track) != row["source_sha256"]:
            raise ValueError(f"{track.name} changed. Review the album again.")
        for relative, fingerprint in row["output_sha256"].items():
            if digest(publication_path(work, relative)) != fingerprint:
                raise ValueError("A reviewed file changed. Review the album again.")
    stages = {relative.split("/", 1)[0] for row in outputs for relative in row["files"]}
    for stage in stages:
        child(album, stage).mkdir(exist_ok=True)
    progress.step("Publishing delivery files")
    publish_deliveries(album, tracks, work, outputs, stages, True, progress.finish, previous)


def publish_deliveries(album: Path, tracks: list[Path], work: Path, outputs: list[dict], stages,
                       replace: bool, report, previous: dict[str, dict]) -> None:
    paths = {path for result in outputs for path in result["files"]}
    superseded = {path for path in owned_outputs(album, tracks, previous)
                  if path.split("/", 1)[0] in stages and not path.startswith("lyrics/")}
    # Do not delete or replace a hand-authored lyrics file based on its name alone.
    for relative in paths - superseded:
        if relative.endswith(".lrc"):
            target = publication_path(album, relative)
            if target.is_file() and digest(target) != digest(publication_path(work, relative)):
                raise FileExistsError(f"An unmanaged lyrics file exists: {relative}")
    superseded.update(path for path in delivery_paths(album, tracks) if not path.endswith(".lrc"))
    publish(album, work, paths, superseded, replace, report, outputs)


def render_album(album: Path, tracks: list[Path], settings: dict, report=lambda **_: None,
                 *, audition: tuple[str, Path, str] | None = None, previous: dict[str, dict] | None = None,
                 review: bool = False) -> list[dict]:
    if not tracks:
        raise ValueError("Choose at least one WAV")
    tags_only = settings.get("metadata_only") and not audition
    count = len(tracks)
    total = count + 3 if tags_only else (5 * count + 4 + (count + 1 if review else 0))
    progress = AlbumProgress(report if not audition else lambda **_: None, total)
    progress.step("Checking source files")
    stage = settings["source"]
    if not audition:
        require_full_album(album, stage, tracks)
    cover = None if audition else delivery_cover(album, settings.get("cover", ""))
    output_stages = ["clean", "normalized", "mp3s"]
    if stage == "raw":
        output_stages.insert(0, "mastered")
    if audition is None and not review:
        for directory in output_stages:
            child(album, directory).mkdir(exist_ok=True)
    full_track_list = wavs(album, stage)
    track_order = {track.stem: index for index, track in enumerate(full_track_list, 1)}
    fingerprints = {track.name: digest(track) for track in tracks}
    assets = review_assets(album, tracks, cover) if review else {}
    previous = previous or {}
    # Keep staged files and backups available to the next process until publication is recorded.
    work = Path(tempfile.mkdtemp(prefix=".sonustemper-", dir=audition[1].parent if audition else album))
    retain_work = False
    def report_publication(**changes):
        nonlocal retain_work
        progress.finish(**changes)
        retain_work = bool(changes.get("publication"))

    try:
        if not audition:
            report(work=work.name)
        for folder in output_stages:
            (work / folder).mkdir()
        if tags_only:
            results = []
            paths, superseded = set(), set()
            progress.step("Preparing album artwork")
            cover_data = render_artwork(work, cover)
            for index, track in enumerate(tracks, 1):
                progress.step("Updating MP3 tags and lyrics", track.name, index)
                result = previous.get(track.name)
                if not result or result.get("source") != stage or result["source_sha256"] != fingerprints[track.name]:
                    raise ValueError(f"Render {track.name} before updating its tags")
                relative = f"mp3s/{result['mp3']}"
                mp3 = work / relative
                source_mp3 = publication_path(album, relative)
                if digest(source_mp3) != result.get("output_sha256", {}).get(relative):
                    raise ValueError(f"The delivered MP3 changed on disk: {result['mp3']}")
                shutil.copyfile(source_mp3, mp3)
                sidecar = tag_delivery(album, mp3, track.name, settings, cover_data, track_order)
                files = [path for path in result["files"] if not path.endswith(".lrc")]
                superseded.update(path for path in owned_outputs(album, [track], previous)
                                  if path.startswith("mp3s/") and path.endswith(".lrc"))
                paths.add(relative)
                checksums = {path: sha for path, sha in result["output_sha256"].items() if not path.endswith(".lrc")}
                checksums[relative] = digest(mp3)
                if sidecar:
                    sidecar_path = f"mp3s/{sidecar.name}"
                    if publication_path(album, sidecar_path).exists() and sidecar_path not in superseded:
                        raise FileExistsError(f"An unmanaged lyrics file exists: {sidecar_path}")
                    paths.add(sidecar_path)
                    files.append(sidecar_path)
                    checksums[sidecar_path] = digest(sidecar)
                companion = stage_missing_lyrics(album, work, track.name, sidecar)
                if companion:
                    target = publication_path(album, companion)
                    if target.exists():
                        raise FileExistsError(f"A lyrics file was added during export: {companion}")
                    paths.add(companion)
                    files.append(companion)
                    checksums[companion] = digest(publication_path(work, companion))
                results.append({**result, "files": files, "output_sha256": checksums})
            progress.step("Publishing updated tags")
            require_full_album(album, stage, tracks)
            publish(album, work, paths, superseded, True, report_publication, results)
            return results
        clean_files = []
        for index, track in enumerate(tracks, 1):
            progress.step("Processing audio", track.name, index)
            mastered = track
            if stage == "raw":
                mastered = work / "mastered" / track.name
                process_album_track(track, mastered, settings)
            clean = work / "clean" / track.name
            if stage == "raw":
                floating = work / "delivery" / track.name
                floating.parent.mkdir(exist_ok=True)
                ffmpeg(mastered, floating, "-c:a", "pcm_f32le", "-ac", "2", "-ar", "48000", "-bitexact")
                clean = floating
            else:
                ffmpeg(mastered, clean, "-c:a", "pcm_s24le", "-ac", "2", "-ar", "48000", "-bitexact")
            clean_files.append(clean)
        measurements = []
        for index, path in enumerate(clean_files, 1):
            progress.step("Measuring loudness and true peak", path.name, index)
            measurements.append(metrics(path))
        progress.step("Calculating delivery gain")
        loudness_target = settings["lufs"]
        ceiling = settings["peak"]
        limits = []
        if settings["balance"] == "album":
            album_lufs = integrated_album(clean_files, work) if len(clean_files) > 1 else measurements[0][0]
            desired = loudness_target - album_lufs
            limiting = max(range(len(measurements)), key=lambda i: measurements[i][1])
            gain = min(desired, ceiling - measurements[limiting][1])
            gains = [gain] * len(clean_files)
            if desired - gain > .05:
                limits.append((limiting, desired - gain))
        else:
            gains = [min(loudness_target - lufs, ceiling - peak) for lufs, peak in measurements]
            limits = [(i, loudness_target - lufs - gains[i]) for i, (lufs, _) in enumerate(measurements)
                      if loudness_target - lufs - gains[i] > .05]
        if audition:
            track_name, destination, format = audition
            index = next(i for i, path in enumerate(clean_files) if path.name == track_name)
            output = work / "audition.wav"
            normalize(clean_files[index], output, gains[index], ceiling)
            if format == "mp3":
                encode_mp3(output, destination)
            else:
                os.replace(output, destination)
            return []
        progress.step("Preparing album artwork")
        cover_data = render_artwork(work, cover)
        outputs = []
        for index, (clean, gain, (_, clean_peak)) in enumerate(zip(clean_files, gains, measurements), 1):
            progress.step("Writing final WAV", clean.name, index)
            if stage == "raw":
                # The clean WAV has its own headroom limit before integer conversion.
                clean_gain = min(0.0, -clean_peak - 0.1)
                ffmpeg(clean, work / "clean" / clean.name, "-af", f"volume={clean_gain:.6f}dB",
                       "-c:a", "pcm_s24le", "-bitexact")
            normalized = work / "normalized" / clean.name
            lufs, peak, lra = normalize(clean, normalized, gain, ceiling)
            stem = f"{clean.stem} (Integrated {lufs:.1f} LUFS, True Peak {peak:.1f} dB, Channel STEREO)"
            named = normalized.with_name(stem + ".wav")
            normalized.rename(named)
            progress.step("Encoding MP3 and adding tags and lyrics", clean.name, index)
            mp3 = work / "mp3s" / (stem + ".mp3")
            encode_mp3(named, mp3)
            _, mp3_peak = metrics(mp3)
            sidecar = tag_delivery(album, mp3, clean.name, settings, cover_data, track_order)
            files = [f"clean/{clean.name}", f"normalized/{named.name}", f"mp3s/{mp3.name}"]
            if stage == "raw":
                files.append(f"mastered/{clean.name}")
            if sidecar:
                files.append(f"mp3s/{sidecar.name}")
            companion = stage_missing_lyrics(album, work, clean.name, sidecar)
            if companion:
                files.append(companion)
            outputs.append({"track": clean.name, "wav": named.name, "mp3": mp3.name,
                            "lufs": lufs, "lra": lra, "true_peak": peak, "mp3_true_peak": mp3_peak,
                            "gain_db": round(gain, 2),
                            "mp3_peak_warning": mp3_peak > ceiling,
                            "source": stage, "source_sha256": fingerprints[clean.name], "files": files,
                            "output_sha256": {path: digest(work / path) for path in files}})
        if review:
            for index, (track, row) in enumerate(zip(tracks, outputs), 1):
                progress.step("Measuring original for level-matched listening", track.name, index)
                try:
                    row["source_lufs"], row["source_true_peak"] = metrics(track)
                except ValueError:
                    # A silent source cannot be level matched, but can still be reviewed.
                    pass
            progress.step("Checking album loudness and limiting peaks")
            limit_rows = [{"track": tracks[i].name, "gain_shortfall_db": round(diff, 1),
                           "time": peak_time(clean_files[i])} for i, diff in limits]
            achieved = integrated_album([work / "normalized" / row["wav"] for row in outputs], work)
            if review_assets(album, tracks, cover) != assets:
                raise ValueError("Cover art or lyrics changed during review. Prepare the album again.")
        for index, track in enumerate(tracks, 1):
            progress.step("Verifying source recording", track.name, index)
            if digest(track) != fingerprints[track.name]:
                raise ValueError(f"Source changed during render: {track.name}")
        require_full_album(album, stage, tracks)
        if review:
            progress.step("Preparing the reviewed album")
            shutil.rmtree(work / "delivery", ignore_errors=True)
            progress.finish(status="ready", stage="Ready to review", results=outputs, review_assets=assets, expires=time()+REVIEW_TTL,
                   loudness={"target_lufs": loudness_target, "achieved_lufs": achieved, "ceiling": ceiling,
                             "balance": settings["balance"], "limits": limit_rows})
            retain_work = True
        else:
            progress.step("Publishing delivery files")
            publish_deliveries(album, tracks, work, outputs, output_stages, settings.get("replace", False),
                               report_publication, previous)
        return outputs
    finally:
        if not retain_work:
            shutil.rmtree(work, ignore_errors=True)
