from __future__ import annotations

import shutil
import os
import tempfile
import importlib
from pathlib import Path

from sonustemper.tools import bundle_root, is_frozen, resolve_tool


def smoke_test_native() -> tuple[bool, list[str]]:
    issues: list[str] = []
    root = bundle_root()
    ui_root = root / "sonustemper-ui" / "app"
    templates_dir = ui_root / "templates"
    static_dir = ui_root / "static"
    htmx_path = static_dir / "vendor" / "htmx.min.js"

    if not templates_dir.exists():
        issues.append(f"missing templates: {templates_dir}")
    if not static_dir.exists():
        issues.append(f"missing static: {static_dir}")
    if not htmx_path.exists():
        issues.append(f"missing HTMX: {htmx_path}")
    else:
        data = htmx_path.read_text(encoding="utf-8", errors="ignore")
        if "HTMX_PLACEHOLDER" in data or htmx_path.stat().st_size < 10000:
            issues.append("HTMX vendor file appears to be a placeholder")

    for name in ("ffmpeg", "ffprobe"):
        tool = resolve_tool(name)
        path = Path(tool)
        if path.is_absolute():
            if not path.exists():
                issues.append(f"{name} not found at {path}")
        else:
            if shutil.which(tool) is None:
                issues.append(f"{name} not found on PATH")

    return (len(issues) == 0, issues)

def smoke_test_library_db() -> tuple[bool, list[str]]:
    issues: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["DATA_DIR"] = tmpdir
        try:
            import sonustemper.storage as storage
            import sonustemper.library_db as library_db
            importlib.reload(storage)
            importlib.reload(library_db)
            library_db.init_db()
            if not storage.LIBRARY_DB.exists():
                issues.append(f"library db missing: {storage.LIBRARY_DB}")
            song = library_db.upsert_song_for_source(
                "library/songs/s_test/source/test.wav",
                "Test Song",
                123.4,
                "wav",
                {"lufs_i": -14.0, "true_peak_db": -1.0, "lra": 3.2},
                True,
                song_id="s_test",
                file_mtime_utc="2026-01-15T00:00:00Z",
            )
            if song.get("song_id") != "s_test":
                issues.append("song insert failed")
            version = library_db.add_version(
                "s_test",
                "master",
                "Master",
                "Test Song",
                {"voicing": "Punch", "loudness_profile": "Apple Music"},
                {"lufs_i": -14.0, "true_peak_db": -1.0},
                [{"format": "wav", "rel": "library/songs/s_test/versions/v_test/master.wav"}],
                version_id="v_test",
            )
            if version.get("version_id") != "v_test":
                issues.append("version insert failed")
            lib = library_db.list_library()
            if not lib.get("songs"):
                issues.append("library list empty after insert")
            deleted, _rels = library_db.delete_song("s_test")
            if not deleted:
                issues.append("song delete failed")
            lib2 = library_db.list_library()
            if lib2.get("songs"):
                issues.append("library not empty after delete")
        finally:
            os.environ.pop("DATA_DIR", None)
    return (len(issues) == 0, issues)

def main() -> int:
    ok, issues = smoke_test_native()
    ok_db, db_issues = smoke_test_library_db()
    issues.extend(db_issues)
    ok = ok and ok_db
    print(f"frozen={is_frozen()}")
    print(f"bundle_root={bundle_root()}")
    if ok:
        print("native smoke test: OK")
        return 0
    print("native smoke test: FAIL")
    for issue in issues:
        print(f"- {issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
