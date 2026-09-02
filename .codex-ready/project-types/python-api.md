# Project Type: Python API Service (Flask)

Guaardvark's backend is a Flask application in `backend/`. It exposes REST APIs via
blueprints plus Socket.IO for realtime. The backend also hosts Celery workers,
in-process services, a tool registry, an agent brain, and the MCP server.

## Entrypoints & structure

- `backend/app.py` defines `create_app()`. It runs **exactly once per process**;
  always use `get_or_create_app()`, never call `create_app()` directly. The
  `__name__ == "__main__"` guard aliases `backend.app` into `sys.modules` to avoid a
  dual-import that would create a second Flask app and corrupt shared state.
- API endpoints live in `backend/api/` (~94 modules ending `_api.py`). They are **not
  registered manually** — `backend/utils/blueprint_discovery.py` scans the directory
  and registers every Flask blueprint. Add an endpoint by dropping a module that
  exports a `Blueprint`; no central wiring needed.
- State lives in the `shared db` SQLAlchemy instance in `backend/models.py` (~61
  models). Schema is synced via `scripts/schema_sync.py` (diffs `models.py` against
  the live DB), **not** migration replay. After changing `models.py`, run
  `python3 scripts/schema_sync.py` (or `--check`).
- Celery workers: `backend/celery_app.py` + `backend/tasks/`. Workers use the `spawn`
  multiprocessing start method and fetch an app context via `get_or_create_app()`.

## Commands

```bash
# iterative dev (after ./start.sh provisioned deps)
source backend/venv/bin/activate
export FLASK_APP=backend.app GUAARDVARK_ROOT=$(pwd)
flask run --debug --host=0.0.0.0 --port=5000

# tests
python3 run_tests.py            # full suite (installs deps, runs migrations, then pytest)
python3 -m pytest backend/tests/test_rules.py -vv

# lint (flake8 syntax + black --check + portability)
scripts/lint.sh
```

## Critical test gotchas

- `run_tests.py` forces `GUAARDVARK_MODE=test` and `DISABLE_CELERY=true`. **Replicate
  both env vars when running pytest directly** or tests hit the real DB/Celery.
- Backend tests live in `backend/tests/` mirroring source layout (`api/`, `services/`,
  `models/`, `integration/`); shared fixtures in `conftest.py`.

## Conventions to respect

- **Never hardcode paths.** Resolve through `backend/config.py`; everything is anchored
  by `GUAARDVARK_ROOT`. Secrets and `DATABASE_URL` come from the repo-root `.env`.
- OOM priority adjustment in `backend/app.py` is intentional — do not remove it.
- Keep changes focused and match surrounding style; one concern per edit.
- The Vite proxy returns 502 `{"error":"backend_offline"}` when backend is down, and
  filters benign ECONNRESET/EPIPE noise — those are features, not bugs.
