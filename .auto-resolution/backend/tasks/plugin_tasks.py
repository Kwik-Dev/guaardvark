"""Off-request plugin dependency reconciliation.

`PluginManager.enable_plugin` used to run `dep_reconciler.py --only=plugin_bundle`
synchronously *inside the Flask request* — a pip install (up to 180s) blocking
the dev server and mutating the live venv mid-request. This moves that work onto
a Celery worker so enabling a plugin returns immediately; deps land in the
background and any failure surfaces when the plugin is actually started.

Wired into Celery from `backend/celery_app.py`.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="plugins.reconcile_deps", bind=True)
def reconcile_plugin_deps(self, plugin_id: str | None = None, timeout: int = 600) -> dict:
    """Run the plugin_bundle dep reconciler out-of-band.

    The reconciler figures out the full set of shared-venv enabled plugins
    itself (from plugin_state.json), so `plugin_id` is advisory — used only to
    log which toggle triggered the run. Timeout is generous (600s) because,
    unlike the old in-request path, nothing is blocked while torch installs.
    """
    repo_root = Path(__file__).resolve().parents[2]  # backend/tasks/x.py → repo
    entry = repo_root / "scripts" / "dep_reconciler.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(entry), "--only=plugin_bundle"],
            capture_output=True, text=True, timeout=timeout, cwd=str(repo_root),
        )
    except subprocess.TimeoutExpired:
        logger.error(f"[plugin-deps] reconciler timed out after {timeout}s (trigger={plugin_id})")
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except (FileNotFoundError, OSError) as e:
        logger.error(f"[plugin-deps] reconciler invocation failed: {e}")
        return {"ok": False, "error": str(e)}

    if proc.returncode == 0:
        logger.info(f"[plugin-deps] reconciler ok (trigger={plugin_id})")
        return {"ok": True}
    detail = (proc.stderr or proc.stdout or "unknown reconciler failure").strip()[:500]
    logger.error(f"[plugin-deps] reconciler rc={proc.returncode} (trigger={plugin_id}): {detail}")
    return {"ok": False, "rc": proc.returncode, "error": detail}
