# docs/PROJECT_MEMORY.md
# SonusTemper – Project Memory (Authoritative)

This file defines the **intent, philosophy, and invariants** of SonusTemper.
It explains *why* the system is built the way it is.

---

## Core Intent

SonusTemper is a **local-first, deterministic audio mastering and analysis tool**.

It prioritizes:
- correctness over cosmetic polish
- determinism over AI-driven variability
- transparency over hidden “magic”

The goal is repeatable, explainable mastering — not black-box automation.

---

## Foundational Invariants

- The **FastAPI backend is the single source of truth**.
- The UI must reflect backend state, never invent or “fix” it.
- Audio processing is driven by **explicit presets and pipelines**, not inference.
- The same codebase must behave consistently across:
  - Docker
  - local Python
  - native desktop builds

---

## Design Philosophy

- **Reuse over creation**  
  Extend existing templates, partials, endpoints, and patterns whenever possible.

- **Minimalism over abstraction**  
  Prefer simple, explicit logic to clever or generalized frameworks.

- **Determinism over dynamism**  
  Given the same input + preset, the output should be the same.

- **Real data over visual convenience**  
  Incorrectly “pretty” visuals are worse than imperfect but accurate ones.

---

## Audio & Analysis Principles

- Loudness uses a **two-pass static workflow** (measure → fixed gain).
- No time-varying loudness normalization unless explicitly changed.
- Analysis results must be traceable and reproducible.
- Variant naming and tagging are deterministic and meaningful.

---

## Persistence Model

- **Anything that is not an audio file belongs in the database**.
- The database is the authoritative record for:
  - runs and job history
  - analysis metrics
  - preset metadata and documentation
  - provenance and relationships
- Sidecar JSON files are export artifacts, not the primary state store.

---

## Stability Over Novelty

- Regressions are worse than missing features.
- Architecture changes require explicit instruction.
- “Helpful” refactors are not improvements unless requested.
- When unsure, stopping to ask is preferred to guessing.

---

## Scope Boundary

SonusTemper is:
- a mastering, analysis, and file-management utility

It is not:
- a DAW
- a real-time audio editor
- an AI composition or generation tool

---

## Change Ethos

If a change:
- breaks determinism
- hides real audio behavior
- duplicates existing structures
- or increases complexity without clear benefit

It likely violates project intent.
