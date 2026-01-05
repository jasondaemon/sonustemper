# Changelog

## [Unreleased]
- TBD

## [v1.0.0] - Pending
- Added voicing mode (8 built-in voicings) with single-select tiles and info drawers.
- Presets are single-select; “Manage Presets” page for download/delete and creating a preset from a reference upload (≤100 MB).
- Deterministic output naming with variant tags; provenance `.run.json` and metrics per output; delete endpoints clean up companions.
- Expanded output formats/options: WAV (rate/depth), MP3 (CBR/VBR), AAC/M4A, OGG (quality), FLAC (level/rate/depth).
- Processing status/previous runs/job output flow refined to update after metrics.
- Loudness stage reworked to two-pass static gain (no time-varying loudnorm) with TP ceiling; tone chains keep light EQ/comp only.
- UI/UX improvements: voicing default, centered preset note, drawer-based info on main and manage presets page.
- Licensing added: project under Apache-2.0; FFmpeg notices clarified.

[v1.0.0]: https://github.com/jasondaemon/sonustemper/releases/tag/v1.0.0
