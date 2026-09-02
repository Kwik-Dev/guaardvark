# KNOWLEDGE.md

Agent-facing sharp edges, invariants, and local rules that are easy to miss while
editing Guaardvark. This is distinct from `README.md` (human big-picture overview) and
`AGENT.md` (editing rules). Consult this before repeated work so the same issue is not
rediscovered, and update it as new discoveries are made.

## Critical invariants

- **Python 3.12 only.** The ML stack (numpy<2.0, mediapipe, basicsr/gfpgan,
  realesrgan) has no wheels for 3.13/3.14. Create venvs with `python3.12`.
  `setuptools<81` is required (several ML libs still import `pkg_resources`).
- **Never hardcode paths.** Resolve through `backend/config.py`; `GUAARDVARK_ROOT`
  anchors everything. Secrets and `DATABASE_URL` come from the repo-root `.env`
  (frontend also reads the repo-root `.env`, not `frontend/.env`).
- **App bootstrap runs once per process.** Always `get_or_create_app()`; never call
  `create_app()` directly. The `__name__ == "__main__"` guard aliases `backend.app`
  into `sys.modules` to prevent a dual-import corrupting shared state.
- **Blueprints auto-register.** API endpoints in `backend/api/*_api.py` are scanned by
  `backend/utils/blueprint_discovery.py`; drop a module exporting a `Blueprint` to add
  an endpoint.

## Naming & directory traps

- `backend/services/swarm/` is the **Film Crew** sequential production pipeline
  (Screenwriter → Casting → Cinematographer → Storyboard → Editor), NOT the parallel
  orchestrator. The parallel Swarm Orchestrator (coding agents, port 8210, **/swarm**
  UI) lives in `plugins/swarm/`. The directory name is a legacy artifact.
- The repo-root `manager` symlink is **broken** (`scripts/system-manager/system-manager`) —
  don't rely on it.

## Schema & data

- Schema is synced via `scripts/schema_sync.py` (diffs `models.py` vs live DB), NOT
  migration replay. After changing `backend/models.py`, run
  `python3 scripts/schema_sync.py` (or `--check`).
- `data/indexes/` (vector store JSON) is gitignored runtime state; don't commit it.
- Default DB: `postgresql://guaardvark:guaardvark@localhost:5432/guaardvark`.

## Safety systems (real teeth)

- `killswitch.sh`, the `codebase_locked` / `self_improvement_enabled` rows, and
  `data/.codebase_lock` gate whether the self-improvement engine may modify code.
- Outreach (`backend/tasks/social_outreach_tasks.py`,
  `backend/tools/outreach_tools.py`) is **supervised by default** — drafts queue and
  nothing posts without explicit approval. Operator identity is config-driven, never
  hardcoded.
- MCP is **default-deny** (`backend/mcp/config.py`); only `data/outputs/` is served
  read-only. Config source of truth is `data/config/mcp.json`; env vars override.
- Tool categories/flags in `backend/tools/` (`is_dangerous`, `requires_approval`) are
  the MCP security boundary — set them correctly on any tool that touches the machine.

## Backend gotchas

- `backend/app.py` applies an OOM score adjustment early (`backend/oom_priority.py`)
  so the kernel kills the backend rather than the desktop under memory pressure — do
  not remove it.
- Celery workers use the `spawn` multiprocessing start method; tasks fetch an app
  context via `get_or_create_app()`.

## Frontend gotchas

- The Vite proxy intentionally returns HTTP 502 `{"error":"backend_offline"}` when the
  backend is down (not a misleading 500) and filters benign ECONNRESET/EPIPE socket
  proxy noise. Do not "fix" these as bugs.
- `vite.config.js` repeats host allowlist + `/api` + `/socket.io` proxy under a
  `preview:` block — preview does not share the `server:` block.

## Testing

- Replicate `GUAARDVARK_MODE=test` and `DISABLE_CELERY=true` when running pytest
  directly (else you hit the real DB/Celery). `run_tests.py` forces both.
- Backend tests mirror source under `backend/tests/`; shared fixtures in `conftest.py`.
- Frontend tests are `src/**/*.{test,spec}.{js,jsx}` under vitest/jsdom.
- CI runs CLI tests, frontend lint+build, and backend static quality gate
  (`scripts/quality_gate.py --mode static`, `scripts/check_portable.sh`, py_compile).
- Use `scripts/lint.sh` for backend (flake8 syntax + black --check + portability);
  frontend lint is strict (`--max-warnings 0`).

## Style

- Conventional commits: `type(scope): description` (`feat`, `fix`, `refactor`, `docs`,
  `test`, `chore`).
- React: functional components + hooks, MUI v5, Zustand global state, React Context
  for layout/status, Axios REST, socket.io-client realtime.
- Match surrounding style, one concern per change.
