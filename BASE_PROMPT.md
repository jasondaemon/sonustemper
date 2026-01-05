# SonusTemper – Base Context for New Chats

Use this as the starting prompt when opening a new session so the assistant has project context and expectations.

## What this project is
- SonusTemper: single-page FastAPI UI + FFmpeg mastering pipeline. Users pick a voicing or user preset, configure loudness/stereo/tone, export multiple formats, and view metrics/provenance.
- Key files:
  - `mastering/master_pack.py` – processing, variant naming, metrics/provenance, output encoding, presets/voicings, loudness pipeline.
  - `mastering-ui/app/app.py` – UI/UX, routes (manage presets, job status, etc.).
  - `README.md` – end-user how-to and capabilities.
  - `CHANGELOG.md` – release notes; keep current.
  - `CONTRIBUTING.md` – branch/tag flow, regression checks, coding notes.
  - `THIRD_PARTY_NOTICES.md` – FFmpeg licensing notes.
  - `LICENSE` – Apache-2.0 for project code.

## Processing expectations
- Voicings vs User Presets are mutually exclusive, single-select; strength/“Intensity” shared.
- Loudness stage is two-pass static: render tone first, measure LUFS/TP, apply fixed gain only if outside ±1 LU; enforce TP ceiling; no time-varying loudnorm in tone chains.
- Tone chains: EQ + light comp only (no embedded loudnorm/limiters).
- Variant tags are deterministic from effective config (preset/voicing, strength, loudness targets, stereo width/guardrails, encoding opts) with length hashing safeguard; used for all outputs + metrics. Each output has `.metrics.json` and `.run.json`.
- Manage Presets page: list/download/delete; create-from-reference (≤100 MB) analyzes audio and seeds a preset JSON; reference is discarded.
- Delete links/API must remove audio plus metrics/provenance.
- Previous Runs update only after full job (including metrics); loading a run should populate Job Output.

## Release/versioning
- Code is Apache-2.0; FFmpeg stays under its own LGPL/GPL (distro ffmpeg). Don’t change license without discussion.
- Tags: semver `vX.Y.Z` used for releases (`latest` tracks most recent tag). `edge` from main is optional; current workflow focuses on tagged releases.
- Keep `CHANGELOG.md` updated for user-facing changes. Mention key fixes/features and loudness/output/UX changes.

## Regression checklist (when changing core paths)
- Local build: `docker compose -f docker-compose.yml build`
- Health: `docker compose -f docker-compose.yml up -d` then `curl http://127.0.0.1:8383/health`
- Functional sanity:
  - Passthrough: run job with only MP3 output (no mastering) — confirm file produced.
  - Voicing vs preset runs create distinct variant tags and outputs.
  - Metrics load without page refresh; Previous Runs -> Job Output works.
  - Delete links remove audio + metrics/provenance.
  - Manage Presets: list/download/delete; create-from-reference (<100 MB) yields new preset JSON.

## When updating this file
- Keep it concise but complete enough to prime a new chat.
- Update if workflows, licensing, naming, or key UX/processing rules change.
