# Changelog

## [Unreleased]
- TBD

## [v1.3.0] - Released
- Security hardening: proxy-only perimeter with Basic Auth + shared secret header; envsubst-rendered nginx config; UI no longer embeds API keys; proxy refuses default creds.
- Non-root containers by default (configurable APP_UID/GID); single `/data` tree for mastering/tagging/presets/analysis; removed separate `/presets` mount.
- Tag Editor: full ID3 workflow (list/import, single/album editing, artwork apply/clear, album ZIP download), badge parsing, working set UX, width-based overflow badges, tooltips.
- Utilities menu expanded: Tag Editor, Preset Manager, File Manager promoted to full pages; preset manager now supports direct JSON preset upload with validation.
- Performance: cached `/api/outlist`, avoids expensive metric recompute on Previous Runs; main output list uses parsed titles/badges.

## [v1.2.0] - Released
- Replaced polling with SSE status-stream: `/api/run` start endpoint, `/api/status-stream` for live updates, `/api/run/{id}` snapshots for reconnects; in-memory ring buffer with TTL cleanup and keepalive.
- Added source metrics analysis (loudnorm + astats) with status events, writing input metrics into `metrics.json` for full “In/Out” comparisons.
- Status timeline now includes source-analysis stages and removes `.processing` checks; UI auto-refreshes on terminal events.
- Added concurrency guard for runs (`MAX_CONCURRENT_RUNS`) and safer event-loop dispatch.
- Default UI mode is Voicing on fresh load; added README images and logo header.

## [v1.1.0] - Released
- Added voicing mode (8 built-in voicings) with single-select tiles and info drawers.
- Presets are single-select; “Manage Presets” page for download/delete and creating a preset from a reference upload (≤100 MB).
- Deterministic output naming with variant tags; provenance `.run.json` and metrics per output; delete endpoints clean up companions.
- Expanded output formats/options: WAV (rate/depth), MP3 (CBR/VBR), AAC/M4A, OGG (quality), FLAC (level/rate/depth).
- Processing status/previous runs/job output flow refined to update after metrics.
- Loudness stage reworked to two-pass static gain (no time-varying loudnorm) with TP ceiling; tone chains keep light EQ/comp only.
- UI/UX improvements: voicing default, centered preset note, drawer-based info on main and manage presets page.
- Licensing added: project under Apache-2.0; FFmpeg notices clarified.

[v1.0.0]: https://github.com/jasondaemon/sonustemper/releases/tag/v1.0.0
[v1.1.0]: https://github.com/jasondaemon/sonustemper/releases/tag/v1.1.0
[v1.2.0]: https://github.com/jasondaemon/sonustemper/releases/tag/v1.2.0
