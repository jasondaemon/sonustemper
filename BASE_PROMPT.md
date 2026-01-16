# SonusTemper – Base Context for New Chats

Use this as the starting prompt when opening a new session so the assistant has full project context, constraints, and expectations.

---

## What this project is
- **SonusTemper** is a local-first audio mastering, tagging, analysis, and file management tool.
- Backend: **FastAPI** (Python).
- UI: **Jinja2 + HTMX** served by the backend (no SPA framework).
- Primary UX is browser-based, but native desktop builds exist (macOS menu bar app, Windows browser + background process).
- Legacy UI has been removed; the only UI is the `sonustemper-ui` app.

---

## High-level architecture
- FastAPI server provides:
  - API endpoints
  - UI routes
  - SSE streams
  - Static assets
- Docker runs the app behind an nginx proxy (Basic Auth + shared secret header).
- The same server runs in:
  - Docker (behind proxy)
  - Local Python
  - Native desktop builds (PyInstaller: macOS `.app`, Windows exe)

The backend is the **single source of truth** across all environments.

---

## Native desktop app (important context)
- The desktop app is built using **PyInstaller**.
- The app:
  - Starts the FastAPI server locally on `127.0.0.1`
  - Auto-selects an available port (default range 8383–8433)
  - Opens the UI in the user’s default browser
  - macOS: runs as a **menu bar app** with "Open SonusTemper" and "Quit SonusTemper"
  - Windows: no tray/menu; keeps the background process alive
- The Dock icon is preserved on macOS (this is **not** a pure agent app).

### Native entrypoint
- `sonustemper/desktop_main.py`
  - Starts/stops Uvicorn using `uvicorn.Server` (not string imports)
  - Manages lifecycle, port detection, browser launch
  - Hosts the macOS menu bar (via `rumps`) and Windows fallback loop
  - Loads bundled resources using `sys._MEIPASS` when frozen
  - Sets OS-specific `DATA_DIR` defaults when unset

### Native build constraints
- Changes to **UI templates, CSS, JS, and existing Python logic** are safe.
- The native build **only needs spec changes** if:
  - A new Python dependency is added
  - A new external binary/resource is introduced
  - New dynamic imports are used
- UI and mastering logic changes **do not affect** the native build unless dependencies change.

---

## Native build layout (PyInstaller)
- Build output: `build/native/dist/SonusTemper.app` (macOS) or `build/native/dist/SonusTemper/` (Windows bundle)
- Spec file: `build/native/sonustemper.spec`
- Bundled resources:
  - UI templates + static assets
  - Menu bar icon (`images/sonustemper-menubar.png`)
  - App icon (`images/sonustemper.icns`)
  - ffmpeg / ffprobe binaries (from `vendor/ffmpeg/<platform>/`)
  - LICENSE + third-party notices
- HTMX must be the real minified build (not a placeholder) before packaging.
- `noarchive=True` is used for reliability on Python 3.13.

### Menu bar icon behavior
- Menu bar icon is a **template PNG** (monochrome, transparent).
- Uses `rumps.App(..., template=True)` so macOS auto-tints for light/dark mode.
- Dock/Finder icon uses `.icns`.

---

## Architecture + key files
- `sonustemper/server.py` – FastAPI entrypoint (routes, auth, SSE, static mounts).
- `sonustemper/desktop_main.py` – Native desktop entrypoint + lifecycle manager.
- `sonustemper/master_pack.py` – mastering pipeline, variant naming, metrics/provenance.
- `sonustemper/logging_util.py` – structured logging helpers.
- `sonustemper/tagger.py` – MP3 tagging service.
- `sonustemper-ui/app/ui.py` – UI router (Jinja2 templates + HTMX partials).
- `sonustemper-ui/app/templates/` – UI templates.
- `sonustemper-ui/app/static/` – UI CSS/JS.
- `nginx.conf.template`, `proxy-entrypoint.sh` – proxy auth + shared-secret header injection.
- `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md` – docs + release workflow.

---

## Data layout (default DATA_DIR)
- Mastering:
  - `/data/mastering/in`
  - `/data/mastering/out`
  - `/data/mastering/tmp`
- Tagging:
  - `/data/tagging/in`
  - `/data/tagging/tmp`
- Presets:
  - `${DATA_DIR}/presets/user`
  - `${DATA_DIR}/presets/generated`
- Analysis:
  - `/data/analysis/in`
  - `/data/analysis/out`
  - `/data/analysis/tmp`
- Previews:
  - `/data/previews` (session-scoped, TTL-cleaned)
- Native default `DATA_DIR` (if unset):
  - macOS: `~/Library/Application Support/SonusTemper/data`
  - Windows: `%APPDATA%/SonusTemper/data`

---

## UI routes
- `/` – starter page (workflow tiles + docs).
- `/mastering` – batch-first mastering UI (Voicing vs User Presets modes).
- `/analyze` – source vs output comparison (waveforms + metrics).
- `/tagging` – MP3 tagging workflow.
- `/presets` – voicing profiles + preset management (download/delete/create-from-reference).
- `/files` – file manager.
- `/docs` – documentation and how-tos.

---

## Browser sidebar + library list
- File browser partial:
  - `templates/partials/file_browser.html`
  - `static/js/components/fileBrowser.js`
- Unified listing endpoint:
  - `/partials/library_list?view=...`
- Badge pills parsed from filenames (title + voicing + preset + format).

---

## Backend behavior + API highlights
- Runs + status:
  - `POST /api/run` (multi-file; returns `run_ids` + `primary_run_id`)
  - `GET /api/status-stream?song=<run_id>` (SSE events)
  - `GET /api/run/<run_id>` (snapshot for reconnect; in-memory replay buffer)
- Preview audio (session-scoped):
  - `POST /api/preview/start`
  - `GET /api/preview/stream`
  - `GET /api/preview/file`
- Analyze helpers:
  - `/api/analyze-resolve`
  - `/api/analyze-resolve-file`
  - `/api/analyze-upload`
- Presets:
  - `/api/preset/list` (full metadata)
  - `/api/presets` (name list)
  - `/api/preset/download/{name}`
  - `/api/preset/upload`
  - `/api/preset/generate` (kind = `profile` or `voicing`)
  - `/api/preset/{name}` (delete)
- Tagger:
  - `/api/tagger/*`
- File utilities:
  - `/api/utility-*`

---

## Processing expectations
- Loudness: two-pass static (measure → fixed gain).
- No time-varying loudnorm in tone chains.
- Tone chains:
  - EQ + light compression
  - Limiter only for safety
- Variant tags are deterministic and used for:
  - filenames
  - metrics
  - provenance
- Each output includes:
  - `.metrics.json`
  - `.run.json`
- Delete endpoints remove audio **and** metadata companions.

---

## Security + runtime
- Docker perimeter is nginx Basic Auth; proxy injects `X-SonusTemper-Proxy` using `PROXY_SHARED_SECRET`.
- API key (`API_KEY`) is optional and intended for CLI/scripts; UI does not embed it.
- App-side auth accepts proxy secret or API key (unless `API_AUTH_DISABLED=1`).
- Uvicorn is started programmatically (not via CLI string); Docker and native builds share the same app.

---

## Distribution (desktop builds)
- macOS distributed via **DMG** (not App Store).
- Current macOS approach:
  - Unsigned / unnotarized
  - Users approve via **Privacy & Security → Open Anyway**
- Windows build is a PyInstaller bundle (signing/installer TBD).

---

## Regression checklist (when changing core paths)
- Build:
  - `docker compose -f docker-compose.yml build`
- Health:
  - `curl http://127.0.0.1:8383/health`
- Mastering:
  - Single-run completes
  - Multi-file run returns one SSE stream and all runs appear in Previous Runs
  - Job Output refreshes on terminal SSE event
- SSE reconnect:
  - Refresh mid-run; `/api/run/<run_id>` replay finishes cleanly
- Preview:
  - Voicing/strength change rebuilds preview via SSE
- Tagging:
  - Import MP3
  - Edit tags
  - Download album ZIP
- Presets:
  - List/download/delete user presets
  - Create-from-reference produces JSON
- Proxy (Docker):
  - Basic Auth blocks default creds
  - `PROXY_SHARED_SECRET` is set and matches app
- Native:
  - macOS app launches; menu bar icon appears; Open/Quit work correctly
  - Windows build launches; browser opens; background process stays alive

---

## When updating this file
- Keep it concise but complete.
- Update when:
  - Architecture changes
  - Native build behavior changes
  - UI workflows materially change
- This file should allow a new assistant to reason correctly **without guessing**.
