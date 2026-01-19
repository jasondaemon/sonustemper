# Troubleshooting

## What it does
Common fixes for playback, rendering, and library issues.

## When to use it
- Audio is silent.
- Library does not update.
- Presets do not appear.

## Step-by-step
1) Check logs for errors (server and UI).
2) Verify /data and /db mounts are correct.
3) Confirm ffmpeg/ffprobe are available.
4) Reload the page and Library.

## Common pitfalls
- Browser blocks AudioContext until a user gesture.
- SQLite on NFS can cause stalls; ensure /db is local.

## Tips
- Enable debug flags for EQ and AI Toolkit to surface issues.

## TODO
- Add a log collection checklist.
