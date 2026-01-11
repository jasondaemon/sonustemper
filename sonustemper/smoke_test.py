from __future__ import annotations

import shutil
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


def main() -> int:
    ok, issues = smoke_test_native()
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
