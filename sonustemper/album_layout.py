"""Physical folder names for the stable logical stages used in album jobs."""
from pathlib import Path
from typing import TypedDict


AUDIO_FOLDERS = {stage: f"{i:02d} - {stage}" for i, stage in enumerate(
    ("raw", "mastered", "clean", "normalized", "mp3s")
)}


class LayoutMove(TypedDict):
    source: str | None
    target: str


def child(parent: Path, name: str) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Invalid path component")
    path = parent / name
    if path.is_symlink():
        raise ValueError("Symbolic links are unavailable in production albums")
    return path


def folder_names(album: Path, stage: str) -> tuple[Path, Path]:
    old, new = child(album, stage), child(album, AUDIO_FOLDERS[stage])
    for path in (old, new):
        if path.exists() and not path.is_dir():
            raise ValueError(f"Expected a directory: {path.name}")
    if old.exists() and new.exists():
        raise ValueError(f"Both {old.name} and {new.name} exist; resolve this conflict before organizing.")
    return old, new


def stage_dir(album: Path, stage: str) -> Path:
    if stage not in AUDIO_FOLDERS:
        return child(album, stage)
    old, new = folder_names(album, stage)
    return old if old.is_dir() else new


def visible_files(folder: Path, suffix: str) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir()
                  if not path.name.startswith((".", "#", "@")) and path.suffix.lower() == suffix
                  and not path.is_symlink() and path.is_file())


def flat_raw(album: Path) -> list[Path]:
    if stage_dir(album, "raw").exists() or stage_dir(album, "mastered").exists():
        return []
    return visible_files(album, ".wav")


def flat_moves(album: Path) -> list[LayoutMove]:
    loose = flat_raw(album)
    if not loose:
        return []
    raw = AUDIO_FOLDERS["raw"]
    return [{"source": None, "target": raw}, *[
        {"source": path.name, "target": f"{raw}/{path.name}"} for path in loose
    ]]


def layout_plan(album: Path) -> list[LayoutMove]:
    moves: list[LayoutMove] = []
    for stage in AUDIO_FOLDERS:
        old, new = folder_names(album, stage)
        if old.is_dir():
            moves.append({"source": old.name, "target": new.name})
    return moves + flat_moves(album)


def layout_path(album: Path, relative: str) -> Path:
    path = album
    for component in relative.split("/"):
        path = child(path, component)
    return path


def rename_without_replacing(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Will not replace {target.name}")
    source.rename(target)


def rollback_layout(album: Path, moves: list[LayoutMove]) -> None:
    for move in reversed(moves):
        target = layout_path(album, move["target"])
        if move["source"] is None:
            target.rmdir()
        else:
            rename_without_replacing(target, layout_path(album, move["source"]))


def apply_layout(album: Path, moves: list[LayoutMove]) -> None:
    done: list[LayoutMove] = []
    try:
        for move in moves:
            target = layout_path(album, move["target"])
            if move["source"] is None:
                target.mkdir()
            else:
                rename_without_replacing(layout_path(album, move["source"]), target)
            done.append(move)
    except (OSError, ValueError) as exc:
        try:
            rollback_layout(album, done)
        except (OSError, ValueError) as rollback:
            raise RuntimeError(f"Could not fully roll back album folders: {rollback}") from exc
        raise
