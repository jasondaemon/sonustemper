# Library

## What it does
The Library organizes songs and versions in a single, song‑centric index backed by SQLite. A song has one source file and multiple versions created by utilities.

## When to use it
- Importing songs and tracking outputs.
- Managing versions across utilities.
- Deleting or downloading outputs.

## Step-by-step
1) Open Song Library.
2) Import a song (upload or scan/import).
3) Select a song to open it in a utility.
4) Save outputs to create versions.

## Common pitfalls
- If a song does not appear, confirm the import succeeded and the DB is writable.
- If versions are missing, verify the save path and library DB location.

## Tips
- Sources and versions are separate. Keep the source intact.
- Use the latest version for Compare.

## TODO
- Document library sync and import scanning.
