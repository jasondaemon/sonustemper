#!/usr/bin/env python3
"""Generate a markdown report of installed Python dependencies and licenses."""
from __future__ import annotations

from pathlib import Path
import importlib.metadata as metadata

def _license_for(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    if meta is None:
        return "unknown"
    lic = (meta.get("License") or "").strip()
    if lic and lic.lower() != "unknown":
        return lic
    classifiers = meta.get_all("Classifier") or []
    for classifier in classifiers:
        if classifier.startswith("License ::"):
            return classifier.split("::", 1)[-1].strip()
    return "unknown"

def main() -> int:
    rows = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata.get("Name", "")):
        name = (dist.metadata.get("Name") or dist.metadata.get("Summary") or dist.name or "").strip()
        if not name:
            continue
        version = (dist.version or "").strip()
        license_name = _license_for(dist)
        rows.append((name, version, license_name))

    out_path = Path("docs/python-deps.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Python Dependencies",
        "",
        "| Package | Version | License |",
        "| --- | --- | --- |",
    ]
    for name, version, license_name in rows:
        lines.append(f"| {name} | {version} | {license_name} |")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
