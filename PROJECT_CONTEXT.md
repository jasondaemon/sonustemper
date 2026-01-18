# docs/PROJECT_CONTEXT.md
# SonusTemper – Project Context (Reference)

This document provides **deep architectural and operational context** for SonusTemper.
It is intended for **reference and lookup**, not as a source of rules or constraints.

Authoritative constraints live in:
- `docs/CODEX_RULES.md`
- `docs/PROJECT_MEMORY.md`

---

## What SonusTemper Is

SonusTemper is a **local-first audio mastering, analysis, tagging, and file management tool**.

- Backend: **FastAPI (Python)**
- UI: **Server-rendered Jinja2 + HTMX**
- No SPA framework
- Browser-based primary UX
- Native desktop builds exist for macOS and Windows

Legacy UI has been removed.  
The only UI is the **sonustemper-ui** application.

---

## High-Level Architecture

- FastAPI server provides:
  - API endpoints
  - UI routes
  - SSE streams
  - Static asset serving
- Docker runs the app behind an nginx proxy:
  - Basic Auth
  - Shared-secret header injection
- The same backend runs in:
  - Docker (behind proxy)
  - Local Python
  - Native desktop builds (PyInstaller)

The backend is shared across all environments.

---

## Native Desktop Application (Reference)

### Runtime Behavior
- Built with **PyInstaller**
- Starts FastAPI locally on `127.0.0.1`
- Auto-selects an available port (default range 8383–8433)
- Opens UI in the user’s default browser

#### macOS
- Menu bar app
- Menu items: “Open SonusTemper”, “Quit SonusTemper”
- Dock icon is preserved (not a pure agent app)

#### Windows
- Background process
- Browser opens automatically
- No tray/menu integration

---

## Native Entrypoint

- `sonustemper/desktop_main.py`
  - Manages Uvicorn lifecycle using `uvicorn.Server`
  - Handles port detection and browser launch
  - macOS menu bar via `rumps`
  - Windows fallback loop
  - Loads bundled resources via `sys._MEIPASS`
  - Sets OS-specific `DATA_DIR` defaults when unset

---

## Native Build Constraints (Reference)

Changes that **do not** require spec updates:
- UI templates
- CSS / JS
- Existing Python logic

Spec changes are required only when:
- Adding new Python dependencies
- Introducing new external binaries/resources
- Adding new dynamic imports

---

## Native Build Layout

- Build output:
  - macOS: `build/
