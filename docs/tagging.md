# Tagging (ID3)

## 🧭 Table of contents
- [What it does](#what-it-does)
- [When to use it](#when-to-use-it)
- [Step-by-step](#step-by-step)
- [Controls and functions](#controls-and-functions)
- [Common pitfalls](#common-pitfalls)
- [Tips](#tips)
- [Screenshot placeholders](#screenshot-placeholders)

## What it does
The Tagging utility lets you edit ID3 metadata and album art for MP3 files, either from the Library or from standalone uploads that do not enter the Library.

## When to use it
- You need consistent album/artist/title metadata across a release.
- You want to attach album artwork.
- You have MP3s from mastering or other utilities and want to tag them before delivery.

## Step-by-step
1) Open **Tagging** from the Utilities menu.
2) Add MP3s:
   - From **Library**: select a version that already has an MP3 rendition, or use **Convert to MP3**.
   - From **Standalone Tagging**: upload MP3s (not added to the Library).
3) Edit **Album Details** (album, artist, year, genre, artwork).
4) Edit per‑track **Title/Artist/Track** fields as needed.
5) Click **Apply** to write tags, then download tracks or a full album ZIP.

## Controls and functions

### Standalone Tagging (MP3 only)
- **Upload MP3s**: Uploads files into a temporary session (not added to the Library).
- **Add All**: Adds all temp uploads into the editor.
- **Clear Temp**: Deletes temp files for the current session.

![standalone](img/tagging-standalone.png)

### Library list
- **Convert to MP3**: Creates an MP3 rendition when only WAV/FLAC exists.
- **Tag/Open**: Adds the MP3 rendition to the editor.

### Editor: Album Details
- **Album/Album Artist/Artist**: Shared fields across tracks.
- **Year/Genre/Disc/Comment**: Additional metadata fields.
- **Artwork**: Upload or clear cover art. If all tracks share the same artwork, it will show as consistent.

### Editor: Tracks table
- **Track**: Track number per file (compact input).
- **Title/Artist**: Per‑track overrides.
- **Download icon**: Downloads the tagged file.
- **Remove (X)**: Removes the track from the editor.

![tracks](img/tagging-tracks.png)

## Common pitfalls
- **No MP3 available**: Some versions only have WAV/FLAC. Use **Convert to MP3** first.
- **Tags not saved**: Click **Apply** to write tags before downloading.
- **Temp files missing after restart**: Temp uploads are cleaned up by preview cleanup TTL.

## Tips
- Set album fields first, then adjust track‑specific titles.
- Use **Add All** to bring all temp uploads into the editor quickly.
- Keep track numbers short (e.g., 1–12) so titles have more space.

## Screenshot placeholders
- [Screenshot: Tagging main page]
- [Screenshot: Standalone Tagging section]
- [Screenshot: Tracks editor with download/remove]

<details>
<summary>Technical Details</summary>

- **Library MP3 conversion**: `POST /api/tagger/ensure-mp3` uses ffmpeg to create an MP3 rendition if missing:
  ```bash
  ffmpeg -y -i input.wav -vn -sn -dn -codec:a libmp3lame -b:a 320k output.mp3
  ```
- **Standalone uploads**: `POST /api/tagger/upload-mp3` stores files in:
  - `DATA_ROOT/previews/mp3-temp/<session>/`
- **Temp cleanup**:
  - Files under `previews/` are auto‑cleaned after `PREVIEW_TTL_SEC`.
  - Manual cleanup via **Clear Temp** calls `POST /api/tagger/temp-clear`.
- **Tag writes**:
  - Album: `POST /api/tagger/album/apply`
  - Per‑file download: `GET /api/tagger/file/{id}/download`
- **Security**: Tagging endpoints validate paths under the safe previews/library roots.

</details>
