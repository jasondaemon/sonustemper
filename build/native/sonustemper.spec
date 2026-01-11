from pathlib import Path
import sys

project_root = Path(__file__).resolve().parents[2]
entry = project_root / "sonustemper" / "desktop_main.py"

datas = [
    (str(project_root / "sonustemper-ui" / "app" / "ui.py"), "sonustemper-ui/app"),
    (str(project_root / "sonustemper-ui" / "app" / "templates"), "sonustemper-ui/app/templates"),
    (str(project_root / "sonustemper-ui" / "app" / "static"), "sonustemper-ui/app/static"),
    (str(project_root / "LICENSE"), "."),
    (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
    (str(project_root / "LICENSES"), "LICENSES"),
]

binaries = []
if sys.platform == "darwin":
    binaries += [
        (str(project_root / "vendor" / "ffmpeg" / "macos" / "ffmpeg"), "vendor/ffmpeg/macos"),
        (str(project_root / "vendor" / "ffmpeg" / "macos" / "ffprobe"), "vendor/ffmpeg/macos"),
    ]
elif sys.platform.startswith("win"):
    binaries += [
        (str(project_root / "vendor" / "ffmpeg" / "windows" / "ffmpeg.exe"), "vendor/ffmpeg/windows"),
        (str(project_root / "vendor" / "ffmpeg" / "windows" / "ffprobe.exe"), "vendor/ffmpeg/windows"),
    ]

a = Analysis(
    [str(entry)],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
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
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="SonusTemper",
)
