from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules

spec_dir = Path(SPECPATH).resolve()

def find_repo_root(start: Path) -> Path:
    for p in [start] + list(start.parents):
        if (p / "sonustemper").is_dir() and (p / "sonustemper-ui").is_dir() and (p / "vendor").is_dir():
            return p
    raise FileNotFoundError(f"Could not locate repo root from {start}")

project_root = find_repo_root(spec_dir)
if sys.platform == "darwin":
    entry = project_root / "sonustemper" / "macos_app.py"
else:
    entry = project_root / "sonustemper" / "desktop_main.py"

datas = [
    (str(project_root / "sonustemper-ui" / "app" / "ui.py"), "sonustemper-ui/app"),
    (str(project_root / "sonustemper-ui" / "app" / "templates"), "sonustemper-ui/app/templates"),
    (str(project_root / "sonustemper-ui" / "app" / "static"), "sonustemper-ui/app/static"),
    (str(project_root / "assets" / "demo"), "assets/demo"),
    (str(project_root / "assets" / "presets"), "assets/presets"),
    (str(project_root / "images"), "images"),
    (str(project_root / "docs"), "docs"),
    (str(project_root / "LICENSE"), "."),
    (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
    (str(project_root / "LICENSES"), "LICENSES"),
]

lockfile = project_root / "sonustemper" / "vendor" / "ffmpeg.lock.json"
if lockfile.exists():
    datas.append((str(lockfile), "vendor"))

for arch in ("arm64", "x86_64"):
    ffmpeg_bin = project_root / "sonustemper" / "vendor" / "ffmpeg" / arch / "ffmpeg"
    ffprobe_bin = project_root / "sonustemper" / "vendor" / "ffmpeg" / arch / "ffprobe"
    if ffmpeg_bin.exists():
        datas.append((str(ffmpeg_bin), f"vendor/ffmpeg/{arch}"))
    if ffprobe_bin.exists():
        datas.append((str(ffprobe_bin), f"vendor/ffmpeg/{arch}"))

binaries = []

hiddenimports = collect_submodules("sonustemper")
hiddenimports += collect_submodules("rumps")
hiddenimports += collect_submodules("objc")
hiddenimports += collect_submodules("Foundation")
hiddenimports += collect_submodules("AppKit")
hiddenimports += collect_submodules("PyObjCTools")

a = Analysis(
    [str(entry)],
    pathex=[
        str(project_root),
        str(project_root / "sonustemper"),
    ],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=True,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SonusTemper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(project_root / "images" / "sonustemper.icns"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="SonusTemper",
)

# macOS app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SonusTemper.app",
        icon=str(project_root / "images" / "sonustemper.icns"),
        bundle_identifier="net.jasondaemon.sonustemper",
        info_plist={
            "CFBundleName": "SonusTemper",
            "CFBundleDisplayName": "SonusTemper",
            "CFBundleIdentifier": "net.jasondaemon.sonustemper",
            "LSUIElement": "0",
            "LSBackgroundOnly": "0",
        },
    )
