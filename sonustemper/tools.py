from __future__ import annotations

import os
import platform
import shutil
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

def _arch_dir() -> str:
    if sys.platform != "darwin":
        return _platform_dir()
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    return "unknown"


def resolve_tool(name: str) -> str:
    name = name.lower().strip()
    checked: list[str] = []
    env_var = "SONUSTEMPER_FFMPEG" if name == "ffmpeg" else "SONUSTEMPER_FFPROBE"
    env_val = (os.getenv(env_var) or "").strip()
    if env_val:
        checked.append(f"{env_var}={env_val}")
        path = Path(env_val)
        if path.exists():
            return str(path)

    if is_frozen():
        if sys.platform == "darwin":
            arch = _arch_dir()
            resources = Path(sys.executable).resolve().parent.parent / "Resources"
            frameworks = Path(sys.executable).resolve().parent.parent / "Frameworks"
            candidate = bundle_root() / "vendor" / "ffmpeg" / arch / name
            checked.append(str(candidate))
            if candidate.exists():
                candidate.chmod(0o755)
                return str(candidate)
            candidate = resources / "vendor" / "ffmpeg" / arch / name
            checked.append(str(candidate))
            if candidate.exists():
                candidate.chmod(0o755)
                return str(candidate)
            candidate = frameworks / "vendor" / "ffmpeg" / arch / name
            checked.append(str(candidate))
            if candidate.exists():
                candidate.chmod(0o755)
                return str(candidate)
            raise RuntimeError(f"{name} not bundled; checked: {', '.join(checked)}")
        plat = _platform_dir()
        exe = f"{name}.exe" if plat == "windows" else name
        candidate = bundle_root() / "vendor" / "ffmpeg" / plat / exe
        if candidate.exists():
            return str(candidate)
        checked.append(str(candidate))
        raise RuntimeError(f"{name} not bundled; checked: {', '.join(checked)}")

    env_var = "FFMPEG_PATH" if name == "ffmpeg" else "FFPROBE_PATH"
    env_val = (os.getenv(env_var) or "").strip()
    if env_val:
        checked.append(f"{env_var}={env_val}")
        path = Path(env_val)
        if path.exists():
            return str(path)

    if sys.platform == "darwin":
        arch = _arch_dir()
        dev_candidate = bundle_root() / "sonustemper" / "vendor" / "macos" / arch / name
        checked.append(str(dev_candidate))
        if dev_candidate.exists():
            return str(dev_candidate)

    resolved = shutil.which(name)
    if resolved:
        checked.append(resolved)
        return resolved

    raise RuntimeError(f"{name} not found; checked: {', '.join(checked)}")
