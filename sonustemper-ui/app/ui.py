import os
import mimetypes
import re
import shutil
import json
from urllib.parse import quote
from pathlib import Path
from datetime import datetime
from typing import List
from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates

# New tandem UI router (mounted under /ui/*)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MASTER_IN_DIR = Path(os.getenv("MASTER_IN_DIR", str(DATA_DIR / "mastering" / "in")))
MASTER_OUT_DIR = Path(os.getenv("MASTER_OUT_DIR", str(DATA_DIR / "mastering" / "out")))
TAG_IN_DIR = Path(os.getenv("TAG_IN_DIR", str(DATA_DIR / "tagging" / "in")))
PRESET_DIR = Path(os.getenv("PRESET_DIR", str(DATA_DIR / "presets" / "user")))
GEN_PRESET_DIR = Path(os.getenv("GEN_PRESET_DIR", str(DATA_DIR / "presets" / "generated")))

UTILITY_ROOTS = {
    ("mastering", "source"): MASTER_IN_DIR,
    ("mastering", "output"): MASTER_OUT_DIR,
    ("tagging", "library"): TAG_IN_DIR,
    ("presets", "user"): PRESET_DIR,
    ("presets", "generated"): GEN_PRESET_DIR,
}

AUDIO_EXTS = {".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".ogg"}
PRESET_EXTS = {".json"}
VOICING_TITLE_MAP = {
    "universal": "Voicing: Universal",
    "airlift": "Voicing: Airlift",
    "ember": "Voicing: Ember",
    "detail": "Voicing: Detail",
    "glue": "Voicing: Glue",
    "wide": "Voicing: Wide",
    "cinematic": "Voicing: Cinematic",
    "punch": "Voicing: Punch",
    "warm": "Voicing: Warm",
    "modern": "Voicing: Modern",
    "clean": "Voicing: Clean",
    "rock": "Voicing: Rock",
    "acoustic": "Voicing: Acoustic",
}

router = APIRouter()


def _util_root(util: str, section: str) -> Path:
    root = UTILITY_ROOTS.get((util, section))
    if not root:
        raise HTTPException(status_code=400, detail="invalid_utility")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_rel(root: Path, rel: str) -> Path:
    rel = (rel or "").strip().lstrip("/").replace("\\", "/")
    candidate = (root / rel).resolve()
    if root not in candidate.parents and candidate != root:
        raise HTTPException(status_code=400, detail="invalid_path")
    return candidate


def _human_size(num: int | None) -> str:
    if num is None:
        return "-"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{num} B"
        num /= 1024
    return f"{num:.1f} TB"


def _fmt_mtime(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%m/%d/%Y, %I:%M:%S %p")


def _list_dir(root: Path, allow_audio: bool = False, allow_json: bool = False, prefix: str = "") -> list[dict]:
    base = _safe_rel(root, prefix) if prefix else root
    if not base.exists():
        return []
    items: list[dict] = []
    for entry in base.iterdir():
        try:
            is_dir = entry.is_dir()
            if is_dir:
                st = entry.stat()
                items.append(
                    {
                        "name": entry.name,
                        "rel": str(entry.relative_to(root)),
                        "is_dir": True,
                        "size": None,
                        "mtime": st.st_mtime,
                    }
                )
                continue
            ext = entry.suffix.lower()
            ok = False
            if allow_audio and ext in AUDIO_EXTS:
                ok = True
            if allow_json and ext in PRESET_EXTS:
                ok = True
            if not ok:
                continue
            st = entry.stat()
            items.append(
                {
                    "name": entry.name,
                    "rel": str(entry.relative_to(root)),
                    "is_dir": False,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
        except Exception:
            continue
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return items


def _sections_for(util: str):
    if util == "mastering":
        return [
            {"title": "Source Files", "section": "source", "allow_audio": True, "allow_json": False},
            {"title": "Job Output", "section": "output", "allow_audio": True, "allow_json": False},
        ]
    if util == "tagging":
        return [{"title": "MP3 Library", "section": "library", "allow_audio": True, "allow_json": False}]
    if util == "presets":
        return [
            {"title": "User Presets", "section": "user", "allow_audio": False, "allow_json": True},
            {"title": "Generated Presets", "section": "generated", "allow_audio": False, "allow_json": True},
        ]
    return []


@router.get("/files", response_class=HTMLResponse)
async def files(request: Request, util: str = "mastering"):
    util = util if util in ("mastering", "tagging", "presets", "analysis") else "mastering"
    return TEMPLATES.TemplateResponse(
        "pages/files.html",
        {
            "request": request,
            "util": util,
            "current_page": "files",
        },
    )


@router.get("/", response_class=HTMLResponse)
async def starter(request: Request):
    return TEMPLATES.TemplateResponse(
        "pages/starter.html",
        {
            "request": request,
            "current_page": "",
        },
    )


@router.get("/mastering", response_class=HTMLResponse)
async def mastering_page(request: Request):
    return TEMPLATES.TemplateResponse(
        "pages/mastering.html",
        {
            "request": request,
            "show_sidebar": False,
            "current_page": "mastering",
        },
    )


@router.get("/tagging", response_class=HTMLResponse)
async def tagging_page(request: Request):
    return TEMPLATES.TemplateResponse(
        "pages/tagging.html",
        {
            "request": request,
            "current_page": "tagging",
        },
    )


@router.get("/presets", response_class=HTMLResponse)
async def presets_page(request: Request):
    return TEMPLATES.TemplateResponse(
        "pages/presets.html",
        {
            "request": request,
            "current_page": "presets",
        },
    )


def _render_sections(request: Request, util: str) -> HTMLResponse:
    util = util if util in ("mastering", "tagging", "presets", "analysis") else "mastering"
    sections_meta = _sections_for(util)
    sections = []
    for meta in sections_meta:
        root = _util_root(util, meta["section"])
        items = _list_dir(root, allow_audio=meta["allow_audio"], allow_json=meta["allow_json"])
        sections.append(
            {
                "title": meta["title"],
                "utility": util,
                "section": meta["section"],
                "items": items,
            }
        )
    return TEMPLATES.TemplateResponse(
        "partials/file_sections.html",
        {
            "request": request,
            "util": util,
            "sections": sections,
            "human_size": _human_size,
            "fmt_mtime": _fmt_mtime,
        },
    )


@router.get("/partials/files_sections", response_class=HTMLResponse)
async def files_sections(request: Request, util: str = "mastering"):
    return _render_sections(request, util)


def _recent_runs(limit: int = 12) -> List[dict]:
    if not MASTER_OUT_DIR.exists():
        return []
    items = []
    for d in MASTER_OUT_DIR.iterdir():
        if not d.is_dir():
            continue
        try:
            st = d.stat()
            items.append({"name": d.name, "mtime": st.st_mtime})
        except Exception:
            continue
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


@router.get("/partials/master_prev", response_class=HTMLResponse)
async def master_prev(request: Request):
    runs = _recent_runs()
    return TEMPLATES.TemplateResponse(
        "partials/master_prev.html",
        {"request": request, "runs": runs},
    )


def _list_mastering_runs(only_mp3: bool, q: str, limit: int) -> list[dict]:
    if not MASTER_OUT_DIR.exists():
        return []
    items = []
    for d in MASTER_OUT_DIR.iterdir():
        if not d.is_dir():
            continue
        if only_mp3:
            has_mp3 = any(p.is_file() and p.suffix.lower() == ".mp3" for p in d.iterdir())
            if not has_mp3:
                continue
        rep = _pick_representation_file(d)
        title = _base_title(rep.stem) if rep else d.name
        badges = _parse_variant_tags(rep.name) if rep else []
        items.append(
            {
                "id": d.name,
                "title": title,
                "subtitle": "Mastering Run",
                "kind": "mastering_run",
                "badges": badges,
                "action": {
                    "hx_get": f"/ui/partials/master_output?song={quote(d.name)}",
                    "hx_target": "#outputPaneWrap",
                    "hx_swap": "innerHTML",
                },
                "mtime": d.stat().st_mtime if d.exists() else 0,
            }
        )
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["title"].lower()]
    items.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return items[:limit]


def _list_tagging_mp3(q: str, limit: int) -> list[dict]:
    if not TAG_IN_DIR.exists():
        return []
    items = []
    for fp in sorted(TAG_IN_DIR.rglob("*.mp3"), key=lambda p: p.name.lower()):
        if not fp.is_file():
            continue
        rel = str(fp.relative_to(TAG_IN_DIR))
        title = fp.stem
        items.append(
            {
                "id": rel,
                "title": title,
                "subtitle": "MP3",
                "kind": "mp3",
                "badges": [],
                "action": None,
            }
        )
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["title"].lower()]
    return items[:limit]


@router.get("/partials/library_list", response_class=HTMLResponse)
async def library_list(request: Request, view: str, q: str = "", limit: int = 200):
    view = (view or "").strip().lower()
    limit = max(1, min(limit, 1000))
    items: list[dict] = []

    if view == "mastering_runs":
        items = _list_mastering_runs(False, q, limit)
    elif view == "mastering_runs_with_mp3":
        items = _list_mastering_runs(True, q, limit)
    elif view == "tagging_mp3":
        items = _list_tagging_mp3(q, limit)
    elif view == "analysis_combo":
        runs = _list_mastering_runs(False, q, limit)
        mp3s = _list_tagging_mp3(q, limit)
        items = (runs + mp3s)[:limit]
    else:
        raise HTTPException(status_code=400, detail="invalid_view")

    return TEMPLATES.TemplateResponse(
        "ui/partials/library_list.html",
        {"request": request, "items": items},
    )


def _load_metrics(path: Path) -> dict | None:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _base_title(stem: str) -> str:
    return stem.split("__", 1)[0] if "__" in stem else stem


def _parse_badges(stem: str) -> list[dict]:
    if "__" not in stem:
        return []
    _, suffix = stem.split("__", 1)
    tokens = [t for t in suffix.split("_") if t]
    badges: list[dict] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "V" and (i + 1) < len(tokens):
            vname = tokens[i + 1]
            lbl = f"V_{vname}"
            slug = vname.lower()
            title = VOICING_TITLE_MAP.get(slug, f"Voicing: {vname.replace('_',' ').title()}")
            badges.append({"type": "voicing", "label": lbl, "title": title})
            i += 2
            continue
        if t.startswith("V_") or t == "source":
            lbl = t
            if t == "source":
                title = "Source"
            else:
                slug = t[2:].lower()
                title = VOICING_TITLE_MAP.get(slug, f"Voicing: {slug.replace('_',' ').title()}")
            badges.append({"type": "voicing", "label": lbl, "title": title})
            i += 1
            continue
        if t.startswith("S") and t[1:].isdigit():
            badges.append({"type": "param", "label": t, "title": f"Strength: {t[1:]}"})
            i += 1
            continue
        if t == "LMCustom":
            badges.append({"type": "preset", "label": t, "title": f"Preset: {t}"})
            i += 1
            continue
        if t.startswith("TI-"):
            val = t[3:]
            badges.append({"type": "param", "label": t, "title": f"LUFS: {val}"})
            i += 1
            continue
        if t.startswith("TTP-"):
            badges.append({"type": "param", "label": t, "title": f"True Peak: {t.replace('TTP-','-')} dBTP"})
            i += 1
            continue
        if t.startswith("W") and t[1:]:
            probe = t[1:].replace(".", "", 1).replace("-", "", 1)
            if probe.isdigit():
                badges.append({"type": "param", "label": t, "title": f"Width: {t[1:]}"})
                i += 1
                continue
        if t.startswith("GR") and t[2:]:
            badges.append({"type": "param", "label": t, "title": f"Gain Reduction: {t[2:]}"})
            i += 1
            continue
        if t.upper().startswith("WAV") and t[3:-1].isdigit():
            rate = t[3:].rstrip("kK")
            bit = None
            if (i + 1) < len(tokens) and tokens[i + 1].isdigit():
                bit = tokens[i + 1]
                i += 1
            lbl = f"{rate}k/{bit}" if bit else f"{rate}k"
            title = f"Source Format: {rate} kHz" + (f" / {bit}-bit" if bit else "")
            badges.append({"type": "format", "label": lbl, "title": title})
            i += 1
            continue
        if t == "MP3":
            lbl = "MP3"
            if (i + 1) < len(tokens) and tokens[i + 1].upper().startswith("CBR"):
                br = tokens[i + 1].upper().replace("CBR", "")
                lbl = f"MP3 {br}"
                i += 1
            title = f"Output Format: {lbl} kbps (CBR)" if " " in lbl else f"Output Format: {lbl}"
            badges.append({"type": "format", "label": lbl, "title": title})
            i += 1
            continue
        if t.upper().startswith("AAC"):
            lbl = "AAC"
            nxt = tokens[i + 1] if (i + 1) < len(tokens) else ""
            if "_" in t:
                parts = t.split("_", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    lbl = f"AAC {parts[1]}"
            elif nxt.isdigit():
                lbl = f"AAC {nxt}"
                i += 1
            title = f"Output Format: {lbl}"
            badges.append({"type": "format", "label": lbl, "title": title})
            i += 1
            continue
        i += 1
    return badges


def _metric_pills(metrics: dict | None) -> list[dict]:
    if not metrics:
        return []
    data = metrics
    if isinstance(metrics, dict) and "output" in metrics and isinstance(metrics.get("output"), dict):
        data = metrics["output"]
    if not isinstance(data, dict):
        return []
    pills = []
    for key in sorted(data.keys()):
        val = data.get(key)
        if isinstance(val, (dict, list)):
            continue
        if isinstance(val, float):
            disp = f"{val:.2f}".rstrip("0").rstrip(".")
        else:
            disp = str(val)
        pills.append({"label": key, "value": disp})
    return pills


PROFILE_TITLE_MAP = {
    "apple": "Apple Music",
    "applemusic": "Apple Music",
    "apple_music": "Apple Music",
    "spotify": "Spotify",
    "loud": "Loud",
    "manual": "Manual",
    "custom": "Custom",
}


def _normalize_profile_name(raw: str) -> str | None:
    if not raw:
        return None
    slug = raw.strip().replace("-", "_")
    key = slug.lower()
    if key in PROFILE_TITLE_MAP:
        return PROFILE_TITLE_MAP[key]
    return slug.replace("_", " ").strip().title()


def _parse_float_token(token: str) -> float | None:
    try:
        return float(token)
    except Exception:
        return None


def _parse_variant_tags(filename: str) -> list[dict]:
    stem = Path(filename).stem
    if "__" not in stem:
        return []
    _, suffix = stem.split("__", 1)
    tokens = [t for t in suffix.split("_") if t]
    badges: list[dict] = []
    voicing = None
    profile = None
    strength = None
    width = None
    target_i = None
    target_tp = None
    format_badges = []

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "V" and (i + 1) < len(tokens):
            voicing = tokens[i + 1].replace("_", " ").strip()
            i += 2
            continue
        if t.startswith("V_"):
            voicing = t[2:].replace("_", " ").strip()
            i += 1
            continue
        if t.lower() == "source":
            i += 1
            continue
        if t.startswith("LM"):
            profile = _normalize_profile_name(t[2:])
            i += 1
            continue
        if t.startswith("S") and t[1:].isdigit():
            strength = t[1:]
            i += 1
            continue
        if t.startswith("TI"):
            num = _parse_float_token(t[2:])
            if num is not None:
                target_i = num
                i += 1
                continue
        if t.startswith("TTP"):
            num = _parse_float_token(t[3:])
            if num is not None:
                target_tp = num
                i += 1
                continue
        if t.startswith("WAV"):
            rate = t[3:].lower().replace("k", "")
            label = f"WAV {rate}k" if rate else "WAV"
            format_badges.append({"label": label, "title": f"Output Format: {label}"})
            i += 1
            continue
        if t.startswith("MP3"):
            label = "MP3"
            if "_" in t:
                label = f"MP3 {t.split('_', 1)[1]}".replace("_", " ")
            format_badges.append({"label": label, "title": f"Output Format: {label}"})
            i += 1
            continue
        if t.startswith("AAC"):
            label = "AAC"
            if "_" in t:
                label = f"AAC {t.split('_', 1)[1]}".replace("_", " ")
            format_badges.append({"label": label, "title": f"Output Format: {label}"})
            i += 1
            continue
        if t.startswith("OGG"):
            label = "OGG"
            if "_" in t:
                label = f"OGG {t.split('_', 1)[1]}".replace("_", " ")
            format_badges.append({"label": label, "title": f"Output Format: {label}"})
            i += 1
            continue
        if t.startswith("FLAC"):
            label = "FLAC"
            if "_" in t:
                label = f"FLAC {t.split('_', 1)[1]}".replace("_", " ")
            format_badges.append({"label": label, "title": f"Output Format: {label}"})
            i += 1
            continue
        if t.startswith("W") and not t.startswith("WAV"):
            w_match = re.match(r"^W(-?\d+(?:\.\d+)?)$", t)
            if w_match:
                width = _parse_float_token(w_match.group(1))
                i += 1
                continue
        i += 1

    if voicing:
        badges.append({"key": "voicing", "label": f"V: {voicing.title()}", "title": f"Voicing: {voicing.title()}"})
    if profile:
        badges.append({"key": "profile", "label": f"P: {profile}", "title": f"Normalization Profile: {profile}"})
    if not profile and (target_i is not None or target_tp is not None):
        bits = []
        if target_i is not None:
            bits.append(f"{target_i:g} LUFS")
        if target_tp is not None:
            bits.append(f"{target_tp:g} TP")
        joined = " / ".join(bits)
        if joined:
            badges.append({"key": "profile", "label": f"P: {joined}", "title": f"Normalization Profile: {joined}"})
    if strength:
        badges.append({"key": "param", "label": f"S: {strength}", "title": f"Strength: {strength}"})
    if width is not None:
        badges.append({"key": "param", "label": f"W: {width:g}", "title": f"Width: {width:g}"})
    if target_i is not None:
        badges.append({"key": "param", "label": f"TI: {target_i:g}", "title": f"Target Integrated Loudness: {target_i:g} LUFS"})
    if target_tp is not None:
        badges.append({"key": "param", "label": f"TP: {target_tp:g}", "title": f"True Peak Target: {target_tp:g} dBTP"})
    for fmt in format_badges:
        badges.append({"key": "format", "label": fmt["label"], "title": fmt["title"]})

    return badges


def _pick_representation_file(run_dir: Path) -> Path | None:
    if not run_dir.exists() or not run_dir.is_dir():
        return None
    preferred = [".mp3", ".wav", ".flac"]
    candidates: list[Path] = []
    for p in run_dir.iterdir():
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            candidates.append(p)
    if not candidates:
        return None
    for ext in preferred:
        matches = [p for p in candidates if p.suffix.lower() == ext]
        if matches:
            return sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _run_outputs(song: str) -> list[dict]:
    folder = _util_root("mastering", "output")
    base = _safe_rel(folder, song)
    items: list[dict] = []
    if not base.exists() or not base.is_dir():
        return items
    audio_exts = {".wav": "WAV", ".mp3": "MP3", ".m4a": "M4A", ".aac": "AAC", ".ogg": "OGG", ".flac": "FLAC"}
    files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in audio_exts]
    stems = sorted(set(p.stem for p in files))
    pref = [".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav"]
    for stem in stems:
        downloads = []
        primary = None
        for ext in pref:
            fp = base / f"{stem}{ext}"
            if not fp.exists():
                continue
            url = f"/out/{song}/{fp.name}"
            downloads.append({"label": audio_exts[ext], "url": url})
            if not primary:
                primary = url
        m = _load_metrics(base / f"{stem}.metrics.json") or _load_metrics(base / "metrics.json")
        display_title = _base_title(stem)
        badges = _parse_badges(stem)
        items.append({
            "name": stem,
            "display_title": display_title,
            "primary": primary,
            "downloads": downloads,
            "metrics": m,
            "metric_pills": _metric_pills(m),
            "badges": badges,
        })
    return items


@router.get("/partials/master_output", response_class=HTMLResponse)
async def master_output(request: Request, song: str = ""):
    song = song.strip()
    if not song:
        return TEMPLATES.TemplateResponse(
            "partials/master_output.html",
            {"request": request, "song": None, "items": []},
        )
    root = _util_root("mastering", "output")
    base = _safe_rel(root, song)
    items = _run_outputs(song)
    return TEMPLATES.TemplateResponse(
        "partials/master_output.html",
        {"request": request, "song": song, "items": items},
    )


@router.post("/actions/delete", response_class=HTMLResponse)
async def delete_items(request: Request, util: str = Form(...), section: str = Form(...), delete_all: str = Form(default=""), rels: list[str] = Form(default=[])):
    util = util if util in ("mastering", "tagging", "presets") else "mastering"
    root = _util_root(util, section)
    to_delete = []
    allow_dirs = util == "mastering" and section == "output"
    if delete_all:
        allow_audio = util in ("mastering", "tagging")
        allow_json = util == "presets"
        items = _list_dir(root, allow_audio=allow_audio, allow_json=allow_json)
        to_delete = [i["rel"] for i in items if allow_dirs or not i["is_dir"]]
    else:
        to_delete = [r for r in rels if r]
    if not to_delete:
        return _render_sections(request, util)
    for rel in to_delete:
        try:
            target = _safe_rel(root, rel)
            if not target.exists():
                continue
            # Mastering outputs: delete entire run folder or file + sidecars
            if util == "mastering" and section == "output":
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                    continue
                parent = target.parent
                stem = target.stem
                for f in parent.glob(f"{stem}.*"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                except Exception:
                    pass
                    target.unlink()
                except Exception:
                    pass
            else:
                # All other sections: delete files only
                if target.is_dir():
                    continue
                try:
                    target.unlink()
                except Exception:
                    pass
        except HTTPException:
            raise
        except Exception:
            continue
    return _render_sections(request, util)


@router.get("/download")
async def download_file(utility: str, section: str, rel: str):
    root = _util_root(utility, section)
    target = _safe_rel(root, rel)
    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=404, detail="not_found")
    mime, _ = mimetypes.guess_type(target.name)
    return FileResponse(target, media_type=mime or "application/octet-stream", filename=target.name)
