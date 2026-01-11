# Vendoring FFmpeg/ffprobe

Native builds bundle FFmpeg and ffprobe so end users do not install them.

Place platform binaries before running the build scripts:

- macOS: vendor/ffmpeg/macos/ffmpeg and vendor/ffmpeg/macos/ffprobe
- Windows: vendor/ffmpeg/windows/ffmpeg.exe and vendor/ffmpeg/windows/ffprobe.exe

The build scripts fail fast if these files are missing.
Ensure the binaries and licenses you ship match your intended distribution.
