# AGENT.md

Repository-specific editing instructions for coding agents working on Guaardvark.
This is the agent-operating doc; human overview, architecture, and setup live in
`README.md`. Deep agent-facing sharp edges live in `KNOWLEDGE.md`. The authoritative
long-form agent guidance already exists in `AGENTS.md` (with architecture, control
flow, and safety systems) — read both.

## Before editing

1. Scan and classify the repository. Guaardvark spans multiple project types; load
   **every** applicable repository-local project-type file **before** making changes:

   - `.codex-ready/project-types/python-api.md`
   - `.codex-ready/project-types/frontend-react.md`
   - `.codex-ready/project-types/cli-python.md`
   - `.codex-ready/project-types/gpu-plugins-ml.md`
   - `.codex-ready/project-types/monorepo.md`

2. Read `AGENTS.md` and `KNOWLEDGE.md` (consult `KNOWLEDGE.md` again on later edits so
   you don't rediscover the same sharp edges). Read `KNOWN_BUGS.md` to see what is
   already known before you touch that area.
3. Reproduce the environment: `source backend/venv/bin/activate` and export
   `GUAARDVARK_ROOT=$(pwd)`.

## Rules

- Work step by step; make the **smallest change** that satisfies the request.
- Preserve existing code and structure. Do not delete working code except for
  user-approved bug fixes or unavoidable refactors.
- Ask the user when code intent is unclear.
- Ask the user **before editing existing source code** when the task can begin with
  docs, tests, or scaffolding first.
- When bugs are found during inspection or testing, write them to `KNOWN_BUGS.md`
  **before** attempting fixes, unless the user explicitly asks you to fix them.
- Keep `KNOWLEDGE.md` updated with discoveries; don't let the same investigation be
  repeated.

## Per-edit testing

- Run fast, local unit/component tests for the important functions you touched during
  each edit cycle. Backend: `python3 -m pytest backend/tests/<file>` (with
  `GUAARDVARK_MODE=test DISABLE_CELERY=true`). Frontend: `cd frontend && npm run
  test`. Lint: `npm run lint` (frontend) and `scripts/lint.sh` (backend).
- Replicate test env vars exactly; see `KNOWLEDGE.md` for the gotchas.

## End-to-end testing

- Run end-to-end / browser-level tests **separately**, after a coherent batch of work,
  not after every small edit.
- Use end-to-end results to update `KNOWN_BUGS.md`.
- Ask the user before filling in full end-to-end implementations or before treating
  dummy/seeded data as canonical.

## Handoff checklist

- Confirm the generated guidance still works on the repo after your edits.
- Confirm doc split: big picture in `README.md`, agent details in `KNOWLEDGE.md`,
  bugs in `KNOWN_BUGS.md`, rules here in `AGENT.md`.
