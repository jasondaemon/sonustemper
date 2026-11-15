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
from itertools import groupby
from pathlib import Path
from threading import Lock

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
            save_text_asset(album, stage, stem, suffix, text + "\n" if text else "")
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


def record(database: Path, job: str, **changes) -> dict:
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS album_jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
        if changes:
            conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state FROM album_jobs WHERE id = ?", (job,)).fetchone()
        state = json.loads(row[0]) if row else {}
        if changes:
            state.update(changes)
            conn.execute("INSERT OR REPLACE INTO album_jobs VALUES (?, ?)", (job, json.dumps(state)))
        return state


def job_states(database: Path, statuses: tuple[str, ...]) -> list[tuple[str, dict]]:
    with sqlite3.connect(database) as conn:
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
    if stage not in {"mastered", "clean", "normalized", "mp3s"}:
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
        rendered = folder / f"{name}.wav"
        process_album_track(track, rendered, render_settings)
        versions.append((rendered, *metrics(rendered)))
    level = comparison_level(versions)
    for name, (path, lufs, _) in zip(("before", "after"), versions):
        ffmpeg(path, folder / f"{name}.mp3", "-af", f"volume={level - lufs:.6f}dB",
               "-ac", "2", "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "320k")
        path.unlink()


def normalize(clean: Path, output: Path, gain: float, ceiling: float) -> tuple[float, float]:
    # Write a fresh PCM WAV, without source tags or generated ancillary metadata.
    ffmpeg(clean, output, "-af", f"volume={gain:.6f}dB", "-c:a", "pcm_s24le", "-bitexact",
           "-write_bext", "0", "-write_peak", "0")
    lufs, peak = metrics(output)
    if peak > ceiling + 0.1:
        raise ValueError(f"{clean.name} exceeds the WAV true-peak ceiling")
    return lufs, peak


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
    if lyrics and settings.get("sidecar"):
        sidecar = mp3.with_suffix(".lrc")
        sidecar.write_text(lyrics + "\n", encoding="utf-8")
        return sidecar
    return None


def album_session(database: Path, key: str, draft: dict | None = None) -> dict | None:
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS album_sessions (id TEXT PRIMARY KEY, draft TEXT NOT NULL)")
        if draft is not None:
            conn.execute("INSERT OR REPLACE INTO album_sessions VALUES (?, ?)", (key, json.dumps(draft)))
            return draft
        row = conn.execute("SELECT draft FROM album_sessions WHERE id = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None


def render_album(album: Path, tracks: list[Path], settings: dict, report=lambda **_: None,
                 *, audition: tuple[str, Path, str] | None = None, previous: dict[str, dict] | None = None) -> list[dict]:
    if not tracks:
        raise ValueError("Choose at least one WAV")
    stage = settings["source"]
    if not audition:
        require_full_album(album, stage, tracks)
    cover = None if audition else delivery_cover(album, settings.get("cover", ""))
    output_stages = ["clean", "normalized", "mp3s"]
    if stage == "raw":
        output_stages.insert(0, "mastered")
    if audition is None:
        for directory in output_stages:
            child(album, directory).mkdir(exist_ok=True)
    full_track_list = wavs(album, stage)
    track_order = {track.stem: index for index, track in enumerate(full_track_list, 1)}
    fingerprints = {track.name: digest(track) for track in tracks}
    previous = previous or {}
    # Keep staged files and backups available to the next process until publication is recorded.
    work = Path(tempfile.mkdtemp(prefix=".sonustemper-", dir=audition[1].parent if audition else album))
    retain_work = False
    def report_publication(**changes):
        nonlocal retain_work
        report(**changes)
        retain_work = bool(changes.get("publication"))

    try:
        if not audition:
            report(work=work.name)
        for folder in output_stages:
            (work / folder).mkdir()
        if settings.get("metadata_only") and not audition:
            results = []
            paths, superseded = set(), set()
            cover_data = render_artwork(work, cover)
            for index, track in enumerate(tracks, 1):
                report(stage="Updating MP3 tags", track=track.name, track_number=index)
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
                superseded.update(path for path in owned_outputs(album, [track], previous) if path.endswith(".lrc"))
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
                results.append({**result, "files": files, "output_sha256": checksums})
            require_full_album(album, stage, tracks)
            publish(album, work, paths, superseded, True, report_publication, results)
            return results
        clean_files = []
        for index, track in enumerate(tracks, 1):
            report(stage="Processing song", track=track.name, track_number=index)
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
        report(stage="Measuring loudness", track="", track_number=None)
        measurements = [metrics(path) for path in clean_files]
        loudness_target = settings["lufs"]
        ceiling = settings["peak"]
        if settings["balance"] == "album" and len(clean_files) > 1:
            joined = work / "album.txt"
            # Temporary filenames are escaped for FFmpeg's concat demuxer.
            joined.write_text("".join("file '" + str(path).replace("'", "'\\''") + "'\n"
                                      for path in clean_files), encoding="utf-8")
            combined = work / "album.wav"
            subprocess.run([resolve_tool("ffmpeg"), "-nostdin", "-v", "error", "-f", "concat",
                            "-safe", "0", "-i", str(joined), "-c", "copy", "-rf64", "auto",
                            str(combined)], check=True, timeout=900)
            album_lufs, _ = metrics(combined)
            gain = min(loudness_target - album_lufs, ceiling - max(tp for _, tp in measurements))
            gains = [gain] * len(clean_files)
        else:
            gains = [min(loudness_target - lufs, ceiling - peak) for lufs, peak in measurements]
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
        cover_data = render_artwork(work, cover)
        outputs = []
        for index, (clean, gain, (_, clean_peak)) in enumerate(zip(clean_files, gains, measurements), 1):
            report(stage="Building final WAV and MP3", track=clean.name, track_number=index)
            if stage == "raw":
                # The clean WAV has its own headroom limit before integer conversion.
                clean_gain = min(0.0, -clean_peak - 0.1)
                ffmpeg(clean, work / "clean" / clean.name, "-af", f"volume={clean_gain:.6f}dB",
                       "-c:a", "pcm_s24le", "-bitexact")
            normalized = work / "normalized" / clean.name
            lufs, peak = normalize(clean, normalized, gain, ceiling)
            stem = f"{clean.stem} (Integrated {lufs:.1f} LUFS, True Peak {peak:.1f} dB, Channel STEREO)"
            named = normalized.with_name(stem + ".wav")
            normalized.rename(named)
            mp3 = work / "mp3s" / (stem + ".mp3")
            encode_mp3(named, mp3)
            _, mp3_peak = metrics(mp3)
            sidecar = tag_delivery(album, mp3, clean.name, settings, cover_data, track_order)
            files = [f"clean/{clean.name}", f"normalized/{named.name}", f"mp3s/{mp3.name}"]
            if stage == "raw":
                files.append(f"mastered/{clean.name}")
            if sidecar:
                files.append(f"mp3s/{sidecar.name}")
            outputs.append({"track": clean.name, "wav": named.name, "mp3": mp3.name,
                            "lufs": lufs, "true_peak": peak, "mp3_true_peak": mp3_peak,
                            "mp3_peak_warning": mp3_peak > ceiling,
                            "source": stage, "source_sha256": fingerprints[clean.name], "files": files,
                            "output_sha256": {path: digest(work / path) for path in files}})
        for track in tracks:
            if digest(track) != fingerprints[track.name]:
                raise ValueError(f"Source changed during render: {track.name}")
        require_full_album(album, stage, tracks)
        paths = {path for result in outputs for path in result["files"]}
        superseded = {path for path in owned_outputs(album, tracks, previous)
                      if path.split("/", 1)[0] in output_stages}
        superseded.update(delivery_paths(album, tracks))
        publish(album, work, paths, superseded, settings.get("replace", False), report_publication, outputs)
        return outputs
    finally:
        if not retain_work:
            shutil.rmtree(work, ignore_errors=True)
