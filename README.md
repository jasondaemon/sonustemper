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

## Health Check
`GET /health` returns ffmpeg/ffprobe availability, dir writability, preset status, build/app id.

## Images and Tags
- GHCR image: `ghcr.io/jasondaemon/sonustemper`
- Env `SONUSTEMPER_TAG` controls the tag (`edge` by default, `vX.Y.Z` for releases).
- For private GHCR, authenticate Docker with a GHCR PAT or GITHUB_TOKEN.

## Legacy
- Old ffmpeg-sidecar compose is kept only for reference at `legacy/docker-compose.ffmpeg-old.yml` and is not used in the current workflow.

## Notes
- Outputs served from `/out/...` (backed by `OUT_DIR`).
- Metrics use local ffmpeg (no docker exec).
- Powered by FFmpeg (see THIRD_PARTY_NOTICES.md).
