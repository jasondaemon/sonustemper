import os
import mimetypes
import re
import shutil
import json
import base64
import unicodedata
import hashlib
import uuid
from urllib.parse import quote
from pathlib import Path
from datetime import datetime
from typing import List
from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sonustemper.tools import bundle_root, is_frozen
from sonustemper.storage import DATA_ROOT
from sonustemper import library_db as library_index

# New tandem UI router (mounted at root).

UI_ROOT = (bundle_root() / "sonustemper-ui" / "app") if is_frozen() else Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(UI_ROOT / "templates"))

def _static_url(path: str) -> str:
    return f"/static/{(path or '').lstrip('/')}"

TEMPLATES.env.globals["static_url"] = _static_url

DATA_DIR = DATA_ROOT
MASTER_IN_DIR = Path(os.getenv("MASTER_IN_DIR", str(DATA_DIR / "mastering" / "in")))
MASTER_OUT_DIR = Path(os.getenv("MASTER_OUT_DIR", str(DATA_DIR / "mastering" / "out")))
TAG_IN_DIR = Path(os.getenv("TAG_IN_DIR", str(DATA_DIR / "tagging" / "in")))
ANALYSIS_IN_DIR = Path(os.getenv("ANALYSIS_IN_DIR", str(DATA_DIR / "analysis" / "in")))
PRESET_DIR = Path(os.getenv("PRESET_DIR", str(DATA_DIR / "user_presets")))
GEN_PRESET_DIR = Path(os.getenv("GEN_PRESET_DIR", str(DATA_DIR / "user_presets")))
USER_VOICING_DIR = PRESET_DIR / "voicings"
USER_PROFILE_DIR = PRESET_DIR / "profiles"
USER_NOISE_DIR = PRESET_DIR / "noise_filters"
STAGING_VOICING_DIR = PRESET_DIR / "voicings"
STAGING_PROFILE_DIR = PRESET_DIR / "profiles"
STAGING_NOISE_DIR = PRESET_DIR / "noise_filters"
ASSET_PRESET_DIR = bundle_root() / "assets" / "presets"
BUILTIN_VOICING_DIR = ASSET_PRESET_DIR / "voicings"

UTILITY_ROOTS = {
    ("mastering", "source"): MASTER_IN_DIR,
    ("mastering", "output"): MASTER_OUT_DIR,
    ("tagging", "library"): TAG_IN_DIR,
    ("analysis", "uploads"): ANALYSIS_IN_DIR,
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
VOICING_ORDER = [
    "universal",
    "airlift",
    "ember",
    "detail",
    "glue",
    "wide",
    "cinematic",
    "punch",
]
PREVIEW_SESSION_COOKIE = "st_preview_session"
APP_VERSION = os.getenv("APP_VERSION", os.getenv("SONUSTEMPER_TAG", "dev"))

router = APIRouter()

def _sanitize_label(value: str, max_len: int = 80) -> str:
    raw = str(value or "").replace("\u00a0", " ")
    raw = "".join(ch for ch in raw if unicodedata.category(ch)[0] != "C")
    cleaned = re.sub(r"[\r\n\t]+", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].strip()
    return cleaned

def _legacy_sanitize_label(value: str) -> str:
    raw = str(value or "").replace("\u00a0", " ")
    cleaned = re.sub(r"[\r\n\t]+", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

def _legacy_corruption_signature(value: str, max_len: int = 80) -> str:
    raw = str(value or "")
    stripped = raw.translate(str.maketrans("", "", "\\rnts"))
    return _sanitize_label(stripped, max_len)

def _norm_for_legacy_compare(value: str, max_len: int = 80) -> str:
    cleaned = _sanitize_label(value, max_len)
    return re.sub(r"[\s_\-]+", "", cleaned).lower()

def _repair_legacy_label(label: str, fallback: str | None, max_len: int = 80) -> str:
    cleaned = _sanitize_label(label, max_len)
    if not fallback:
        return cleaned
    fallback_clean = _sanitize_label(fallback, max_len)
    if not fallback_clean:
        return cleaned
    cleaned_norm = _norm_for_legacy_compare(cleaned, max_len)
    fallback_norm = _norm_for_legacy_compare(fallback_clean, max_len)
    fallback_sig_norm = _norm_for_legacy_compare(
        _legacy_corruption_signature(fallback_clean, max_len),
        max_len,
    )
    if fallback_sig_norm == cleaned_norm:
        return fallback_clean
    return cleaned

def _asset_preset_dirs() -> list[Path]:
    candidates = []
    env_dir = (os.getenv("ASSET_PRESET_DIR") or "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend([
        ASSET_PRESET_DIR,
        bundle_root().parent / "assets" / "presets",
        Path.cwd() / "assets" / "presets",
    ])
    seen = set()
    roots = []
    for root in candidates:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            roots.append(resolved)
    return roots

def _load_builtin_voicings() -> list[dict]:
    items = []
    for root in _asset_preset_dirs():
        voicing_dir = root / "voicings"
        if not voicing_dir.exists():
            continue
        for fp in sorted(voicing_dir.glob("*.json"), key=lambda p: p.name.lower()):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            meta = data.get("meta", {}) if isinstance(data, dict) else {}
            raw_title = meta.get("title") or data.get("name") or fp.stem
            fallback_title = Path(meta.get("source_file") or fp.stem).stem
            title = _repair_legacy_label(raw_title, fallback_title, 80) or fp.stem
            raw_tags = meta.get("tags")
            if not isinstance(raw_tags, list):
                raw_tags = []
            tags = []
            legacy_generated_norm = _norm_for_legacy_compare("Generated from reference audio.", 60)
            for tag in raw_tags:
                if tag is None or not str(tag).strip():
                    continue
                cleaned = _sanitize_label(tag, 60)
                if _norm_for_legacy_compare(cleaned, 60) == legacy_generated_norm:
                    cleaned = "Generated from reference audio."
                if cleaned:
                    tags.append(cleaned)
            chain = data.get("chain") if isinstance(data, dict) else {}
            stereo = chain.get("stereo") if isinstance(chain, dict) else {}
            width = stereo.get("width") if isinstance(stereo, dict) else None
            eq = chain.get("eq") if isinstance(chain, dict) else None
            items.append({
                "id": fp.stem,
                "title": title,
                "tags": tags,
                "width": width,
                "eq": eq if isinstance(eq, list) else None,
                "origin": "builtin",
            })
    return items


def _version_label() -> str:
    ver = (APP_VERSION or "dev").strip()
    if not ver:
        ver = "dev"
    if ver.lower().startswith("v"):
        return ver
    return f"v{ver}"


def _page_context(request: Request, **extra) -> dict:
    ctx = {
        "request": request,
        "app_version_label": _version_label(),
    }
    ctx.update(extra)
    return ctx


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


@router.get("/files")
async def files(request: Request, util: str = "mastering"):
    return RedirectResponse(url=f"/library?util={util}")


@router.get("/library", response_class=HTMLResponse)
async def library_manager(request: Request, util: str = "mastering"):
    util = util if util in ("mastering", "tagging", "presets", "analysis") else "mastering"
    return TEMPLATES.TemplateResponse(
        "pages/library_manager.html",
        _page_context(request, util=util, current_page="library"),
    )


@router.get("/", response_class=HTMLResponse)
async def starter(request: Request):
    return TEMPLATES.TemplateResponse(
        "pages/starter.html",
        _page_context(request, current_page=""),
    )


@router.get("/mastering", response_class=HTMLResponse)
async def mastering_page(request: Request):
    response = TEMPLATES.TemplateResponse(
        "pages/mastering.html",
        _page_context(
            request,
            show_sidebar=False,
            current_page="mastering",
            voicing_seed=_load_builtin_voicings(),
        ),
    )
    if not request.cookies.get(PREVIEW_SESSION_COOKIE):
        response.set_cookie(
            PREVIEW_SESSION_COOKIE,
            uuid.uuid4().hex,
            httponly=True,
            samesite="lax",
        )
    return response


@router.get("/tagging", response_class=HTMLResponse)
async def tagging_page(request: Request):
    return TEMPLATES.TemplateResponse(
        "pages/tagging.html",
        _page_context(request, current_page="tagging"),
    )


@router.get("/presets", response_class=HTMLResponse)
async def presets_page(request: Request):
    return TEMPLATES.TemplateResponse(
        "pages/presets.html",
        _page_context(request, current_page="presets"),
    )


@router.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request):
    return TEMPLATES.TemplateResponse(
        "pages/docs.html",
        _page_context(request, current_page="docs"),
    )

@router.get("/compare", response_class=HTMLResponse)
async def compare_page(request: Request):
    return TEMPLATES.TemplateResponse(
        "pages/compare.html",
        _page_context(request, current_page="compare"),
    )


@router.get("/analyze", response_class=HTMLResponse)
async def analyze_page(request: Request):
    params = request.url.query
    target = "/noise_removal"
    if params:
        target = f"{target}?{params}"
    return RedirectResponse(target, status_code=307)


@router.get("/noise_removal", response_class=HTMLResponse)
async def noise_removal_page(request: Request):
    wide = request.query_params.get("wide") == "1"
    return TEMPLATES.TemplateResponse(
        "pages/noise_removal.html",
        _page_context(request, current_page="analyze", wide=wide),
    )

@router.get("/ai", response_class=HTMLResponse)
async def ai_toolkit_page(request: Request):
    return TEMPLATES.TemplateResponse(
        "pages/ai_toolkit.html",
        _page_context(request, current_page="ai"),
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


def _list_mastering_runs(only_mp3: bool, q: str, limit: int, context: str = "") -> list[dict]:
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
        action = None
        clickable = True
        if context in ("", "mastering", "files"):
            target = "#outputPaneWrap" if context in ("", "mastering") else "#fileDetailPane"
            action = {
                "hx_get": f"/partials/master_output?song={quote(d.name)}",
                "hx_target": target,
                "hx_swap": "innerHTML",
            }
        items.append(
            {
                "id": d.name,
                "title": title,
                "subtitle": "Mastering Run",
                "kind": "mastering_run",
                "badges": badges,
                "action": action,
                "clickable": clickable,
                "mtime": d.stat().st_mtime if d.exists() else 0,
            }
        )
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["title"].lower()]
    items.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return items[:limit]


def _list_mastering_sources(q: str, limit: int, context: str = "") -> list[dict]:
    if not MASTER_IN_DIR.exists():
        return []
    items = []
    for fp in sorted(MASTER_IN_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not fp.is_file() or fp.suffix.lower() not in AUDIO_EXTS:
            continue
        title = _base_title(fp.stem).replace("_", " ").strip() or fp.stem
        action = None
        if context == "files":
            action = {
                "hx_get": f"/partials/file_detail?utility=mastering&section=source&rel={quote(fp.name)}",
                "hx_target": "#fileDetailPane",
                "hx_swap": "innerHTML",
            }
        items.append(
            {
                "id": fp.name,
                "title": title,
                "subtitle": "Source File",
                "kind": "source",
                "badges": [{"key": "format", "label": "Source", "title": "Source file"}],
                "action": action,
                "clickable": context in ("files", "ai"),
                "meta": {"rel": fp.name},
            }
        )
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["title"].lower()]
    return items[:limit]


def _list_mastering_outputs(q: str, limit: int, context: str = "") -> list[dict]:
    if not MASTER_OUT_DIR.exists():
        return []
    items = []
    for d in MASTER_OUT_DIR.iterdir():
        if not d.is_dir():
            continue
        run_mtime = d.stat().st_mtime if d.exists() else 0
        outputs = _run_outputs(d.name)
        for out in outputs:
            stem = out.get("name") or ""
            display_title = out.get("display_title") or stem or d.name
            badges = out.get("badges") or []
            has_source_badge = any(
                (badge.get("label") or "").strip().lower() == "source"
                or (badge.get("title") or "").strip().lower() == "source"
                for badge in badges
                if isinstance(badge, dict)
            )
            same_as_run = stem.strip().lower() == d.name.strip().lower()
            is_source = bool(has_source_badge or same_as_run or (not badges and out.get("metrics") is None))
            meta = {"song": d.name, "out": stem, "solo": is_source}
            items.append(
                {
                    "id": f"{d.name}::{stem}",
                    "title": display_title,
                    "subtitle": f"Run {d.name}",
                    "kind": "mastering_output",
                    "badges": badges,
                    "action": None,
                    "clickable": True,
                    "meta": meta,
                    "mtime": run_mtime,
                }
            )
    if q:
        ql = q.lower()
        items = [
            i for i in items
            if ql in i["title"].lower()
            or ql in (i.get("meta", {}).get("out") or "").lower()
            or ql in (i.get("meta", {}).get("song") or "").lower()
        ]
    items.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return items[:limit]


def _make_tagger_id(root_key: str, relpath: str, size: int, mtime: float) -> str:
    raw = f"{root_key}:{relpath}:{size}:{mtime}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _list_tagging_mp3(q: str, limit: int, context: str = "", scope: str = "tag") -> list[dict]:
    scope = (scope or "tag").lower()
    if scope not in {"tag", "out", "all"}:
        scope = "tag"
    roots: list[tuple[str, Path]] = []
    if scope in {"tag", "all"}:
        roots.append(("tag", TAG_IN_DIR))
    if scope in {"out", "all"}:
        roots.append(("out", MASTER_OUT_DIR))
    items = []
    for root_key, root_dir in roots:
        if not root_dir.exists():
            continue
        for fp in sorted(root_dir.rglob("*.mp3"), key=lambda p: p.name.lower()):
            if not fp.is_file():
                continue
            rel = str(fp.relative_to(root_dir))
            stat = fp.stat()
            tagger_id = _make_tagger_id(root_key, rel, stat.st_size, stat.st_mtime)
            title = _base_title(fp.stem)
            if title:
                title = title.replace("_", " ").strip() or title
            badges = _parse_variant_tags(fp.name)
            if not badges:
                label = "Mastered" if root_key == "out" else "Imported"
                badges = [{"key": "format", "label": label, "title": label}]
            subtitle = "Mastered MP3" if root_key == "out" else "Imported MP3"
            items.append(
                {
                    "id": tagger_id,
                    "title": title,
                    "subtitle": subtitle,
                    "kind": "mp3",
                    "badges": badges,
                    "action": None,
                    "clickable": context == "tagging",
                    "meta": {
                        "root": root_key,
                        "basename": fp.name,
                        "relpath": rel,
                        "full_name": rel,
                    },
                }
            )
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["title"].lower()]
    return items[:limit]


def _list_analysis_imports(q: str, limit: int, context: str = "") -> list[dict]:
    if not ANALYSIS_IN_DIR.exists():
        return []
    items = []
    for fp in sorted(ANALYSIS_IN_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not fp.is_file() or fp.suffix.lower() not in AUDIO_EXTS:
            continue
        title = _base_title(fp.stem)
        if title:
            title = title.replace("_", " ").strip() or title
        badges = _parse_variant_tags(fp.name)
        if not badges:
            badges = [{"key": "format", "label": "Uploaded", "title": "Analyze upload"}]
        action = None
        if context == "files":
            action = {
                "hx_get": f"/partials/file_detail?utility=analysis&section=uploads&rel={quote(fp.name)}",
                "hx_target": "#fileDetailPane",
                "hx_swap": "innerHTML",
            }
        items.append(
            {
                "id": fp.name,
                "title": title,
                "subtitle": "Analyze Upload",
                "kind": "import",
                "badges": badges,
                "action": action,
                "clickable": context in ("analyze", "compare", "files", "ai"),
                "meta": {"rel": fp.name},
            }
        )
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["title"].lower()]
    return items[:limit]


def _list_tagging_uploads(q: str, limit: int, context: str = "") -> list[dict]:
    if not TAG_IN_DIR.exists():
        return []
    items = []
    for fp in sorted(TAG_IN_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not fp.is_file() or fp.suffix.lower() not in AUDIO_EXTS:
            continue
        title = _base_title(fp.stem)
        if title:
            title = title.replace("_", " ").strip() or title
        badges = _parse_variant_tags(fp.name)
        if not badges:
            badges = [{"key": "format", "label": "Tagged", "title": "Tagging upload"}]
        action = None
        if context == "files":
            action = {
                "hx_get": f"/partials/file_detail?utility=tagging&section=library&rel={quote(fp.name)}",
                "hx_target": "#fileDetailPane",
                "hx_swap": "innerHTML",
            }
        items.append(
            {
                "id": fp.name,
                "title": title,
                "subtitle": "Tagging Upload",
                "kind": "tagging_upload",
                "badges": badges,
                "action": action,
                "clickable": context == "files",
                "meta": {"rel": fp.name},
            }
        )
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["title"].lower()]
    return items[:limit]


@router.get("/partials/library_list", response_class=HTMLResponse)
async def library_list(request: Request, view: str, q: str = "", limit: int = 200):
    view = (view or "").strip().lower()
    context = (request.query_params.get("context") or "").strip().lower()
    scope = (request.query_params.get("scope") or "").strip().lower()
    limit = max(1, min(limit, 1000))
    items: list[dict] = []

    groups = None
    total_count = None

    if view == "mastering_runs":
        items = _list_mastering_runs(False, q, limit, context)
    elif view == "mastering_runs_with_mp3":
        items = _list_mastering_runs(True, q, limit, context)
    elif view == "mastering_sources":
        items = _list_mastering_sources(q, limit, context)
    elif view == "mastering_outputs":
        items = _list_mastering_outputs(q, limit, context)
    elif view == "tagging_mp3":
        items = _list_tagging_mp3(q, limit, context, scope)
    elif view == "tagging_uploads":
        items = _list_tagging_uploads(q, limit, context)
    elif view == "analysis_imports":
        items = _list_analysis_imports(q, limit, context)
    elif view == "presets_user":
        items = _list_presets("user", q, limit, context)
    elif view == "presets_user_profiles":
        items = _list_presets("user", q, limit, context, "profile")
        groups = _group_profile_items(items)
        total_count = sum(len(group["items"]) for group in groups)
    elif view == "presets_user_voicings":
        items = _list_presets("user", q, limit, context, "voicing")
    elif view == "presets_user_noise":
        items = _list_presets("user", q, limit, context, "noise_filter")
    elif view == "presets_generated":
        items = _list_presets("staging", q, limit, context)
    elif view == "presets_staging":
        items = _list_presets("staging", q, limit, context)
    elif view == "presets_staging_profiles":
        items = _list_presets("staging", q, limit, context, "profile")
        groups = _group_profile_items(items)
        total_count = sum(len(group["items"]) for group in groups)
    elif view == "presets_staging_voicings":
        items = _list_presets("staging", q, limit, context, "voicing")
    elif view == "presets_staging_noise":
        items = _list_presets("staging", q, limit, context, "noise_filter")
    elif view == "presets_all":
        items = _list_presets("all", q, limit, context)
    elif view == "voicings":
        items = _list_voicings(q, limit, context)
    elif view == "analysis_combo":
        runs = _list_mastering_runs(False, q, limit, context)
        mp3s = _list_tagging_mp3(q, limit, context, "all")
        items = (runs + mp3s)[:limit]
    else:
        raise HTTPException(status_code=400, detail="invalid_view")

    return TEMPLATES.TemplateResponse(
        "partials/library_list.html",
        {"request": request, "items": items, "groups": groups, "total_count": total_count},
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


def _preset_meta_from_file(fp: Path) -> dict:
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        kind = None
        meta_kind = meta.get("kind")
        if isinstance(meta_kind, str) and meta_kind.strip():
            kind = meta_kind.strip().lower()
        if not kind:
            keys = set(data.keys())
            profile_hints = {"lufs", "tp", "limiter", "compressor", "loudness", "target_lufs", "target_tp"}
            if keys & profile_hints:
                kind = "profile"
            elif "eq" in keys or "width" in keys or "stereo" in keys:
                kind = "voicing"
        tags = meta.get("tags")
        if not isinstance(tags, list):
            tags = []
        cleaned_tags = []
        legacy_generated_norm = _norm_for_legacy_compare("Generated from reference audio.", 60)
        for tag in tags:
            if tag is None or not str(tag).strip():
                continue
            cleaned = _sanitize_label(tag, 60)
            if _norm_for_legacy_compare(cleaned, 60) == legacy_generated_norm:
                cleaned = "Generated from reference audio."
            if cleaned:
                cleaned_tags.append(cleaned)
        tags = cleaned_tags
        chain = data.get("chain") if isinstance(data, dict) else None
        stereo = chain.get("stereo") if isinstance(chain, dict) else None
        dynamics = chain.get("dynamics") if isinstance(chain, dict) else None
        width = None
        eq = None
        if isinstance(stereo, dict) and "width" in stereo:
            width = stereo.get("width")
        if isinstance(chain, dict) and isinstance(chain.get("eq"), list):
            eq = chain.get("eq")
        elif isinstance(data.get("eq"), list):
            eq = data.get("eq")
        lufs = data.get("lufs")
        if lufs is None:
            lufs = data.get("target_lufs")
        tp = data.get("tpp")
        if tp is None:
            tp = data.get("tp")
        if tp is None:
            tp = data.get("target_tp")
        name = data.get("name")
        voicing_id = data.get("id")
        raw_title = meta.get("title") or name or voicing_id or fp.stem
        fallback_title = Path(meta.get("source_file") or fp.stem).stem
        title = _repair_legacy_label(raw_title, fallback_title, 80) or fp.stem
        return {
            "title": title,
            "name": name,
            "id": voicing_id,
            "source_file": meta.get("source_file"),
            "created_at": meta.get("created_at"),
            "source": meta.get("source"),
            "kind": kind,
            "tags": tags,
            "eq": eq,
            "width": width,
            "dynamics": dynamics if isinstance(dynamics, dict) else None,
            "stereo": stereo if isinstance(stereo, dict) else None,
            "lufs": lufs,
            "tp": tp,
            "category": data.get("category"),
            "order": data.get("order"),
        }
    except Exception:
        return {"title": fp.stem}


def _normalize_preset_kind(value: str | None) -> str | None:
    if value is None:
        return None
    key = value.strip().lower()
    if key in {"noise", "noise_filter", "noise_preset", "noise-preset"}:
        return "noise_filter"
    return key


def _list_presets(kind: str, q: str, limit: int, context: str = "", meta_kind: str | None = None) -> list[dict]:
    kind = (kind or "user").lower()
    meta_kind = _normalize_preset_kind(meta_kind)
    roots = []
    if kind in {"user", "all"}:
        if meta_kind in (None, "voicing"):
            roots.append(("user", USER_VOICING_DIR, "voicing"))
        if meta_kind in (None, "profile"):
            roots.append(("user", USER_PROFILE_DIR, "profile"))
        if meta_kind in (None, "noise_filter"):
            roots.append(("user", USER_NOISE_DIR, "noise_filter"))
        roots.append(("user", PRESET_DIR, None))
    if kind in {"generated", "gen", "staging", "all"}:
        if meta_kind in (None, "voicing"):
            roots.append(("staging", STAGING_VOICING_DIR, "voicing"))
        if meta_kind in (None, "profile"):
            roots.append(("staging", STAGING_PROFILE_DIR, "profile"))
        if meta_kind in (None, "noise_filter"):
            roots.append(("staging", STAGING_NOISE_DIR, "noise_filter"))
        roots.append(("staging", GEN_PRESET_DIR, None))
    items = []
    for label, root, default_kind in roots:
        if not root.exists():
            continue
        for fp in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
            if not fp.is_file():
                continue
            meta = _preset_meta_from_file(fp)
            effective_kind = _normalize_preset_kind(meta.get("kind") or default_kind or "profile")
            if meta_kind and effective_kind != meta_kind:
                continue
            if effective_kind == "voicing":
                item_id = meta.get("id") or meta.get("name") or fp.stem
            else:
                item_id = meta.get("name") or meta.get("id") or fp.stem
            raw_title = meta.get("title") or item_id or fp.stem
            fallback_title = Path(meta.get("source_file") or fp.stem).stem.replace("_", " ")
            title = _repair_legacy_label(raw_title, fallback_title, 80).replace("_", " ").strip() or fp.stem
            if effective_kind == "voicing":
                kind_label = "Voicing"
            elif effective_kind == "noise_filter":
                kind_label = "Noise Preset"
            else:
                kind_label = "Profile"
            created = meta.get("created_at")
            subtitle = f"Created {created}" if created else ""
            source = meta.get("source") or ("user" if label == "user" else "generated")
            badges = [
                {
                    "key": "format",
                    "label": source.title() if isinstance(source, str) else "User",
                    "title": f"Source: {source}" if source else "Source",
                }
            ]
            if effective_kind:
                badges.append({
                    "key": effective_kind,
                    "label": kind_label,
                    "title": f"Type: {kind_label}",
                })
            action = None
            if context == "files":
                section = "user" if label == "user" else "generated"
                action = {
                    "hx_get": f"/partials/file_detail?utility=presets&section={section}&rel={quote(fp.name)}",
                    "hx_target": "#fileDetailPane",
                    "hx_swap": "innerHTML",
                }
            items.append(
                {
                    "id": item_id,
                    "title": title,
                    "subtitle": subtitle,
                    "kind": "preset",
                    "badges": badges,
                    "action": action,
                    "clickable": context in ("presets", "files"),
                    "meta": {
                        "name": meta.get("name") or fp.stem,
                        "id": meta.get("id") or fp.stem,
                        "filename": fp.name,
                        "title": meta.get("title") or fp.stem,
                        "source_file": meta.get("source_file"),
                        "created_at": meta.get("created_at"),
                        "source": meta.get("source"),
                        "kind": effective_kind,
                        "origin": label,
                        "tags": meta.get("tags") or [],
                        "eq": meta.get("eq"),
                        "width": meta.get("width"),
                        "dynamics": meta.get("dynamics"),
                        "stereo": meta.get("stereo"),
                        "lufs": meta.get("lufs"),
                        "tp": meta.get("tp"),
                        "category": meta.get("category"),
                    },
                }
            )
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["title"].lower()]
    return items[:limit]


def _group_profile_items(items: list[dict]) -> list[dict]:
    category_order = [
        "Custom Profiles",
        "Film / TV / Gaming",
        "Online Streaming",
        "Platform Targets",
        "Manual",
    ]
    grouped: dict[str, list[dict]] = {}
    for item in items:
        meta = item.get("meta") or {}
        category = meta.get("category")
        if not category:
            if meta.get("manual"):
                category = "Manual"
            else:
                category = "Custom Profiles"
        grouped.setdefault(category, []).append(item)

    def _category_rank(name: str) -> tuple[int, str]:
        idx = category_order.index(name) if name in category_order else 9999
        return idx, name

    groups = []
    for category in sorted(grouped.keys(), key=_category_rank):
        group_items = grouped[category]
        group_items.sort(key=lambda i: (
            i.get("meta", {}).get("order") if isinstance(i.get("meta", {}).get("order"), (int, float)) else 9999,
            i.get("title", "")
        ))
        groups.append({"title": category, "items": group_items})
    return groups


def _list_voicings(q: str, limit: int, context: str = "") -> list[dict]:
    items = []
    ordered = [key for key in VOICING_ORDER if key in VOICING_TITLE_MAP]
    for key in sorted(VOICING_TITLE_MAP.keys()):
        if key not in ordered:
            ordered.append(key)
    for slug in ordered:
        title = VOICING_TITLE_MAP.get(slug, f"Voicing: {slug.title()}")
        display = title.replace("Voicing: ", "").strip()
        items.append(
            {
                "id": slug,
                "title": display,
                "subtitle": "Built-in Voicing",
                "kind": "voicing",
                "badges": [
                    {
                        "key": "voicing",
                        "label": f"V: {display}",
                        "title": f"Built-in voicing: {display}",
                    }
                ],
                "action": None,
                "clickable": context == "presets",
                "meta": {
                    "slug": slug,
                    "title": display,
                    "kind": "voicing",
                },
            }
        )
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["title"].lower()]
    return items[:limit]


def _find_master_input(song: str) -> Path | None:
    try:
        for fp in MASTER_IN_DIR.iterdir():
            if fp.is_file() and fp.stem == song:
                return fp
    except Exception:
        return None
    return None


def _register_master_versions(song: str, items: list[dict]) -> None:
    if not song or not items:
        return
    source_path = _find_master_input(song)
    if not source_path:
        return
    source_rel = f"in/{source_path.name}"
    song_entry = library_index.upsert_song_for_source(
        source_rel,
        source_path.stem,
        None,
        source_path.suffix.lower().lstrip("."),
        {},
        False,
    )
    for item in items:
        primary_rel = item.get("primary_rel") or ""
        if not primary_rel:
            continue
        rel = f"out/{primary_rel}"
        summary = {}
        for badge in item.get("badges") or []:
            if badge.get("type") == "voicing" and not summary.get("voicing"):
                title = badge.get("title") or badge.get("label") or ""
                summary["voicing"] = title.replace("Voicing: ", "").strip() or badge.get("label")
            if badge.get("type") == "preset" and not summary.get("loudness_profile"):
                summary["loudness_profile"] = badge.get("label") or badge.get("title")
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        try:
            fmt = Path(rel).suffix.lower().lstrip(".") or None
            library_index.create_version_with_renditions(
                song_entry.get("song_id"),
                "master",
                "Master",
                item.get("display_title") or item.get("name") or "Master",
                summary,
                metrics,
                [{"format": fmt, "rel": rel}],
            )
        except Exception:
            continue


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
        primary_path = None
        for ext in pref:
            fp = base / f"{stem}{ext}"
            if not fp.exists():
                continue
            url = f"/out/{song}/{fp.name}"
            downloads.append({"label": audio_exts[ext], "url": url})
            if not primary:
                primary = url
                primary_path = fp
        m = _load_metrics(base / f"{stem}.metrics.json") or _load_metrics(base / "metrics.json")
        display_title = _base_title(stem)
        badges = _parse_badges(stem)
        primary_rel = f"{song}/{primary_path.name}" if primary_path else ""
        primary_size = primary_path.stat().st_size if primary_path else None
        primary_mtime = primary_path.stat().st_mtime if primary_path else None
        items.append({
            "name": stem,
            "display_title": display_title,
            "primary": primary,
            "primary_rel": primary_rel,
            "primary_size": primary_size,
            "primary_mtime": primary_mtime,
            "downloads": downloads,
            "metrics": m,
            "metric_pills": _metric_pills(m),
            "badges": badges,
        })
    _register_master_versions(song, items)
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


def _detail_title(target: Path, utility: str, section: str) -> dict:
    title = target.name
    subtitle = "File"
    if utility == "mastering" and section == "source":
        title = _base_title(target.stem).replace("_", " ").strip() or target.name
        subtitle = "Source File"
    elif utility == "analysis" and section == "uploads":
        title = _base_title(target.stem).replace("_", " ").strip() or target.name
        subtitle = "Analyze Upload"
    elif utility == "tagging" and section == "library":
        title = _base_title(target.stem).replace("_", " ").strip() or target.name
        subtitle = "Tagging Upload"
    elif utility == "presets":
        meta = _preset_meta_from_file(target)
        title = (meta.get("title") or target.stem).replace("_", " ").strip() or target.name
        kind = (meta.get("kind") or "preset").title()
        subtitle = f"User {kind}"
    return {"title": title, "subtitle": subtitle}


@router.get("/partials/file_detail", response_class=HTMLResponse)
async def file_detail(request: Request, utility: str, section: str, rel: str):
    root = _util_root(utility, section)
    target = _safe_rel(root, rel)
    if not target.exists() or target.is_dir():
        return TEMPLATES.TemplateResponse(
            "partials/file_detail_empty.html",
            {"request": request},
        )
    info = _detail_title(target, utility, section)
    stat = target.stat()
    is_audio = target.suffix.lower() in AUDIO_EXTS
    return TEMPLATES.TemplateResponse(
        "partials/file_detail.html",
        {
            "request": request,
            "utility": utility,
            "section": section,
            "rel": str(rel),
            "filename": target.name,
            "title": info["title"],
            "subtitle": info["subtitle"],
            "size": _human_size(stat.st_size),
            "mtime": _fmt_mtime(stat.st_mtime),
            "is_audio": is_audio,
        },
    )


def _make_file_item(
    title: str,
    subtitle: str,
    rel: str,
    size: int | None,
    mtime: float | None,
    downloads: list[dict],
    badges: list[dict] | None = None,
    metric_pills: list[dict] | None = None,
) -> dict:
    return {
        "title": title,
        "subtitle": subtitle,
        "rel": rel,
        "size": _human_size(size),
        "mtime": _fmt_mtime(mtime),
        "downloads": downloads,
        "badges": badges or [],
        "metric_pills": metric_pills or [],
    }


def _list_processed_outputs_groups(q: str) -> list[dict]:
    groups = []
    if not MASTER_OUT_DIR.exists():
        return groups
    pref = [".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav"]
    for run_dir in sorted(MASTER_OUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        if not run_dir.is_dir():
            continue
        files = [p for p in run_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
        stems = sorted(set(p.stem for p in files))
        items = []
        for stem in stems:
            stem_files = [p for p in files if p.stem == stem]
            primary_path = None
            downloads = []
            for ext in pref:
                fp = run_dir / f"{stem}{ext}"
                if not fp.exists():
                    continue
                label = ext.lstrip(".").upper()
                rel = f"{run_dir.name}/{fp.name}"
                downloads.append({"label": label, "url": f"/download?utility=mastering&section=output&rel={quote(rel)}"})
                if not primary_path:
                    primary_path = fp
            if not downloads:
                continue
            metrics = _load_metrics(run_dir / f"{stem}.metrics.json") or _load_metrics(run_dir / "metrics.json")
            metric_pills = _metric_pills(metrics)
            title = _base_title(stem).replace("_", " ").strip() or stem
            badges = _parse_badges(stem)
            rel = f"{run_dir.name}/{primary_path.name}" if primary_path else ""
            size = primary_path.stat().st_size if primary_path else None
            mtime = primary_path.stat().st_mtime if primary_path else None
            items.append(
                _make_file_item(
                    title=title,
                    subtitle="Processed Output",
                    rel=rel,
                    size=size,
                    mtime=mtime,
                    downloads=downloads,
                    badges=badges,
                    metric_pills=metric_pills,
                )
            )
        if q:
            ql = q.lower()
            items = [i for i in items if ql in i["title"].lower() or ql in run_dir.name.lower()]
        if items:
            groups.append({"title": run_dir.name, "items": items})
    return groups


def _file_manager_data(category: str, q: str = "") -> dict:
    category = (category or "").strip().lower()
    groups = []
    title = "Song Library"
    util = "mastering"
    section = "source"

    if category == "sources":
        title = "Sources"
        util, section = "mastering", "source"
        items = []
        if MASTER_IN_DIR.exists():
            for fp in sorted(MASTER_IN_DIR.iterdir(), key=lambda p: p.name.lower()):
                if not fp.is_file() or fp.suffix.lower() not in AUDIO_EXTS:
                    continue
                display = _base_title(fp.stem).replace("_", " ").strip() or fp.name
                rel = fp.name
                downloads = [{"label": fp.suffix.lstrip(".").upper(), "url": f"/download?utility=mastering&section=source&rel={quote(rel)}"}]
                items.append(
                    _make_file_item(
                        title=display,
                        subtitle="Source File",
                        rel=rel,
                        size=fp.stat().st_size,
                        mtime=fp.stat().st_mtime,
                        downloads=downloads,
                        badges=[{"key": "format", "label": "Source", "title": "Source file"}],
                    )
                )
        if q:
            ql = q.lower()
            items = [i for i in items if ql in i["title"].lower()]
        groups = [{"title": None, "items": items}]
    elif category == "processed_runs":
        title = "Processed Runs"
        util, section = "mastering", "output"
        groups = _list_processed_outputs_groups(q)
    elif category == "analysis_uploads":
        title = "Analyze Uploads"
        util, section = "analysis", "uploads"
        items = []
        if ANALYSIS_IN_DIR.exists():
            for fp in sorted(ANALYSIS_IN_DIR.iterdir(), key=lambda p: p.name.lower()):
                if not fp.is_file() or fp.suffix.lower() not in AUDIO_EXTS:
                    continue
                display = _base_title(fp.stem).replace("_", " ").strip() or fp.name
                rel = fp.name
                downloads = [{"label": fp.suffix.lstrip(".").upper(), "url": f"/download?utility=analysis&section=uploads&rel={quote(rel)}"}]
                items.append(
                    _make_file_item(
                        title=display,
                        subtitle="Analyze Upload",
                        rel=rel,
                        size=fp.stat().st_size,
                        mtime=fp.stat().st_mtime,
                        downloads=downloads,
                        badges=[{"key": "format", "label": "Uploaded", "title": "Analyze upload"}],
                    )
                )
        if q:
            ql = q.lower()
            items = [i for i in items if ql in i["title"].lower()]
        groups = [{"title": None, "items": items}]
    elif category == "tagging_uploads":
        title = "Tagging Uploads"
        util, section = "tagging", "library"
        items = []
        if TAG_IN_DIR.exists():
            for fp in sorted(TAG_IN_DIR.iterdir(), key=lambda p: p.name.lower()):
                if not fp.is_file() or fp.suffix.lower() not in AUDIO_EXTS:
                    continue
                display = _base_title(fp.stem).replace("_", " ").strip() or fp.name
                rel = fp.name
                downloads = [{"label": fp.suffix.lstrip(".").upper(), "url": f"/download?utility=tagging&section=library&rel={quote(rel)}"}]
                items.append(
                    _make_file_item(
                        title=display,
                        subtitle="Tagging Upload",
                        rel=rel,
                        size=fp.stat().st_size,
                        mtime=fp.stat().st_mtime,
                        downloads=downloads,
                        badges=[{"key": "format", "label": "Tagged", "title": "Tagging upload"}],
                    )
                )
        if q:
            ql = q.lower()
            items = [i for i in items if ql in i["title"].lower()]
        groups = [{"title": None, "items": items}]
    elif category in {"user_voicings", "user_profiles"}:
        title = "User Voicings" if category == "user_voicings" else "User Profiles"
        util, section = "presets", "user"
        want_kind = "voicing" if category == "user_voicings" else "profile"
        items = []
        roots = []
        if want_kind == "voicing":
            roots.append(USER_VOICING_DIR)
        if want_kind == "profile":
            roots.append(USER_PROFILE_DIR)
        roots.append(PRESET_DIR)
        for root in roots:
            if not root.exists():
                continue
            for fp in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
                if not fp.is_file():
                    continue
                meta = _preset_meta_from_file(fp)
                effective_kind = (meta.get("kind") or "profile").lower()
                if effective_kind != want_kind:
                    continue
                display = (meta.get("title") or fp.stem).replace("_", " ").strip() or fp.stem
                rel = str(fp.relative_to(PRESET_DIR))
                downloads = [{"label": "JSON", "url": f"/download?utility=presets&section=user&rel={quote(rel)}"}]
                badges = [
                    {"key": "format", "label": "User", "title": "User preset"},
                    {"key": "profile", "label": want_kind.title(), "title": f"{want_kind.title()} preset"},
                ]
                items.append(
                    _make_file_item(
                        title=display,
                        subtitle=f"User {want_kind.title()}",
                        rel=rel,
                        size=fp.stat().st_size,
                        mtime=fp.stat().st_mtime,
                        downloads=downloads,
                        badges=badges,
                    )
                )
        if q:
            ql = q.lower()
            items = [i for i in items if ql in i["title"].lower()]
        groups = [{"title": None, "items": items}]
    else:
        title = "Song Library"
        util, section = "mastering", "source"
        groups = [{"title": None, "items": []}]

    total_count = sum(len(group["items"]) for group in groups)
    return {
        "title": title,
        "category": category,
        "util": util,
        "section": section,
        "groups": groups,
        "total_count": total_count,
    }


@router.get("/partials/file_manager_list", response_class=HTMLResponse)
async def file_manager_list(request: Request, category: str = "", q: str = ""):
    data = _file_manager_data(category, q)
    return TEMPLATES.TemplateResponse(
        "partials/file_manager_list.html",
        {"request": request, **data},
    )


@router.post("/actions/delete", response_class=HTMLResponse)
async def delete_items(request: Request, util: str = Form(...), section: str = Form(...), delete_all: str = Form(default=""), rels: list[str] = Form(default=[]), context: str = Form(default=""), category: str = Form(default="")):
    util = util if util in ("mastering", "tagging", "presets", "analysis") else "mastering"
    root = _util_root(util, section)
    to_delete = []
    allow_dirs = util == "mastering" and section == "output"
    if delete_all:
        allow_audio = util in ("mastering", "tagging", "analysis")
        allow_json = util == "presets"
        items = _list_dir(root, allow_audio=allow_audio, allow_json=allow_json)
        to_delete = [i["rel"] for i in items if allow_dirs or not i["is_dir"]]
    else:
        to_delete = [r for r in rels if r]
    if not to_delete:
        if context == "file_detail":
            return TEMPLATES.TemplateResponse(
                "partials/file_detail_empty.html",
                {"request": request},
            )
        if context == "file_manager":
            return TEMPLATES.TemplateResponse(
                "partials/file_manager_list.html",
                {"request": request, **_file_manager_data(category)},
            )
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
    if context == "file_detail":
        response = TEMPLATES.TemplateResponse(
            "partials/file_detail_empty.html",
            {"request": request},
        )
        response.headers["HX-Trigger"] = "refreshFileBrowser"
        return response
    if context == "file_manager":
        return TEMPLATES.TemplateResponse(
            "partials/file_manager_list.html",
            {"request": request, **_file_manager_data(category)},
        )
    return _render_sections(request, util)


@router.get("/download")
async def download_file(utility: str, section: str, rel: str):
    root = _util_root(utility, section)
    target = _safe_rel(root, rel)
    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=404, detail="not_found")
    mime, _ = mimetypes.guess_type(target.name)
    return FileResponse(target, media_type=mime or "application/octet-stream", filename=target.name)
