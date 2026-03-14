from __future__ import annotations

from pathlib import Path


def desktop_base_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "SonusTemper"


def desktop_data_dir() -> Path:
    return desktop_base_dir() / "data"


def desktop_logs_dir() -> Path:
    return desktop_base_dir() / "logs"


def ensure_desktop_dirs() -> None:
    base = desktop_base_dir()
    data_dir = desktop_data_dir()
    logs_dir = desktop_logs_dir()
    base.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
