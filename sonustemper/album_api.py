"""HTTP boundary for the album workspace."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Literal
import base64
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import subprocess
from threading import BoundedSemaphore
from time import time
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from starlette.background import BackgroundTask

from sonustemper import album_workflow as workflow
from sonustemper import album_cleanup as cleanup
from sonustemper import master_pack
from sonustemper.storage import LIBRARY_DB, PREVIEWS_DIR, PRESETS_DIR
from sonustemper.tools import bundle_root

router = APIRouter()
worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="album-render")
logger = logging.getLogger(__name__)
compare_slots = BoundedSemaphore(2)


class Band(BaseModel):
    type: str = "peaking"
    freq_hz: float = Field(ge=20, le=20000)
    gain_db: float = Field(default=0, ge=-24, le=24)
    q: float = Field(default=0.7, ge=0.1, le=20)
    enabled: bool = True


class AlbumInput(BaseModel):
    location: str
    artist_folder: str
    album_folder: str
    source: str = "raw"


class Correction(BaseModel):
    enabled: bool = True
    value: float = Field(default=0, ge=-24, le=100)
    options: dict[str, float | bool] = Field(default_factory=dict)


class Repair(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["rumble", "presence", "highs"]
    enabled: bool = True
    frequency: float = Field(ge=20, le=18000)
    amount: float = Field(ge=0, le=12)
    start: float = Field(default=0, ge=0, le=36000)
    end: float | None = Field(default=None, gt=0, le=36000)


class NoiseSelection(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    f_low: float = Field(ge=20, le=20000)
    f_high: float = Field(ge=20, le=20000)
    band_depth_db: float = Field(default=18, ge=0, le=30)
    afftdn_strength: float = Field(default=0, ge=0, le=1)
    hp_hz: float = Field(default=0, ge=0, le=20000)
    lp_hz: float = Field(default=0, ge=0, le=20000)
    apply_scope: Literal["global", "selection"] = "selection"
    t0_sec: float = Field(default=0, ge=0, le=36000)
    t1_sec: float = Field(default=0, ge=0, le=36000)

    @model_validator(mode="after")
    def validate_range(self):
        if self.f_high <= self.f_low:
            raise ValueError("High frequency must exceed low frequency")
        if self.apply_scope == "selection" and self.t1_sec - self.t0_sec < .01:
            raise ValueError("Noise selections must be at least 0.01 seconds long")
        return self


class Settings(AlbumInput):
    tracks: list[str] = Field(min_length=1, max_length=100)
    preset: str = ""
    bands: list[Band] | None = Field(default=None, max_length=20)
    correction: str = ""
    correction_value: float = Field(default=0, ge=-24, le=100)
    eq_bypass: bool = False
    manual_bands: list[Band] = Field(default_factory=list, max_length=20)
    noise: dict[str, float | str] | None = None
    noise_selections: list[NoiseSelection] | None = Field(default=None, max_length=24)
    noise_enabled: bool = True
    voicing_enabled: bool = True
    voice_controls: dict = Field(default_factory=dict)
    corrections: dict[str, Correction] = Field(default_factory=dict)
    cleanup_enabled: bool = True
    cleanup_version: Literal[1, 2] = 1
    repairs: list[Repair] = Field(default_factory=list, max_length=128)
    strength: int = Field(default=100, ge=0, le=100)
    stereo_width: float | None = Field(default=None, ge=0.5, le=1.5)
    guardrails: bool = True
    balance: str = "album"
    lufs: float = Field(default=-16, ge=-70, le=-5)
    peak: float = Field(default=-1.5, ge=-9, le=0)
    artist: str = Field(default="", max_length=200)
    album_artist: str = Field(default="", max_length=200)
    album: str = Field(default="", max_length=200)
    cover: str = ""
    year: int = Field(default_factory=lambda: date.today().year, ge=1, le=9999)
    tags: dict[str, dict[str, str]] = Field(default_factory=dict)
    sidecar: bool = False
    replace: bool = False
    metadata_only: bool = False
    level_match: bool = True
    audition_track: str = ""
    audition_format: Literal["wav", "mp3"] = "wav"
    track_overrides: dict[str, dict] = Field(default_factory=dict)
    preview_start: float = Field(default=0, ge=0, le=36000)


def locations() -> dict[str, Path]:
    return {name: Path(path) for name, path in
            json.loads(os.getenv("SONUSTEMPER_ALBUM_LOCATIONS") or "{}").items()}


def guarded(parent: Path, name: str) -> Path:
    try:
        return workflow.child(parent, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def location_root(location: str) -> Path:
    path = locations().get(location)
    if path is None:
        raise HTTPException(404, "Unknown storage location")
    if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
        raise HTTPException(503, "Storage location is unavailable or access was denied")
    return path


def resolve_album(location: str, artist_folder: str, album_folder: str) -> Path:
    album = guarded(guarded(location_root(location), artist_folder), album_folder)
    if not album.is_dir():
        raise HTTPException(404, "Album not found")
    return album


def selected_tracks(album: Path, source: str, names: list[str]) -> list[Path]:
    if source not in {"raw", "mastered"}:
        raise HTTPException(400, "Choose raw or mastered")
    try:
        available = workflow.wavs(album, source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if len(set(names)) != len(names) or not set(names).issubset({p.name for p in available}):
        raise HTTPException(400, "Choose distinct WAVs from the selected album stage")
    return [path for path in available if path.name in names]


def presets() -> dict:
    entries = {}
    for kind, folder in [
        ("builtin", bundle_root() / "assets/presets/voicings"),
        ("voicing", PRESETS_DIR / "voicings"),
        ("profile", PRESETS_DIR / "profiles"),
    ]:
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                if isinstance(data.get("chain"), dict):
                    entries[f"{kind}/{path.stem}"] = data
            except (OSError, ValueError, AttributeError):
                logger.warning("Could not read album preset %s", path.name)
    return entries


def resolved_settings(settings: Settings) -> dict:
    from sonustemper.server import _ai_tool_combo_chain, _eq_voice_filters, _noise_filter_chain

    data = settings.model_dump()
    if settings.balance not in {"album", "track"}:
        raise HTTPException(400, "Choose album or track loudness")
    preset = presets().get(settings.preset)
    if settings.preset and preset is None:
        raise HTTPException(400, "Unknown mastering preset")
    chain = dict(preset["chain"]) if preset else {}
    if settings.bands is not None:
        chain["eq"] = [band.model_dump() for band in settings.bands if band.enabled]
    if settings.eq_bypass:
        chain["eq"] = []
    corrections = {"", "ai_deglass", "ai_vocal_smooth", "ai_bass_tight",
                   "ai_transient_soften", "ai_reverb_reduce"}
    tools = {key: value.model_dump() for key, value in settings.corrections.items()}
    if settings.correction and not tools:
        tools[settings.correction] = {"enabled": True, "value": settings.correction_value}
    if not tools.keys() <= corrections - {""}:
        raise HTTPException(400, "Unknown correction")
    if settings.cleanup_version == 2 and "ai_deglass" in tools:
        tools["ai_deglass"]["options"]["afftdn_strength"] = 0
    if settings.cleanup_version == 2:
        tools["ai_platform_safe"] = {"enabled": False}
    correction = _ai_tool_combo_chain(tools) if settings.cleanup_enabled else ""
    mastering = master_pack._voicing_filters_from_json(
        {"chain": chain}, settings.strength, settings.stereo_width, True, settings.guardrails
    )
    manual = [] if settings.eq_bypass else [
        master_pack._eq_filter_for_band(band.model_dump(), 1)
        for band in settings.manual_bands if band.enabled
    ]
    if not settings.noise_enabled:
        data["noise_chain"] = None
    elif settings.noise_selections is not None:
        data["noise_chain"] = cleanup.combine_noise_chains([
            _noise_filter_chain({**item.model_dump(), "mode": "remove"})
            for item in settings.noise_selections if item.enabled
        ])
    else:
        data["noise_chain"] = _noise_filter_chain({**settings.noise, "mode": "remove"}) if settings.noise else None
    voice = _eq_voice_filters(settings.voice_controls)
    tone = [mastering if settings.voicing_enabled else "", *manual]
    if settings.cleanup_version == 2:
        repairs = settings.repairs if settings.cleanup_enabled else []
        if len({fix.id for fix in repairs}) != len(repairs) or any(
                (fix.start != 0 if fix.end is None else fix.end - fix.start < .05) for fix in repairs):
            raise HTTPException(422, "Repairs need distinct IDs and ranges of at least 0.05 seconds")
        data["repair_chain"] = {
            "global": ",".join(filter(None, [correction, *(voice if settings.cleanup_enabled else [])])),
            "fixes": [{**fix.model_dump(), "filter": cleanup.repair_filter(fix.model_dump())}
                      for fix in repairs if fix.enabled and fix.amount > 0],
            "tone": ",".join(filter(None, tone)),
        }
    else:
        data["repair_chain"] = None
    data["filters"] = ",".join(filter(None, [correction, *(voice if settings.voicing_enabled else []), *tone]))
    return data


def require_writable(location: str) -> None:
    if location in json.loads(os.getenv("SONUSTEMPER_READ_ONLY_ALBUM_LOCATIONS") or "[]"):
        raise HTTPException(403, "Choose a writable test location to save or export")


@router.get("/api/albums/locations")
def get_locations():
    read_only = json.loads(os.getenv("SONUSTEMPER_READ_ONLY_ALBUM_LOCATIONS") or "[]")
    return [{"name": name, "available": path.is_dir() and os.access(path, os.R_OK | os.X_OK),
             "writable": name not in read_only and os.access(path, os.W_OK | os.X_OK)}
            for name, path in locations().items()]


def folders(path: Path) -> list[str]:
    return sorted((p.name for p in path.iterdir() if not p.name.startswith((".", "#", "@"))
                   and not p.is_symlink() and p.is_dir()), key=str.casefold)


def album_status(path: Path) -> dict:
    try:
        stages = workflow.album_stages(path)
    except (OSError, ValueError):
        return {"playable": False, "pending": None}
    tracks = stages["raw"] or stages["mastered"]
    delivery = workflow.track_delivery(stages)
    return {"playable": bool(tracks), "pending": sum(bool(delivery[name]) for name in tracks)}


def artist_status(path: Path) -> dict:
    try:
        albums = [album_status(path / name) for name in folders(path)]
    except OSError:
        return {"playable": False, "pending": None}
    return {"playable": any(item["playable"] for item in albums),
            "pending": sum(item["pending"] for item in albums) if all(item["pending"] is not None for item in albums) else None}


@router.get("/api/albums/browse")
def browse(location: str, artist_folder: str = "", album_folder: str = "", status: bool = False):
    if not artist_folder:
        root = location_root(location)
        names = folders(root)
        return [{"name": name, **artist_status(root / name)} for name in names] if status else names
    if not album_folder:
        path = guarded(location_root(location), artist_folder)
        if not path.is_dir():
            raise HTTPException(404, "Artist folder not found")
        names = folders(path)
        return [{"name": name, **album_status(path / name)} for name in names] if status else names
    album = resolve_album(location, artist_folder, album_folder)
    try:
        stages = workflow.album_stages(album)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    tracks = list(dict.fromkeys(stages["raw"] + stages["mastered"]))
    return {**stages, "delivery": workflow.track_delivery(stages), "covers": workflow.cover_options(album),
            "text_status": workflow.track_text_status(album, tracks)}


@router.get("/api/albums/presets")
def get_presets():
    return [{"id": key, "title": data.get("meta", {}).get("title") or key,
             "bands": data["chain"].get("eq", []), "meta": data.get("meta", {}),
             "width": data["chain"].get("stereo", {}).get("width")} for key, data in presets().items()]


@router.get("/api/albums/audio")
def source_audio(location: str, artist_folder: str, album_folder: str, source: str, track: str):
    album = resolve_album(location, artist_folder, album_folder)
    return FileResponse(selected_tracks(album, source, [track])[0], media_type="audio/wav")


@router.get("/api/albums/spectrogram")
def spectrogram(location: str, artist_folder: str, album_folder: str, source: str, track: str,
                scale: Literal["log", "lin"] = "log", range_db: int = 120,
                width: int = 1200,
                overview: bool = False):
    from sonustemper.server import _spectrogram_for_path

    album = resolve_album(location, artist_folder, album_folder)
    path = selected_tracks(album, source, [track])[0]
    if width not in {1200, 2400, 4800, 8192}:
        raise HTTPException(400, "Unsupported spectrogram width")
    return _spectrogram_for_path(path, 960 if overview else width, 128 if overview else 384,
                                 scale, range_db, "combined", album_axis=True)


@router.get("/api/albums/artwork")
def artwork(location: str, artist_folder: str, album_folder: str, name: str, thumbnail: bool = False):
    path = guarded(resolve_album(location, artist_folder, album_folder), name)
    if not path.is_file() or path.suffix.lower() not in workflow.ARTWORK_SUFFIXES:
        raise HTTPException(404, "Artwork not found")
    if thumbnail:
        directory = tempfile.TemporaryDirectory(prefix="album-cover-")
        try:
            output = Path(directory.name) / "cover.jpg"
            workflow.artwork_jpeg(path, output, thumbnail=True)
            return FileResponse(output, media_type="image/jpeg", background=BackgroundTask(directory.cleanup))
        except Exception:
            directory.cleanup()
            raise HTTPException(422, "Could not preview this cover") from None
    return FileResponse(path)


@router.get("/api/albums/analyze")
def analyze(location: str, artist_folder: str, album_folder: str, source: str, track: str):
    from sonustemper.server import _ai_tool_audio_info

    album = resolve_album(location, artist_folder, album_folder)
    path = selected_tracks(album, source, [track])[0]
    info = _ai_tool_audio_info(path)
    rate, duration = info.get("sample_rate"), info.get("duration_s")
    if not rate or not duration:
        raise HTTPException(422, "Could not read the song's sample rate and duration")
    try:
        return cleanup.analyze(path, int(rate), float(duration))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        logger.warning("Album analysis failed for %s: %s", path.name, exc)
        raise HTTPException(422, "Full-song analysis could not finish. Check the source WAV and try again.") from exc


@router.get("/api/albums/text")
def track_text(location: str, artist_folder: str, album_folder: str, source: str, track: str):
    album = resolve_album(location, artist_folder, album_folder)
    stem = selected_tracks(album, source, [track])[0].stem
    try:
        fields = workflow.read_track_text(album, stem)
    except (OSError, ValueError) as exc:
        raise HTTPException(400, "Invalid track text file") from exc
    return {**fields, "draft": workflow.lyrics_draft(fields["prompt"])}


class TrackText(AlbumInput):
    track: str
    text: str = Field(max_length=30000)
    kind: Literal["style", "negative", "prompt", "lyrics"] = "lyrics"


class Draft(BaseModel):
    text: str = Field(max_length=30000)


@router.post("/api/albums/lyrics/draft")
def draft_lyrics(body: Draft):
    return {"draft": workflow.lyrics_draft(body.text)}


@router.put("/api/albums/lyrics")
@router.put("/api/albums/text")
def save_track_text(body: TrackText):
    require_writable(body.location)
    album = resolve_album(body.location, body.artist_folder, body.album_folder)
    track = selected_tracks(album, body.source, [body.track])[0]
    try:
        text = workflow.write_track_text(album, track.stem, body.kind, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        logger.warning("Could not save album %s for %s: %s", body.kind, track.name, exc)
        raise HTTPException(403, "Could not write to the album folder") from exc
    return {"saved": True, "text": text, **({"draft": workflow.lyrics_draft(text)} if body.kind == "prompt" else {})}


PROCESSING_FIELDS = {
    "preset", "bands", "eq_bypass", "manual_bands", "noise", "voicing_enabled",
    "voice_controls", "corrections", "strength", "stereo_width", "guardrails", "cleanup_enabled", "noise_enabled",
    "cleanup_version", "repairs",
    "noise_selections",
}


def effective_track_settings(settings: Settings, override: dict) -> Settings:
    if not override.keys() <= PROCESSING_FIELDS:
        raise HTTPException(422, "Invalid track override")
    merged = {**settings.model_dump(), **override}
    if "noise" in override and "noise_selections" not in override:
        merged["noise_selections"] = None
    try:
        return Settings.model_validate(merged)
    except ValueError as exc:
        raise HTTPException(422, "Invalid track processing settings") from exc


def resolve_render(settings: Settings) -> dict:
    data = resolved_settings(settings)
    data["track_filters"] = {}
    data["track_noise"] = {}
    data["track_repairs"] = {}
    for name, override in settings.track_overrides.items():
        if name not in settings.tracks:
            continue
        resolved = resolved_settings(effective_track_settings(settings, override))
        data["track_filters"][name] = resolved["filters"]
        data["track_noise"][name] = resolved["noise_chain"]
        data["track_repairs"][name] = resolved["repair_chain"]
    return data


class Session(AlbumInput):
    draft: dict


def session_key(location: str, artist_folder: str, album_folder: str, source: str) -> str:
    resolve_album(location, artist_folder, album_folder)
    if source not in {"raw", "mastered"}:
        raise HTTPException(400, "Choose raw or mastered")
    return json.dumps([location, artist_folder, album_folder, source])


@router.get("/api/albums/session")
def get_session(location: str, artist_folder: str, album_folder: str, source: str):
    key = session_key(location, artist_folder, album_folder, source)
    return {"draft": workflow.album_session(LIBRARY_DB, key)}


@router.put("/api/albums/session")
def save_session(body: Session):
    key = session_key(body.location, body.artist_folder, body.album_folder, body.source)
    if len(json.dumps(body.draft)) > 512_000:
        raise HTTPException(413, "Album session is too large")
    return {"draft": workflow.album_session(LIBRARY_DB, key, body.draft)}


@router.post("/api/albums/noise-audition")
def noise_audition(settings: Settings, mode: Literal["remove", "solo"] = "remove"):
    from sonustemper.server import _noise_filter_chain

    album = resolve_album(settings.location, settings.artist_folder, settings.album_folder)
    track = selected_tracks(album, settings.source, settings.tracks)[0]
    data = resolve_render(settings)
    if mode == "solo" and not settings.noise:
        raise HTTPException(422, "Choose a noise selection to solo")
    if mode == "remove" and not data["track_noise"].get(track.name, data["noise_chain"]):
        raise HTTPException(422, "Enable at least one noise selection first")
    PREVIEWS_DIR.mkdir(exist_ok=True, parents=True)
    directory = tempfile.TemporaryDirectory(prefix="album-noise-", dir=PREVIEWS_DIR)
    try:
        output = Path(directory.name) / "audition.wav"
        if mode == "solo":
            chain = _noise_filter_chain({**settings.noise, "mode": "solo"})
            workflow.process_track(track, output, "", chain)
        else:
            workflow.process_album_track(track, output, data)
        return FileResponse(output, media_type="audio/wav", background=BackgroundTask(directory.cleanup))
    except Exception as exc:
        directory.cleanup()
        raise HTTPException(422, str(exc)) from exc


class RepairAudition(Settings):
    repair_id: str = Field(min_length=1, max_length=64)
    candidate: Repair | None = None


@router.post("/api/albums/repair-audition")
def repair_audition(settings: RepairAudition):
    from sonustemper.server import _ai_tool_audio_info

    if settings.source != "raw" or settings.audition_track not in settings.tracks:
        raise HTTPException(400, "Choose a raw track to audition")
    album = resolve_album(settings.location, settings.artist_folder, settings.album_folder)
    track = selected_tracks(album, settings.source, [settings.audition_track])[0]
    if settings.candidate:
        # Audition-only candidate. Keep the rest of the effective track settings fixed.
        effective = effective_track_settings(settings, settings.track_overrides.get(track.name, {}))
        staged = resolved_settings(effective)
        plan = staged["repair_chain"]
        if plan is None:
            raise HTTPException(422, "Enable the repair-before-EQ chain to audition a finding")
        fix = settings.candidate
        if fix.id != settings.repair_id or not fix.enabled or fix.amount <= 0 or fix.end is None or fix.end - fix.start < .05:
            raise HTTPException(422, "Invalid suggested repair range")
        if any(item["id"] == fix.id for item in plan["fixes"]):
            raise HTTPException(422, "Suggested repair ID already exists")
        plan["fixes"].append({**fix.model_dump(), "filter": cleanup.repair_filter(fix.model_dump())})
        data = staged
    else:
        data = resolve_render(settings)
    plan = data.get("track_repairs", {}).get(track.name, data["repair_chain"])
    fix = next((item for item in (plan or {}).get("fixes", []) if item["id"] == settings.repair_id), None)
    if not fix:
        raise HTTPException(422, "Enable this correction before auditioning it")
    info = _ai_tool_audio_info(track)
    duration = float(info.get("duration_s") or 0)
    if duration <= fix["start"]:
        raise HTTPException(422, "The correction starts beyond the end of the track")
    cursor = settings.preview_start
    if fix["end"] is not None and not fix["start"] <= cursor < fix["end"]:
        cursor = fix["start"]
    start = max(0, min(duration, cursor) - 1)
    length = min(9, duration - start)
    try:
        before, after = workflow.compare_repair(track, data, settings.repair_id, start, length)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        logger.warning("Repair audition failed for %s: %s", track.name, exc)
        raise HTTPException(422, "Could not render that passage for comparison") from exc
    return {"before": base64.b64encode(before).decode(), "after": base64.b64encode(after).decode(), "start": start}


BATCH_PREVIEW_AGE = 2 * 60 * 60


class BatchAudition(Settings):
    candidate_repairs: list[Repair] = Field(default_factory=list, max_length=128)
    bypass_repair_ids: list[str] = Field(default_factory=list, max_length=128)


def prune_batch_auditions(root: Path) -> None:
    entries = []
    for path in root.iterdir():
        try:
            if path.name != UUID(path.name).hex or path.is_symlink() or not path.is_dir():
                continue
            entries.append((path.stat().st_mtime, path))
        except (OSError, ValueError):
            continue
    for index, (modified, path) in enumerate(sorted(entries, reverse=True)):
        if index >= 5 or time() - modified > BATCH_PREVIEW_AGE:
            shutil.rmtree(path, ignore_errors=True)


@router.post("/api/albums/repair-batch-audition")
def repair_batch_audition(settings: BatchAudition):
    if settings.source != "raw" or settings.audition_track not in settings.tracks:
        raise HTTPException(400, "Choose a raw track to audition")
    if bool(settings.candidate_repairs) == bool(settings.bypass_repair_ids):
        raise HTTPException(422, "Choose proposed or already applied repairs to compare")
    album = resolve_album(settings.location, settings.artist_folder, settings.album_folder)
    track = selected_tracks(album, settings.source, [settings.audition_track])[0]
    effective = effective_track_settings(settings, settings.track_overrides.get(track.name, {}))
    bypass = set(settings.bypass_repair_ids)
    audition_only = bool(settings.candidate_repairs) and not effective.cleanup_enabled
    if settings.candidate_repairs:
        if any(not fix.enabled or fix.amount <= 0 or fix.end is None for fix in settings.candidate_repairs):
            raise HTTPException(422, "Invalid suggested repair")
        try:
            combined = Settings.model_validate({**effective.model_dump(), "repairs": [*effective.repairs, *settings.candidate_repairs]})
        except ValueError as exc:
            raise HTTPException(422, "Invalid suggested repairs") from exc
        bypass = {fix.id for fix in settings.candidate_repairs}
        if audition_only:
            # For an individual suggestion, leave existing cleanup bypassed in both versions.
            staged = resolved_settings(combined.model_copy(update={"cleanup_enabled": True}))["repair_chain"]
        else:
            effective = combined
    data = resolved_settings(effective)
    plan = data["repair_chain"]
    if audition_only and plan and staged:
        plan["fixes"] = [fix for fix in staged["fixes"] if fix["id"] in bypass]
    if not plan or not bypass <= {fix["id"] for fix in plan["fixes"]}:
        raise HTTPException(422, "Enable the repairs before comparing the batch")
    if not compare_slots.acquire(blocking=False):
        raise HTTPException(429, "Other comparisons are rendering. Try again in a moment.")
    root = PREVIEWS_DIR / "album-comparisons"
    token = uuid4().hex
    try:
        root.mkdir(parents=True, exist_ok=True)
        prune_batch_auditions(root)
        with tempfile.TemporaryDirectory(prefix=".batch-", dir=root) as temporary:
            pair = Path(temporary) / "preview"
            pair.mkdir()
            workflow.compare_song_repairs(track, data, bypass, pair)
            pair.rename(root / token)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        logger.warning("Batch repair audition failed for %s: %s", track.name, exc)
        raise HTTPException(422, "Could not render a full-song comparison") from exc
    finally:
        compare_slots.release()
    return {"before": f"/api/albums/repair-batch-audition/{token}/before",
            "after": f"/api/albums/repair-batch-audition/{token}/after", "count": len(bypass)}


@router.get("/api/albums/repair-batch-audition/{token}/{side}")
def get_repair_batch_audition(token: UUID, side: Literal["before", "after"]):
    folder = PREVIEWS_DIR / "album-comparisons" / token.hex
    path = folder / f"{side}.wav"
    if not path.is_file() or time() - folder.stat().st_mtime > BATCH_PREVIEW_AGE:
        raise HTTPException(404, "Comparison expired. Render it again.")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "private, no-store"})


@router.post("/api/albums/render-cleanup")
def render_cleanup(settings: Settings):
    if settings.source != "raw" or len(settings.tracks) != 1:
        raise HTTPException(400, "Choose one raw track to render cleanup")
    album = resolve_album(settings.location, settings.artist_folder, settings.album_folder)
    track = selected_tracks(album, settings.source, settings.tracks)[0]
    data = resolve_render(settings)
    if not data["track_repairs"].get(track.name, data["repair_chain"]):
        raise HTTPException(422, "Use repair before EQ for this track first")
    if not compare_slots.acquire(blocking=False):
        raise HTTPException(429, "Other previews are rendering. Try again in a moment.")
    directory = None
    try:
        PREVIEWS_DIR.mkdir(exist_ok=True, parents=True)
        directory = tempfile.TemporaryDirectory(prefix="cleanup-", dir=PREVIEWS_DIR)
        target = Path(directory.name) / "cleanup.wav"
        workflow.render_cleanup_audio(track, target, data)
        return FileResponse(target, media_type="audio/wav", background=BackgroundTask(directory.cleanup))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        if directory:
            directory.cleanup()
        logger.warning("Cleanup render failed for %s: %s", track.name, exc)
        raise HTTPException(422, "Could not render post-cleanup audio") from exc
    finally:
        compare_slots.release()


@router.post("/api/albums/preview")
def preview(settings: Settings):
    if settings.metadata_only:
        raise HTTPException(400, "Tag updates do not change the audio")
    data = resolve_render(settings)
    album = resolve_album(settings.location, settings.artist_folder, settings.album_folder)
    tracks = selected_tracks(album, settings.source, settings.tracks)
    name = settings.audition_track or tracks[0].name
    if name not in settings.tracks:
        raise HTTPException(400, "Include the audition track in the export selection")
    PREVIEWS_DIR.mkdir(exist_ok=True, parents=True)
    directory = tempfile.TemporaryDirectory(prefix="album-", dir=PREVIEWS_DIR)
    try:
        target = Path(directory.name) / f"preview.{settings.audition_format}"
        workflow.render_album(album, tracks, data, audition=(name, target, settings.audition_format))
        mime = "audio/wav" if settings.audition_format == "wav" else "audio/mpeg"
        return FileResponse(target, media_type=mime, background=BackgroundTask(directory.cleanup))
    except Exception as exc:
        directory.cleanup()
        logger.exception("Album audition failed")
        raise HTTPException(422, str(exc)) from exc


def execute(job, album, tracks, data, album_key):
    def report(**changes):
        workflow.record(LIBRARY_DB, job, **{"status": "running", **changes})
    try:
        recover_album_jobs(album_key=album_key, publishing_only=True)
        previous = workflow.published_tracks(LIBRARY_DB, album_key)
        workflow.render_album(album, tracks, data, report, previous=previous)
    except Exception as exc:
        logger.exception("Album render failed")
        workflow.record(LIBRARY_DB, job, status="failed", error=str(exc))


def album_job_inputs(settings: Settings):
    require_writable(settings.location)
    album = resolve_album(settings.location, settings.artist_folder, settings.album_folder)
    if not os.access(album, os.W_OK | os.X_OK):
        raise HTTPException(403, "The album folder is not writable")
    tracks = selected_tracks(album, settings.source, settings.tracks)
    try:
        workflow.require_full_album(album, settings.source, tracks)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    key = json.dumps([settings.location, settings.artist_folder, settings.album_folder])
    return album, tracks, key


@router.on_event("startup")
def recover_album_jobs(*, album_key: str | None = None, publishing_only: bool = False):
    statuses = ("failed",) if publishing_only else ("queued", "running", "failed")
    for job, state in workflow.job_states(LIBRARY_DB, statuses):
        publication = state.get("publication")
        if album_key is not None and state.get("album_key") != album_key:
            continue
        if not publication and (publishing_only or state["status"] == "failed"):
            continue
        try:
            if state.get("work"):
                settings = state["settings"]
                require_writable(settings["location"])
                album = resolve_album(settings["location"], settings["artist_folder"], settings["album_folder"])
                work = workflow.child(album, state["work"])
                if not work.name.startswith(".sonustemper-"):
                    raise ValueError("Invalid export staging folder")
                if publication:
                    if not work.is_dir():
                        raise ValueError("Export recovery folder is missing")
                    workflow.rollback_publication(album, work, publication["entries"])
                if work.exists():
                    shutil.rmtree(work)
            workflow.record(LIBRARY_DB, job, status="failed", stage="Interrupted", publication=None,
                            error="The export stopped before completion. Retry when ready.")
        except Exception:
            logger.exception("Could not recover interrupted album export %s", job)
            workflow.record(LIBRARY_DB, job, status="failed", stage="Recovery needed",
                            error="Could not restore the previous output. Check album storage access.")
            if publishing_only:
                raise RuntimeError("An earlier export still needs recovery") from None


@router.post("/api/albums/preflight")
def preflight(settings: Settings):
    album, tracks, key = album_job_inputs(settings)
    previous = workflow.published_tracks(LIBRARY_DB, key)
    try:
        cover = workflow.delivery_cover(album, settings.cover)
        paths = {path for track in tracks for path in previous.get(track.name, {}).get("files", [])
                 if path.split("/", 1)[0] != "mastered" or settings.source == "raw"}
        if not settings.metadata_only:
            paths.update(workflow.delivery_paths(album, tracks))
            paths.update(f"clean/{track.name}" for track in tracks)
            if settings.source == "raw":
                paths.update(f"mastered/{track.name}" for track in tracks)
        existing = sorted(path for path in paths if workflow.publication_path(album, path).exists())
        taggable = all(previous.get(track.name, {}).get("source") == settings.source and
                       workflow.publication_path(album, f"mp3s/{previous[track.name]['mp3']}").is_file()
                       for track in tracks)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"tracks": len(tracks), "existing": existing, "cover": cover.name if cover else None,
            "can_update_tags": taggable, "will_replace": settings.replace or settings.metadata_only}


@router.post("/api/albums/jobs", status_code=202)
def start_job(settings: Settings):
    album, tracks, key = album_job_inputs(settings)
    try:
        workflow.delivery_cover(album, settings.cover)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    data = settings.model_dump() if settings.metadata_only else resolve_render(settings)
    job = uuid4().hex
    workflow.record(LIBRARY_DB, job, status="queued", stage="Queued", settings=data, album_key=key)
    worker.submit(execute, job, album, tracks, data, key)
    return {"id": job}


@router.get("/api/albums/jobs/{job}")
def job_status(job: str):
    state = workflow.record(LIBRARY_DB, job)
    if not state:
        raise HTTPException(404, "Job not found")
    response = {key: value for key, value in state.items() if key not in {"settings", "work", "publication", "album_key"}}
    if "results" in response:
        response["results"] = [{k: v for k, v in result.items() if k != "output_sha256"}
                               for result in response["results"]]
    return response


@router.get("/albums", include_in_schema=False)
def album_page():
    entry = bundle_root() / "sonustemper-ui/app/static/album-workspace/index.html"
    if not entry.is_file():
        raise HTTPException(503, "Build the album frontend with npm ci and npm run build in frontend/")
    return FileResponse(entry, headers={"Cache-Control": "no-store"})
