# Mastering (Backend)

This folder contains the audio processing pipeline.

- `master_pack.py`: main entry point for mastering runs, output naming, metrics, provenance, and preset/voicing handling.
- Voicings/presets are single-select; loudness is two-pass static (measure then fixed gain); variant tags are deterministic and used across outputs/metrics.
- Outputs include WAV/MP3/AAC-M4A/OGG/FLAC plus sibling `.metrics.json` and `.run.json` for traceability.
- Delete endpoints are expected to remove audio + metrics/provenance companions.

For UI/front-end context, see `../sonustemper-ui/app`. For project overview, see the root `README.md`, `CHANGELOG.md`, and `CONTRIBUTING.md`.
