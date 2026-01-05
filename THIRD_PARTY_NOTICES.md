## Third-Party Notices

This project uses FFmpeg for audio processing.

- FFmpeg is licensed under LGPL/GPL; see https://ffmpeg.org/legal.html
- “FFmpeg” and its logos are trademarks of Fabrice Bellard et al.

- FFmpeg binaries in the published Docker image come from the base distribution package (`apt-get install ffmpeg`), which is typically built under the LGPL configuration (no explicit `--enable-gpl` flags). Refer to the distro package for exact licensing terms.

No FFmpeg binaries are redistributed here beyond those installed via the package manager in the provided Docker image. Refer to the FFmpeg website for full license terms.
