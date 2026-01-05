# SonusTemper

Single-page FastAPI UI + mastering scripts. Portable: runs locally or via Docker Compose with no host-specific paths or docker-in-docker.

## Quickstart
### Prod/Stage (pull prebuilt image)
```bash
cp .env.example .env   # optional: set PORT, SONUSTEMPER_TAG
docker compose pull
docker compose up -d
```
Defaults mount:
- `./data` -> `/data` (I/O)
- `./presets` -> `/presets` (read-only)
Open http://localhost:${PORT:-8383}

### Dev (build from source)
```bash
cp .env.example .env
docker compose -f docker-compose.dev.yml up --build
```

### Local (no Docker)
- Requirements: Python 3.11+, ffmpeg/ffprobe on PATH.
- Env defaults: `DATA_DIR=/data`, `IN_DIR=/data/in`, `OUT_DIR=/data/out`, `PRESET_DIR=/presets`
```bash
cd mastering-ui/app
uvicorn app:app --reload --port 8383
```

## Data & Presets
- Inputs: `./data/in`
- Outputs: `./data/out`
- Presets: `./presets/*.json` (mounted read-only). A starter set lives in `./example-presets/`; copy them into `./presets/` (or use your own) before running.
- Selecting multiple presets runs independent masters for A/B comparison (no stacking).

## Core Workflow (UI)
- Choose **Voicings and User Presets**: mode defaults to **Voicing** (8 built-ins); switch to **User Presets** to pick one preset. Strength/Intensity is shared and single-select in both modes.
- Enable processing stages as needed: Analyze, Loudness/Normalize, Stereo & Tone, Output formats. If all stages are off you can still transcode the source.
- Outputs: WAV (rate/depth), MP3 (CBR/VBR), AAC/M4A, OGG (quality), FLAC (level/rate/depth). Each format is optional.
- Processing Status shows each step; Previous Runs refresh once the full job (including metrics) finishes.

## Voicings & Presets
- Voicing mode is mutually exclusive with presets; switching modes clears the other selection.
- Preset note: “Presets are user-customization from the presets directory.” Files live in `./presets` (and optionally a writable generated-presets dir inside the container).
- Info drawers on voicings/presets explain what each choice does.

## Manage Presets Page
- Click **Manage Presets** to open the full-page manager (same theme).
- Actions per preset: Download JSON, Delete (with confirmation). Metadata shows source and creation time if generated.
- **Create preset from reference**: upload an audio file (≤100 MB). The server analyzes it with ffmpeg (loudness/tonal balance) and seeds a preset JSON in the presets directory. The reference audio is discarded after analysis.
- “Return to SonusTemper” takes you back to the main mastering page.

## Output Naming & Traceability
- Filenames carry a deterministic variant tag built from the effective run config (preset/voicing, strength, loudness target/TP, stereo width/guardrails, and encoding options). Reordering config keys won’t change the tag.
- Length safeguard: long tags are shortened with a stable hash suffix to keep filenames safe.
- Every output has a sibling provenance file: `<stem>__<variant>.run.json` (the exact job payload + resolved values) and a metrics JSON.
- Delete links/API remove the mastered files plus their metrics/provenance.

## Loudness & Dynamics
- Loudness stage is two-pass/static: first measure LUFS/TP, then apply a fixed gain to hit target LUFS; skipped if already within ±1 LU. No time-varying loudness riding.
- True-peak ceiling is always enforced on the final render. Tone stages keep gentle EQ/comp only; no embedded loudnorm.

## Health Check
`GET /health` returns ffmpeg/ffprobe availability, dir writability, preset status, build/app id.

## Images and Tags
- GHCR image: `ghcr.io/jasondaemon/sonustemper`
- Env `SONUSTEMPER_TAG` controls the tag (`edge` by default, `vX.Y.Z` for releases).
- For private GHCR, authenticate Docker with a GHCR PAT or GITHUB_TOKEN.

## Notes
- Outputs served from `/out/...` (backed by `OUT_DIR`).
- Metrics use local ffmpeg (no docker exec).
- Powered by FFmpeg (see THIRD_PARTY_NOTICES.md).
