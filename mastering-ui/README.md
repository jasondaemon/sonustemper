# Mastering UI

FastAPI + frontend assets for the SonusTemper single-page app.

- `app/app.py`: routes, UI rendering, preset/voicing selection, processing status, previous runs/job output, and manage-presets page (download/delete/create-from-reference).
- Uses the backend in `../mastering/master_pack.py` for processing, metrics, and output naming/provenance.
- Outputs are served from `/out`, inputs from `/in`, presets from `/presets` (writable for generated presets).
- To run locally without Docker:
  ```bash
  cd mastering-ui/app
  uvicorn app:app --reload --port 8383
  ```
- For Docker usage, see the root `README.md`.

Reference docs: project overview (`../README.md`), workflow/testing (`../CONTRIBUTING.md`), changes (`../CHANGELOG.md`), licensing (`../LICENSE`, `../THIRD_PARTY_NOTICES.md`).
