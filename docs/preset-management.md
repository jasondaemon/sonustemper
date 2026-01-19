# Preset Management

## 🧭 Table of contents
- [What it does](#what-it-does)
- [When to use it](#when-to-use-it)
- [Step-by-step](#step-by-step)
- [Controls and functions](#controls-and-functions)
- [Common pitfalls](#common-pitfalls)
- [Tips](#tips)
- [Screenshot placeholders](#screenshot-placeholders)

## What it does
Preset Management lets you create and manage voicings, profiles, and noise presets. Presets are stored on disk and appear across utilities.

## When to use it
- Create voicing/profile from reference audio.
- Manage user presets for mastering and noise removal.

## Step‑by‑step
1) Open **Preset Management**.
2) Use **Create From Reference Audio** to generate a voicing/profile.
3) Upload or delete presets as needed.
4) Confirm presets appear in Mastering and Noise Removal.

## Controls and functions

### Create From Reference Audio
- **Upload audio**: Provide a reference track.
- **Generate**: Creates a voicing and/or profile.
- **Status**: Reports generated items and any errors.

### Preset Library lists
- **User Voicings**: Custom mastering voicings.
- **User Profiles**: Loudness / profile presets.
- **User Noise Presets**: Noise Removal presets.
- **Search**: Filter lists by name.

### Actions
- **Delete**: Removes the preset file from disk.
- **Upload**: Add a preset JSON manually.

> **Warning:** Deleting a preset removes it from all utilities.

## Common pitfalls
- If presets do not show, check preset directory paths and UI preset root.
- If generation reports success but lists are empty, reload the preset list.

## Tips
- Keep preset names consistent for easier selection.

## Screenshot placeholders
- [Screenshot: Preset library]
- [Screenshot: Create From Reference Audio]

<details>
<summary>Technical Details</summary>

- **Preset storage**: User presets live under `/data/user_presets/` with subfolders:
  - `voicings/`
  - `profiles/`
  - `noise_filters/`
  - `ai_tools/`
- **Create From Reference**: Uses `ffprobe` for duration/sample rate and ffmpeg analysis (loudness/astats) to derive voicing + profile JSON.

Example analysis commands:
```bash
ffmpeg -y -hide_banner -loglevel error -i ref.wav \
  -af "ebur128=peak=true" -f null -
```

```bash
ffmpeg -y -hide_banner -loglevel error -i ref.wav \
  -af "astats=measure_overall=Peak_level+RMS_level+RMS_peak+Noise_floor+Crest_factor" \
  -f null -
```

- **UI lists**: Preset Management reads directly from these folders; there is no DB for presets.

</details>
