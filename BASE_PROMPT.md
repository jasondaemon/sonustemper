# SonusTemper - Base Context for New Chats

Use this as the starting prompt when opening a new session so the assistant has project context and expectations.

## What this project is
- SonusTemper: FastAPI backend + Jinja2/HTMX UI for local mastering, tagging, analysis, and file management.
- Legacy UI has been removed; the only UI is the new sonustemper-ui app.

## Architecture + key files
- `sonustemper/server.py` - FastAPI entrypoint (API routes, auth, SSE, static mounts).
- `sonustemper/master_pack.py` - mastering pipeline, variant naming, metrics/provenance, outputs.
- `sonustemper/logging_util.py` - structured logging helpers used by the pipeline.
- `sonustemper/tagger.py` - MP3 tagger service used by the Tagging UI.
- `sonustemper-ui/app/ui.py` - UI router (Jinja2 templates + HTMX partials).
- `sonustemper-ui/app/templates/` - UI templates (mastering, tagging, analyze, presets, docs).
- `sonustemper-ui/app/static/` - UI CSS/JS.
- `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md` - user docs + release workflow.

## Data layout (default /data)
- Mastering: `/data/mastering/in`, `/data/mastering/out`, `/data/mastering/tmp`
- Tagging: `/data/tagging/in`, `/data/tagging/tmp`
- Presets: `/data/presets/user`, `/data/presets/generated`
- Analysis: `/data/analysis/in`, `/data/analysis/out`, `/data/analysis/tmp`
- Previews (session temp): `/data/previews` (TTL-cleaned, non-persistent)

## New UI pages (routes at root)
- `/` starter page (tiles for key workflows + docs).
- `/mastering` batch-first mastering UI:
  - Input list with selection + upload, Output Formats block.
  - Voicing + Strength, Loudness Profile with Manual overrides, Stereo Width + Guardrails.
  - Convert Only option disables voicing/loudness/stereo and runs output conversion only.
  - Run summary bar with Run Job button (disabled if no selection).
  - Job Output shows metric pills + Analyze buttons + downloads + delete.
- `/analyze` compares source vs processed output (waveforms + metrics).
  - Uses /api/analyze-resolve or /api/analyze-resolve-file; supports standalone upload mode.
- `/tagging` MP3 tagging workflow with working set, album actions, downloads.
- `/presets` (Voicing Profiles): manage user/generated voicings and delivery profiles.
  - Upload JSON auto-detects voicing vs profile; delete only for user presets.
  - Create from reference supports separate "Create Voicing" and "Create Profile" flows.
- `/files` file manager.
- `/docs` documentation/how-tos (links to third-party tools).

## Browser sidebar + library list
- File browser component lives in `templates/ui/partials/file_browser.html` and `static/js/components/fileBrowser.js`.
- Unified listing endpoint: `/partials/library_list?view=...` with badge pills + overflow.
- Views include mastering runs, tagging mp3s, runs with mp3 outputs, combined lists.

## Backend behavior + API highlights
- SSE status stream: `/api/status-stream?song=<run_id>` (no polling loops).
- Preview audio (session scoped):
  - `POST /api/preview/start` -> preview_id
  - `GET /api/preview/stream?preview_id=...` (SSE)
  - `GET /api/preview/file?preview_id=...`
- Analyze helpers: `/api/analyze-resolve`, `/api/analyze-resolve-file`, `/api/analyze-upload`.
- Presets: `/api/preset/list`, `/api/preset/upload`, `/api/preset/generate`, `/api/preset/{name}`.
- Tagger: `/api/tagger/*` for MP3 metadata + artwork + album download.
- File manager: `/api/utility-*`.

## Processing expectations
- Loudness is two-pass static: measure then apply fixed gain (no time-varying loudnorm in tone chains).
- Tone chains: EQ + light comp only; limiter used for safety where specified.
- Variant tags are deterministic from effective config and used across outputs + metrics.
- Each output has `.metrics.json` and `.run.json` siblings for provenance.
- Delete endpoints remove audio plus metrics/provenance companions.

## Security + runtime
- API key and proxy shared secret guard `/api/*` (unless API_AUTH_DISABLED=1).
- Server entrypoint: `uvicorn sonustemper.server:app`.
- Docker runs the same entrypoint; UI routes are mounted at `/`.

## Regression checklist (when changing core paths)
- Build: `docker compose -f docker-compose.yml build`
- Health: `curl http://127.0.0.1:8383/health`
- Mastering: batch run, status SSE completes, Job Output refreshes on completion.
- Preview: change voicing/strength, preview builds via SSE, plays.
- Tagging: import MP3, edit tags, download album zip.
- Presets: list/download/delete user presets; create-from-reference produces JSON.

## When updating this file
- Keep it concise but complete enough to prime a new chat.
- Update when architecture, workflows, or key UI behaviors change.
