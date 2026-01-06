# ![SonusTemper](images/SonusTemper-128.png) SonusTemper

SonusTemper is a one-page mastering workstation: drop in a song, choose a voicing or user preset, dial loudness/stereo/tone, and export multiple formats side-by-side for A/B comparison. Everything runs locally (FastAPI + FFmpeg), with deterministic naming, metrics, and provenance files for traceable results.

![Main interface](images/maininterface.png)

## What you can do
- Master with **Voicings** (8 built-ins) or **User Presets** (your own JSON).
- Tweak loudness, true-peak, stereo width/guardrails, and tone.
- Export WAV/MP3/AAC-M4A/OGG/FLAC with per-format options.
- Inspect metrics and playback in the browser; download or delete individual outputs.
- Manage presets (download/delete) and even create a new preset from a reference audio upload (analyzed server-side, reference discarded).

## UI tour
### Voicings and User Presets
- Two modes (mutually exclusive). Default is **Voicing**; switch to **User Presets** to pick one preset. Switching modes clears the other selection.
- Single-select tiles with info drawers that explain “what it does,” “best for,” and “watch-outs.”
- Strength/Intensity slider applies to whichever mode is active.

![Settings](images/voicing-settings.png)

### Loudness
- Two-pass, static gain: first measure LUFS/true-peak, then apply a fixed offset to hit target LUFS. Skips gain if already within ±1 LU. True-peak ceiling is always enforced.

![Settings](images/loudness-settings.png)

### Stereo & Tone
- Optional stereo widening with guardrails and light tone shaping (EQ/comp). Voicings/presets supply their EQ/comp curves; stereo width is applied when enabled.

![Settings](images/stereo-settings.png)

### Output
- Select any formats you want; each stage is optional. You can simply transcode WAV ➜ MP3 (or any format) by leaving mastering stages off.
- Formats and options:
  - WAV: sample rate/bit depth
  - MP3: CBR bitrates or VBR (V0/V2)
  - AAC/M4A: bitrate + container
  - OGG Vorbis: quality level
  - FLAC: compression level + optional rate/depth

![Settings](images/output-settings.png)

### Processing Status, Previous Runs, Job Output
- Processing Status lists each step (voicing/preset render, loudness, per-format exports, metrics).
- Previous Runs updates only after a job fully finishes (including metrics) and lets you reload a past run into Job Output.
- Job Output shows playback, per-format download links, delete links, and the metrics panel (LUFS, TP, LRA, crest, DR, noise, duration, width, etc.).

### Status delivery (SSE, no polling)
- Runs start via `/api/run` and stream status over Server-Sent Events from `/api/status-stream?song=<run_id>`.
- The UI reconnects once via `/api/run/<run_id>` if the stream drops, so there’s no `.processing` file polling.
- A tiny in-memory registry keeps the last N events per run for fast replay; terminal events include outlist/metrics payloads so the UI can render immediately.

## Presets and the Manage Presets page
- User presets live in `./presets/*.json` (and an internal writable dir for generated presets). Info text reminds that presets are user-customizable.
- Manage Presets (full-page modal):
  - Download/Delete existing presets (delete requires confirmation).
  - Create preset from reference: upload audio (≤100 MB); FFmpeg analyzes loudness/tonal balance and seeds a preset JSON using the source filename. The reference file is purged after analysis; metadata records source name and creation time.
  - “Return to SonusTemper” returns you to the main mastering UI.

## Naming, metrics, provenance
- Outputs share a deterministic variant tag built from the effective config (preset/voicing, strength, loudness target/TP, stereo width/guardrails, and encoding options). A length guard adds a short hash if needed.
- Each mastered file has siblings:
  - `<stem>__<variant>.metrics.json` (analysis)
  - `<stem>__<variant>.run.json` (provenance: exact payload + resolved values)
- Delete links/API remove the audio plus its metrics/provenance companions.

![Metrics panel](images/jobmetrics.png)

## Data paths
- Inputs: `./data/in`
- Outputs: `./data/out`
- Presets: `./presets/*.json` (starter examples in `./example-presets/`)

## Install & run
### Docker (recommended)
```bash
cp .env.example .env   # optional: set PORT, SONUSTEMPER_TAG
# Set Basic Auth creds (required): edit .env to change BASIC_AUTH_PASS from CHANGEME
# Optional: set API_KEY only for CLI scripts (UI does not use it)
docker compose pull
docker compose up -d
# open http://localhost:${PORT:-8383}
```
Mounts (defaults):
- `./data` -> `/data` (I/O)
- `./presets` -> `/presets` (writable for generated presets)

### Docker (dev build)
```bash
cp .env.example .env
docker compose -f docker-compose.dev.yml up --build
```

### Local (no Docker)
- Requires Python 3.11+ and ffmpeg/ffprobe on PATH.
- Env defaults: `DATA_DIR=/data`, `IN_DIR=/data/in`, `OUT_DIR=/data/out`, `PRESET_DIR=/presets`
```bash
cd mastering-ui/app
uvicorn app:app --reload --port 8383
```

### Security defaults
- Proxy-level Basic Auth is ON by default (BASIC_AUTH_ENABLED=1).
- Defaults in `.env.example`: user `admin`, pass `CHANGEME`. You must change the password; proxy will refuse to start if unchanged.
- All UI/API/SSE routes are behind Basic Auth.
- The optional `API_KEY` is only for non-browser clients/CLI scripts; it is not embedded in the UI and is not required once Basic Auth succeeds. Proxy adds its own shared-secret header internally.
- See `SECURITY.md` for the security posture and hardening notes (proxy perimeter, shared credentials, not Internet-facing without TLS/VPN).

## Images and tags
- GHCR: `ghcr.io/jasondaemon/sonustemper`
- Set `SONUSTEMPER_TAG` to a release tag (`vX.Y.Z`) or use `latest`. 

## Health
`GET /health` reports ffmpeg/ffprobe availability, directory writability, preset status, build/app id.

## Links
- Security: `SECURITY.md`
- Changelog: `CHANGELOG.md`
- License: `LICENSE`
- Third-party notices: `THIRD_PARTY_NOTICES.md`

## Quick regression checklist
- Single-run happy path: one file, one voicing, all outputs, confirm status stream completes and Job Output auto-loads.
- Multi-file run: two files, mixed formats, confirm a single SSE stream drives status and both appear in Previous Runs.
- SSE reconnect: refresh the page mid-run; ensure status replays via `/api/run/<run_id>` and finishes cleanly.
- Error path: intentionally bad preset/voicing to verify terminal `error` event stops the stream and UI doesn’t spin.

## License
- Project code: Apache 2.0 (see `LICENSE`).
- FFmpeg is installed from the distro package and remains under its original LGPL/GPL licensing; see `THIRD_PARTY_NOTICES.md` for details.
