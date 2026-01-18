Before making changes:

1. Read docs/CODEX_RULES.md (authoritative constraints)
2. Read docs/PROJECT_MEMORY.md (intent and invariants)
3. Use docs/PROJECT_CONTEXT.md only as reference
4. If a change touches native builds, packaging, or entrypoints:
   - consult PROJECT_CONTEXT.md
   - STOP and confirm before modifying spec files or dependencies
5. Reuse existing patterns whenever possible

Confirm understanding before applying changes.

#Anchor check: reuse-first, DB-first, minimal diffs.