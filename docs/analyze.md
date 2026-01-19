# Analyze

## 🧭 Table of contents
- [What it does](#what-it-does)
- [When to use it](#when-to-use-it)
- [Step-by-step](#step-by-step)
- [Controls and functions](#controls-and-functions)
- [Common pitfalls](#common-pitfalls)
- [Tips](#tips)
- [Screenshot placeholders](#screenshot-placeholders)

## What it does
Analyze provides inspection tools for audio (metrics, spectra, and diagnostics). It is the entry point for deeper cleanup decisions.

## When to use it
- Inspect loudness and dynamics metrics.
- Review spectral balance before cleanup.
- Jump to other utilities from a known reference.

## Step‑by‑step
1) Select a track in the Library.
2) Review the metrics panel (LUFS, TP, LRA, crest, etc.).
3) Inspect waveform or spectrogram for issues.
4) Use **Open in** actions to continue in other utilities.

## Controls and functions

### Metrics
- **LUFS‑I**: Integrated loudness.
- **TP dBTP**: True peak level.
- **LRA**: Loudness range.
- **Crest / DR / RMS**: Dynamics indicators.
- **Noise floor**: Low‑level noise estimate.

### Visuals
- **Waveform**: Time‑domain overview.
- **Spectrogram**: Frequency‑time intensity view.
- **Zoom**: Controls horizontal scale.

### Actions
- **Open in Compare**: Source vs another version.
- **Open in Noise Removal**: Start cleanup flow.
- **Open in EQ**: Start EQ flow.

> **Note:** Analyze does not change audio; it is inspection only.

## Common pitfalls
- If metrics are missing, re‑analyze the source or verify ffmpeg availability.

## Tips
- Use Analyze before mastering to catch issues early.

## Screenshot placeholders
- [Screenshot: Analyze metrics]
- [Screenshot: Analyze spectrogram]

<details>
<summary>Technical Details</summary>

- **Metrics pipeline**: `ffprobe` for duration/sample rate/channels; `ffmpeg` `ebur128` for LUFS/LRA/TP; `ffmpeg` `astats` for peak/RMS/crest/noise.
- **Spectrogram**: `GET /api/analyze/spectrogram` wraps ffmpeg `showspectrumpic`.

Example spectrogram command:
```bash
ffmpeg -y -hide_banner -loglevel error \
  -i input.wav \
  -lavfi "showspectrumpic=s=1200x256:mode=combined:scale=log:legend=disabled:color=viridis:drange=120" \
  -frames:v 1 out.png
```

</details>
