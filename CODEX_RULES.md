# docs/CODEX_RULES.md
# SonusTemper – Codex Rules (Authoritative)

These rules are **non-negotiable**.  
If a request conflicts with these rules: **STOP and ask before making changes.**

---

## 0) Required Workflow (always)

Before writing or modifying code, Codex must:

1. Identify the **exact files** that will change
2. Explain how the solution **reuses existing patterns**
3. Confirm **no rule below is violated**
4. Apply the **smallest possible diff**

---

## 1) Reuse-First Policy (highest priority)

- **Reuse existing code before creating anything new**
- Do NOT create new pages, partials, components, or endpoints unless:
  - explicitly instructed, or
  - reuse is demonstrably impossible

Before adding anything new, search for reuse in:
- `sonustemper-ui/app/templates/`
- `sonustemper-ui/app/templates/partials/`
- `sonustemper-ui/app/static/js/`
- `sonustemper-ui/app/ui.py`
- `sonustemper/server.py`
- existing `/api/*` endpoints

If reuse is not possible, Codex must explain **why**, and propose the **smallest** new artifact consistent with existing structure.

---

## 2) UI Architecture Rules (Jinja2 + HTMX)

- UI is **server-rendered** using **Jinja2 + HTMX**
- No SPA framework, no client-side state duplication
- Pages are composed of **templates → partials**
- Do not duplicate markup across pages
- Extract or reuse partials instead of copying
- Minimal JavaScript only when required (e.g., waveform interaction)

HTMX requests must return partials, not full pages.

---

## 3) Backend & API Authority

- FastAPI backend is the **single source of truth**
- UI must not invent parallel logic or state
- Prefer existing API endpoints
- Any new endpoint must:
  - follow existing naming conventions
  - match request/response patterns
  - include proper auth, logging, and error handling

---

## 4) Data Persistence Rule (DB-first)

- **Anything that is not an audio file must be persisted in SQLite**, including:
  - jobs/runs and statuses
  - analysis metrics (ALL metrics, even if UI shows only some)
  - preset metadata and documentation content
  - provenance and history
- Do not store critical state only in memory
- JSON sidecars (`.metrics.json`, `.run.json`) are export artifacts; DB remains authoritative unless explicitly stated otherwise

---

## 5) Audio, Waveform, and Analysis Integrity

- Do NOT normalize, clamp, center, or cosmetically adjust audio or waveforms unless explicitly instructed
- Visuals must reflect **real signal data**
- False visual correctness is worse than imperfect accuracy
- Loudness workflow remains deterministic (measure → fixed gain)
- Preserve all analysis outputs and provenance

---

## 6) Change Discipline (anti-thrashing)

- Prefer minimal diffs
- Do NOT refactor architecture unless explicitly requested
- Do NOT rename files, variables, or functions unless required
- Do NOT reformat unrelated code
- Avoid “while I’m here” improvements

---

## 7) File & Repo Safety

Do NOT modify:
- build outputs (`dist/`, packaged artifacts)
- media/audio files (`.wav`, `.mp3`, etc.)
- vendored binaries

If a change requires touching these: **STOP and ask**.

---

## 8) Native Build Constraints (PyInstaller)

- UI/template/CSS/JS and existing Python logic changes are generally safe
- Spec file changes only if:
  - adding a new Python dependency
  - adding new external binaries/resources
  - adding new dynamic imports
- Do NOT alter packaging strategy unless explicitly requested

---

## 9) Conflict Resolution Order

If instructions conflict, follow this priority:

1. `docs/CODEX_RULES.md`
2. `docs/PROJECT_MEMORY.md`
3. User prompt
4. Codex assumptions or optimizations

---

## 10) Confirmation Requirement

Before applying changes, Codex must confirm:
- files to be changed
- reuse-first compliance
- DB persistence compliance (if state/metadata involved)
- minimal-diff compliance

Then apply the patch.

## Security Invariants (Non-Negotiable)

- SonusTemper is **single-tenant** and **proxy-fronted**
- All non-local access is expected to flow through the bundled nginx proxy
- The application service must not assume direct public exposure
- The UI does not use API keys
- Authentication and perimeter behavior must not be weakened or bypassed

If a change affects authentication, proxy headers, network exposure,
or access control, Codex must **consult SECURITY.md** and STOP to confirm intent.

## Native Build Invariants (PyInstaller)

- Native builds use **PyInstaller**
- UI templates, CSS, JS, and existing Python logic are safe to change
- The PyInstaller spec file must NOT be modified unless:
  - a new Python dependency is added
  - a new external binary/resource is introduced
  - new dynamic imports are required
- Codex must not:
  - add dependencies casually
  - move resource paths
  - change entrypoints
  - alter packaging strategy

If a change may affect native builds, Codex must STOP and ask.
.