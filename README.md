# Local Mastering – Portable Setup

Single-page FastAPI UI plus mastering scripts. Runs locally or via Docker Compose without host-specific paths or docker-in-docker.

## Quickstart (Docker)
1) From the repo root:
   ```bash
   cp .env.example .env   # optional, override defaults here
   cd mastering-ui
   docker compose up --build
   ```
2) Open http://localhost:8383 (or `${PORT}` from your `.env`).
3) Presets mount from `../presets` (read-only). Audio I/O lives in `../data`:
   - Inputs: `./data/in`
   - Outputs: `./data/out`

## Local (no Docker)
- Requirements: Python 3.11+, ffmpeg/ffprobe on PATH.
- Set env vars as needed (defaults shown):
  ```
  DATA_DIR=/data
  IN_DIR=/data/in
  OUT_DIR=/data/out
  PRESET_DIR=/presets
  ```
- Run:
  ```bash
  cd mastering-ui/app
  uvicorn app:app --reload --port 8383
  ```

## Health Check
- `GET /health` reports ffmpeg/ffprobe availability, directory writability, preset count, and build stamp.

## Notes
- Selecting multiple presets runs independent masters (A/B pack), not stacked processing.
- Outputs served from `/out/...` (backed by `OUT_DIR`).
- Metrics use local ffmpeg (no docker exec).
