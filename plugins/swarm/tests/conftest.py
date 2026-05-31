"""Pytest setup for the swarm plugin tests.

The swarm service imports its own modules as ``service.*`` (the start script runs
uvicorn with PYTHONPATH=<plugin_root>). Mirror that here so the FastAPI app and
its helpers import cleanly under pytest, and make the repo root importable so the
guarded_code_service chokepoint can be reached.
"""

import sys
from pathlib import Path

# plugins/swarm  (so `import service.app` works)
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# repo root      (so `import backend.services...` works)
REPO_ROOT = Path(__file__).resolve().parents[3]

for p in (str(PLUGIN_ROOT), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
