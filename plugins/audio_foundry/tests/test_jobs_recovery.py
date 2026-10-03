"""A restart fails the job that was rendering and runs the ones still waiting, oldest first."""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from service.jobs import Job, JobManager  # noqa: E402


def _wait_until_finished(manager: JobManager, ids: list[str], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if all(manager.get(i)["status"] in ("done", "error", "cancelled") for i in ids):
            return
        time.sleep(0.02)
    raise AssertionError("jobs did not finish")


def test_restart_requeues_waiting_jobs_and_fails_the_interrupted_one(tmp_path):
    seeded = [
        Job(id="running", intent="music", params={}, status="running", created_at=1.0),
        Job(id="later", intent="music", params={}, status="queued", created_at=3.0),
        Job(id="earlier", intent="music", params={}, status="queued", created_at=2.0),
    ]
    for job in seeded:
        (tmp_path / f"{job.id}.json").write_text(json.dumps(asdict(job)))

    manager = JobManager(runner=lambda intent, params, progress, cancel: {"ok": True}, jobs_dir=tmp_path)
    _wait_until_finished(manager, ["running", "later", "earlier"])

    interrupted = manager.get("running")
    assert interrupted["status"] == "error" and interrupted["error"] == "interrupted by service restart"
    earlier, later = manager.get("earlier"), manager.get("later")
    assert earlier["status"] == later["status"] == "done"
    assert earlier["started_at"] <= later["started_at"]
