from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parents[1]


def _platform_dir() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "unknown"


def resolve_tool(name: str) -> str:
    name = name.lower().strip()
    env_var = "FFMPEG_PATH" if name == "ffmpeg" else "FFPROBE_PATH"
    env_val = (os.getenv(env_var) or "").strip()
    if env_val:
        path = Path(env_val)
        if path.exists():
            return str(path)

    if is_frozen():
        plat = _platform_dir()
        exe = f"{name}.exe" if plat == "windows" else name
        candidate = bundle_root() / "vendor" / "ffmpeg" / plat / exe
        if candidate.exists():
            return str(candidate)

    return name
