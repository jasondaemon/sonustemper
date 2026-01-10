import os
import mimetypes
import re
import shutil
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
    items = []
    if base.exists() and base.is_dir():
        for f in base.iterdir():
            try:
                if f.is_dir():
                    continue
                ext = f.suffix.lower()
                if ext not in AUDIO_EXTS:
                    continue
                st = f.stat()
                items.append(
                    {
                        "name": f.name,
                        "rel": str(f.relative_to(root)),
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    }
                )
            except Exception:
                continue
    items.sort(key=lambda x: x["name"].lower())
    # sort so WAV/W64 first, then others
    items.sort(key=lambda x: (0 if x["name"].lower().endswith((".wav", ".w64")) else 1, x["name"].lower()))
    return TEMPLATES.TemplateResponse(
        "partials/master_output.html",
        {"request": request, "song": song, "items": items, "human_size": _human_size, "fmt_mtime": _fmt_mtime},
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
