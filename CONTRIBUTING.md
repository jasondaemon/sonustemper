# Contributing

Thanks for helping improve SonusTemper! A few quick notes to keep changes flowing smoothly.

## Branch & release flow
- Develop on a branch (e.g., `dev` or a feature branch); keep `main` releasable.
- When ready to release, merge into `main` and tag a semver (`vX.Y.Z`) to publish GHCR images (`vX.Y.Z` + `latest`).

## Commits & PRs
- Keep commits scoped and descriptive. Prefer PRs into `main` for review/history.
- If adding dependencies, update `requirements.txt` or related manifests.
- You’re encouraged to use Codex/OpenAI assistance for code generation and refactors; keep changes reviewed and clear in diffs.

## Regression / functionality checks
- Build locally before tagging: `docker compose -f docker-compose.yml build`
- Quick health: `docker compose -f docker-compose.yml up -d` then `curl http://127.0.0.1:8383/health`
- Sanity exercises:
  - Run a job with only MP3 output (no mastering) to verify passthrough works.
  - Run a voicing job and a preset job to confirm variant naming and outputs land with distinct tags.
  - Check metrics load without page refresh (Previous Runs -> Job Output).
  - Confirm delete links remove audio + metrics/provenance for a run.
  - Verify Manage Presets: list/download/delete, and create-from-reference (≤100 MB) produces a new preset JSON.

## Tests / checks
- Run a local build before tagging: `docker compose -f docker-compose.yml build`.
- Quick sanity: `docker compose -f docker-compose.yml up -d` then `curl http://127.0.0.1:8383/health`.

## Coding notes
- Python 3.11+, FFmpeg on PATH.
- Keep filenames ASCII; avoid destructive git commands.
- UI follows the existing theme/components in `mastering-ui/app/app.py`.

## Licensing
- Project code is Apache-2.0 (see `LICENSE`).
- FFmpeg remains under its own LGPL/GPL terms (see `THIRD_PARTY_NOTICES.md`); don’t vendor GPL-only builds unless intended.
