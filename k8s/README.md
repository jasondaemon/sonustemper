# Kubernetes Deployment

SonusTemper can be deployed to K8s with the same app + proxy pattern used by Docker Compose.

## Layout

- `base/` contains the shared manifest set
- `overlays/dev/` deploys the dev namespace, dev host, and `dev` image tag
- `overlays/prod/` deploys the production namespace
- `k8s/` points to the prod overlay, so `kubectl apply -k k8s/` is the production default

## What this bundle includes

- `Deployment` with the app container and nginx proxy container
- `Service` for the proxy
- `Ingress` with a placeholder hostname
- `PersistentVolumeClaim` for `/data`
- `emptyDir` for `/db`
- ConfigMap generated from the bundled proxy scripts/templates

## Before you apply

1. Create the `sonustemper-auth` secret in the target namespace from [base/secret.example.yaml](base/secret.example.yaml).
2. Replace the placeholder host in the overlay you plan to use.
3. If your cluster needs a specific ingress class, add `ingressClassName` in the overlay.
4. If your cluster needs a specific storage class, edit `base/pvc-data.yaml` or patch it in an overlay.

## Apply

Dev:

```sh
kubectl apply -k k8s/overlays/dev
```

Prod:

```sh
kubectl apply -k k8s/overlays/prod
```

Or, for the production default:

```sh
kubectl apply -k k8s/
```

## Branch workflow

1. Develop on feature branches.
2. Merge feature branches into `dev`.
3. Deploy `k8s/overlays/dev` for testing.
4. Merge `dev` into `main` when ready.
5. Deploy `k8s/overlays/prod` or `k8s/` for production.

## Notes

- The proxy container waits for the upstream target to resolve before it starts. In K8s the manifest points that at `127.0.0.1`, so the app and proxy stay in the same pod without extra DNS dependencies.
- The public repo intentionally avoids live cluster IPs, hostnames, and secret names beyond the application-specific object names.
- If you want a TLS-less local deployment, remove the `tls:` block from the ingress patch before applying.

## Album Studio development

The container build compiles the Svelte frontend. For a source-tree or native
build, run `npm ci --prefix frontend` and `npm run build --prefix frontend` first.
The generated static files are served at `/albums` and included by the existing
native static asset rules.

Set `SONUSTEMPER_ALBUM_LOCATIONS` to a JSON object mapping names to mounted
directories, for example `{"Music Production":"/music-production"}`. In Compose,
set it in `.env` and add a bind mount with a Compose override. In Kubernetes,
set it on the app container and add the corresponding volume mount. Each root
contains artist folders, then album folders. The numbered audio folders are
`00 - raw`, `01 - mastered`, `02 - clean`, `03 - normalized`, and `04 - mp3s`.
Legacy names still work; new output folders use the numbered names.
The album browser reads the share directly. Refresh after adding or removing files.

**Organize album folders** renames the selected album's legacy stages in place.
If there is no raw or mastered folder, WAVs directly in an album folder appear
as raw on browse; organizing the album or starting a review or direct publish
moves them to `00 - raw`. Browsing alone does not move any files. Artwork, stems,
prompts, and lyrics stay where they are. Conflicting stage directories are
never merged or overwritten.

For bulk migration, POST `{"location":"name","dry_run":true}` to
`/api/albums/organize-location`. Check `albums` and `errors` before sending
`dry_run:false`. Keep the response with the applied move lists as a rollback
receipt. If needed, with the library idle and before adding more files, pass an
album path and its applied `moves` to
`sonustemper.album_layout.rollback_layout(Path(album_path), moves)`.
It reverses moves in order and refuses to overwrite an occupied destination.

Set `SONUSTEMPER_READ_ONLY_ALBUM_LOCATIONS` to a JSON array of names to disable
exports and lyric edits there. Mount the source share read-only in dev and use a
writable test copy for renders. The app UID needs read and traverse permissions
on the NAS. Keep `SONUSTEMPER_LIBRARY_DB` on a persistent volume local to the
app host for saved sessions, job history, and export recovery. Keep a single app
replica when sharing that database.

Processing edits follow each song and save automatically in this browser and
on the server. In **Tone & Stereo**, **Apply to full album** shares the current
EQ and stereo settings across all songs. Uncheck it to make further edits on
just the selected song without changing the tone already set on the others.
Cleanup and noise selections stay per song. The loudness settings and export selection cover the
album, with either shared gain or per-track normalization. **Tags and artwork**
shows every song and offers 3000 x 3000 images from the album folder. Under
**Finalize album**, **Publish album** renders and publishes in one step.
**Review first** is optional and stages a full render without replacing
delivery files. Play **Reviewed WAV** in the main player, optionally match it to
**Original** for listening only, or click a song in the review table. Reviewed
playback advances through the album. The review shows measured album loudness,
per-song gain, final WAV and decoded MP3 true peaks, and the approximate
location of any peak that capped the gain.

**Publish reviewed album** moves those same files to `01 - mastered/` (when
starting from raw), `02 - clean/`, `03 - normalized/` and `04 - mp3s/` or
their existing legacy equivalents. A change to the source, album
settings, cover, or lyrics invalidates the review. Publish the current settings
directly or prepare a fresh review. Reviews require a writable
location for staging and are available for up to four hours. Raw recordings
are untouched. **Update MP3 tags only** updates tags, cover art, and lyrics on
previously published MP3s without re-encoding them.
Album jobs lock the workspace in a modal while running. The bar counts
completed work steps and shows the current operation and song below it.
On success the modal becomes a standalone album playlist with cover art and
WAV playback. Closing it stops playback and returns to the workspace.
In the song list, a green check means a final WAV and its matching MP3
are present. A dim check means the pair is incomplete. Intermediate clean
WAVs are not required for this status.

Starting from `00 - raw/` bakes enabled cleanup, EQ, and noise removal into the audio
before album loudness normalization. The final 24-bit WAV is written to a fresh
container without source tags or ancillary chunks (including Suno metadata).
The MP3 is encoded from that WAV and receives only the delivery metadata.

**Cleanup & repair** analyzes a full track and marks measured frequency and
sample-peak hints beneath its waveform. Hints do not apply processing. Approved
whole-song or time-range fixes have separate timeline lines and response curves.
Findings and applied fixes share one review list. Expand a row to adjust the
cut, inspect its response curve, or switch that repair's Before/After preview
in the main player without adding another transport. The full-song scan can
also flag steady fuzz in quiet gaps and persistent narrow tones. These
spectrogram suggestions need individual review and are not included in
**Apply all EQ**. Open a time-scoped row for a linear spectrogram of that
passage. Refine a spectral finding's box and reduction directly in its
row; use the full **Noise removal** panel to create manual selections.
New boxes use spectral attenuation. **Sample noise
in this box** uses a measured profile from the selection; **Find similar
fuzz** scans the full song for other quiet passages with a matching profile.
Existing saved EQ-style band cuts keep their original behavior.
**Before**, **After**, and **Removed** compare just that selection at the same
gain through the main player. **Removed** plays the difference between
the two renders so you can check for wanted audio in the removed signal.
**Loop selection** repeats the same span while switching between the three.
Preview a finding at the suggested cut before approving it; the matched
before/after audition changes only that fix. **Apply all** adds uncovered
findings at the current cut, leaves skipped findings, peak warnings, and existing fixes alone, and
offers an undo. Under **Batch audition**, **Preview all** and **Compare applied suggestions** render a
seekable, loudness-matched pair for the full song without writing to the album.
**Apply all** also prepares a temporary WAV for **Post Cleanup** playback.
Select **Post Cleanup** in the main player to prepare or refresh it after edits.
It includes enabled cleanup and noise removal, but no creative EQ or album
loudness. **Live Monitor** adds live EQ and stereo adjustments to that WAV (or
to **Original** when no current cleanup render is available).
**Tone & Stereo** offers **Clean** (subsonic cut only), **Clarity** (less low-mid
haze, more vocal presence), **Punch** (bass weight and preserved attacks),
**Warm** (more body, softer highs), and **Wide** (more side level; check in
Mono). **50% strength** is the reference sound for these five presets.
The range above 50% pushes EQ, dynamics, and width further, with the width
control still capped at 1.50. Low cuts stay at their selected frequency above
0%; 0% bypasses the preset completely. Saved settings keep the same sound.
**Custom** starts with the seven-band EQ template. To keep the current
mastering preset as a starting point, choose **Customize current preset**.
To reuse your own tone, name it and choose **Save preset**.
Select it from **Saved in this browser**, or use its **×** to delete it without
changing albums where it was already applied. Names and the reusable preset
list stay in browser storage. Applying one saves the actual processing settings
with the album as usual, not the preset name. Clearing this site's browser
data removes the list.
Legacy server presets are no longer offered for new selections in Tone &
Stereo. Sessions that already use one still render unchanged. The original
voicings, including Glue, remain in the separate preset library.

Use **Compare Preset** in **Tone & Stereo** to prepare **Preset Off** and **Preset On** in the main player
at matched loudness. It switches EQ, preset compression, and width together,
keeping cleanup identical. **Compare EQ** for custom EQ without a preset
switches just the EQ. The headroom readout shows true peaks before the listening
gain. Neither comparison writes album audio or changes album loudness settings.
Drag a range on the cleanup timeline to loop it in the comparison. Live Monitor
uses EQ and width only; use **Compare Preset** to hear preset compression
before album loudness normalization.
**Protect against clipping** is an optional, stereo-linked peak limiter for
Live Monitor, enabled by default. It keeps live samples below -1.5 dBFS when
EQ or width boosts would cause overload. It does not change Original, Post
Cleanup, comparisons, or files. Final WAVs still follow the album true-peak
ceiling; decoded MP3 peaks are reported separately.
The player meter also shows three-second short-term LUFS and an estimated
monitor true-peak hold. Click the true-peak readout to reset it. **Mono**
folds playback only; the correlation meter always uses the stereo signal
before that fold. **Measure full songs** under **Album loudness** measures
integrated LUFS and loudness range on the actual source and final WAV files.
Playback, loops and unrendered processing changes do not affect that summary.
Red marks on the track waveform show where sample clipping was heard in the
current monitor playback, not the signal level at the playhead. The L/R clip
lights stay latched; click their reset button to clear those marks and lights.
New sessions
run noise removal and repairs before **Tone & Stereo** (presets or custom EQ),
then measure loudness for delivery. Older sessions retain their original order
until explicitly upgraded.

**Noise removal** has a zoomable log/linear spectrogram and full-song scrub
overview. Save several time/frequency regions or whole-song bands for each
track. Enabled entries run in list order, including when auditioning the
filtered track. Solo previews only the selection being edited. Album-wide
settings copy whole-song bands but keep time ranges on their own tracks.
Linear shows the whole 0–24 kHz range with linear intensity; filters still use
20 Hz–20 kHz. Log emphasizes quieter detail.

**Prompts and Lyrics** reads and saves song files on the share. The two prompt
editors share `styles/<song>.md`: style text comes first, followed by an optional
`## Negative Prompt` section. Original lyric prompts stay in `lyric-prompts/`
as Markdown. Saving reviewed submission lyrics writes `lyrics/<song>.txt`
for copy/paste into DistroKid and `lyrics/<song>.lrc` for untimed playback.
Publishing also embeds the text in the MP3 tags and writes a matching
`.lrc` next to each MP3. Publish or use **Update MP3 tags only** to fill in
missing `.lrc` companions for older lyrics. Existing hand-authored companions
are not overwritten. Songs without saved lyrics do not get generated lyrics.
