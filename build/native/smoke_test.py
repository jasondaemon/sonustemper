from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sonustemper.smoke_test import smoke_test_native
from sonustemper.tools import bundle_root, is_frozen, resolve_tool


def main() -> int:
    ok, issues = smoke_test_native()
    print(f"frozen={is_frozen()}")
    print(f"bundle_root={bundle_root()}")
    print(f"ffmpeg={resolve_tool('ffmpeg')}")
    print(f"ffprobe={resolve_tool('ffprobe')}")
    if ok:
        print("native smoke test: OK")
        return 0
    print("native smoke test: FAIL")
    for issue in issues:
        print(f"- {issue}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
