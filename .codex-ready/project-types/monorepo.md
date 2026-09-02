# Project Type: Mixed Workspace / Monorepo

Guaardvark is a mixed workspace spanning multiple loosely coupled packages under one
repo. It is not a single-language monorepo; it mixes Python and JS toolchains with
GPU service plugins.

## Top-level shape

- `backend/` — Flask API, Celery workers, services, tools, MCP, models (Python 3.12).
- `frontend/` — React/Vite SPA (Node 22).
- `cli/` — standalone Python CLI, published to PyPI as `guaardvark`.
- `plugins/` — 13+ self-contained GPU services, each with its own `plugin.json`.
- `scripts/` — shell + Python operational tooling (start/stop, lint, schema sync,
  importers, test utils).
- `docs/` — architecture and user guides.
- Root orchestration: `start.sh` (single entry point), `stop.sh`, `killswitch.sh`.

## Per-package boundaries

- Each package has its own manifests, venv/deps, tests, and commands. Don't assume a
  backend command works in `frontend/` or `cli/`, and vice versa.
- Backend and frontend each run their own test/lint suites (see their project-type
  files).
- `scripts/lint.sh` covers backend (flake8 + black + portability); `frontend` lints
  via `npm run lint`.

## Versioning

- Single-sourced from the repo-root `VERSION` file (currently `2.7.0`).

## Cross-cutting conventions

- **Never hardcode paths** anywhere; resolve through `backend/config.py` or env vars.
- Secrets and `DATABASE_URL` come from the repo-root `.env`.
- `data/indexes/` (vector store JSON) and runtime state under `data/` are gitignored;
  don't commit them.
- The repo-root `manager` symlink is broken (`scripts/system-manager/system-manager`).
- Follow the repo's conventional-commit style: `type(scope): description`.
