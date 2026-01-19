# AI Toolkit

## What it does
AI Toolkit provides real‑time cleanup controls with recommendations derived from track analysis. It uses WebAudio for preview and FFmpeg for final renders.

## When to use it
- Fast cleanup of hiss, harsh vocals, rumble, transients, and platform loudness.
- Beginners who want safe, guided adjustments.

## Step-by-step
1) Load a song from the Library.
2) Review recommendations and apply what you want.
3) Enable tools and adjust sliders in real units.
4) Save a cleaned copy to create a new version.

## Common pitfalls
- If recommendations show "unavailable," check the detect endpoint and logs.
- If audio is silent, confirm the audio engine is running.

## Tips
- Tools are off by default. Enable only what you need.
- Loudness slider is a target; final save uses FFmpeg loudness.

## TODO
- Add a mapping table from findings to recommended values.
