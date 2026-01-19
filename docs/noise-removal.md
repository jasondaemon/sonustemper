# Noise Removal

## 🧭 Table of contents
- [What it does](#what-it-does)
- [When to use it](#when-to-use-it)
- [Step-by-step](#step-by-step)
- [Controls and functions](#controls-and-functions)
- [Common pitfalls](#common-pitfalls)
- [Tips](#tips)
- [Screenshot placeholders](#screenshot-placeholders)

## What it does
Noise Removal isolates and reduces unwanted noise using presets or a marquee selection. It supports previewing solo noise vs filtered song and saves versioned outputs.

## When to use it
- Hiss, hum, and background noise cleanup.
- Targeted cleanup in specific time regions.

## Step‑by‑step

### Preset Noise Removal (full song)
1) Select a song.
2) Choose a preset from the dropdown.
3) Preview the result.
4) Save the cleaned song (creates a new version).

### Selected Noise Removal (marquee)
1) Drag a selection on the spectrogram.
2) Choose **Solo Noise** or **Filtered Song** audition.
3) Adjust settings and preview.
4) Save the cleaned song (creates a new version).
5) Save as Preset if you want to reuse the settings.

## Controls and functions

### Inspection & diagnostics
- **Metrics**: LUFS‑I, TP dBTP, LRA, crest, RMS, DR, noise floor.
- **Spectrogram**: Frequency‑time view for locating hiss, hum, or broadband noise.
- **Waveform**: Time‑domain context for selection placement.

### Preset workflow (left)
- **Noise Filter Preset**: Select a preset (e.g., Gentle Denoise).
- **Filter depth (dB)**: How much reduction to apply.
- **Denoise strength**: How aggressive the reduction is.
- **High‑pass / Low‑pass**: Optional bounds on the processing.
- **Preview**: Audition preset settings on the full song.
- **Save Song**: Renders a cleaned version.

### Selected workflow (right)
- **Marquee selection**: Select time + frequency region.
- **Audition toggle**: Solo Noise vs Filtered Song.
- **Apply scope**: Global or Selection.
- **Auto play selection**: Auto‑plays preview when selection changes.
- **Preview**: Audition selected settings.
- **Save Song**: Renders a cleaned version.
- **Save as Preset**: Stores current settings as a user preset.

### Selection readout
- Shows time range, frequency range, bandwidth, and clear selection action.

> **Note:** Preset workflow applies across the full song; Selected workflow applies only within the marquee.

## Common pitfalls
- If no selection exists, “Apply to Selection” does nothing.
- If preview is silent, check selection bounds and audition mode.
 - If metrics are missing, re‑analyze the source or verify ffmpeg availability.

## Tips
- Use Preset workflow for quick global cleanup.
- Use Selected workflow for tight noise regions.
 - Use the spectrogram to spot narrowband noise and place marquee accurately.

## Screenshot placeholders
- [Screenshot: Preset Noise Removal]
- [Screenshot: Selected Noise Removal with marquee]
- [Screenshot: Save as Preset]

<details>
<summary>Technical Details</summary>

- **Core filter chain**: `_noise_filter_chain` builds either a band‑pass (`highpass` + `lowpass`) for **Solo Noise** or a mid‑band cut (`equalizer`) for **Filtered Song**. Optional `highpass`/`lowpass` bounds and `afftdn` are appended.
- **Selection apply**: For selection‑only, ffmpeg uses `asplit` + `volume` expressions to fade wet in/out and `amix` to combine dry/wet.
- **Spectrogram**: `GET /api/analyze/spectrogram` wraps ffmpeg `showspectrumpic` for the visual.

Example remove chain (global):
```bash
ffmpeg -y -hide_banner -loglevel error -i input.wav \
  -af "equalizer=f=2800:t=q:w=1.4:g=-18,highpass=f=70,lowpass=f=16000,afftdn=nr=12:nf=-25" \
  output.wav
```

Example selection apply (conceptual):
```bash
ffmpeg -y -hide_banner -loglevel error -i input.wav \
  -filter_complex "[0:a]asplit=2[dry][wet];[wet]equalizer=f=2800:t=q:w=1.4:g=-18[wetf];[wetf]volume='WET_EXPR'[wetv];[dry]volume='DRY_EXPR'[dryv];[dryv][wetv]amix=inputs=2:normalize=0[out]" \
  -map "[out]" output.wav
```

</details>
