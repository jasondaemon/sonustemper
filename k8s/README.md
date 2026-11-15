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
contains artist folders, album folders, and `raw/` or `mastered/` WAVs. The album
browser reads those directories directly. Refresh after adding or removing files
on the share.

Set `SONUSTEMPER_READ_ONLY_ALBUM_LOCATIONS` to a JSON array of names to disable
exports and lyric edits there. Mount the source share read-only in dev and use a
writable test copy for renders. The app UID needs read and traverse permissions
on the NAS. Keep `SONUSTEMPER_LIBRARY_DB` on a persistent volume local to the
app host for saved sessions, job history, and export recovery. Keep a single app
replica when sharing that database.

Processing edits follow each track and are saved in this browser. **Apply
processing to all tracks** sets album defaults. **Save album session** also saves
the draft on the server. The loudness settings and export selection cover the
album, with either shared gain or per-track normalization. **Tags and artwork**
shows every song and offers 3000 x 3000 images from the album folder. Under
**Finalize album**, **Process album** runs FFmpeg on all songs and publishes the
complete set to `mastered/` (when starting from raw), `clean/`, `normalized/`
(final WAVs), and `mp3s/` (final MP3s and optional lyrics files). Matching outputs
are replaced only after the full render; the source recordings and unrelated
files stay untouched. **Update MP3 tags only** updates tags, cover art, and
lyrics on previously published MP3s without re-encoding them.

Starting from `raw/` bakes enabled cleanup, EQ, and noise removal into the audio
before album loudness normalization. The final 24-bit WAV is written to a fresh
container without source tags or ancillary chunks (including Suno metadata).
The MP3 is encoded from that WAV and receives only the delivery metadata.

**Cleanup & repair** analyzes a full track and marks measured frequency and
sample-peak hints beneath its waveform. Hints do not apply processing. Approved
whole-song or time-range fixes have separate timeline lines and response curves.
The matched before/after audition changes only the selected fix. New sessions
run noise removal and repairs before **Tone & Stereo** (presets or custom EQ),
then measure loudness for delivery. Older sessions retain their original order
until explicitly upgraded. Applying settings to the album leaves each song's
time-range fixes on that song.

**Noise removal** has a zoomable log/linear spectrogram and full-song scrub
overview. Save several time/frequency regions or whole-song bands for each
track. Enabled entries run in list order, including when auditioning the
filtered track. Solo previews only the selection being edited. Album-wide
settings copy whole-song bands but keep time ranges on their own tracks.

**Prompts and Lyrics** reads and saves song files on the share. The two prompt
editors share `styles/<song>.md`: style text comes first, followed by an optional
`## Negative Prompt` section. Original lyric prompts stay in `lyric-prompts/`
as Markdown. Reviewed submission lyrics go in `lyrics/` as plain text, also
used for MP3 tags and optional lyric sidecars.
