"""A failed or cancelled job names its kind (``error_kind``), which the backend's
get_generation_status turns into a label and a next step. The values are the
backend's RenderErrorKind values (backend/services/job_types.py)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from backends.base import GenerationCancelled  # noqa: E402
from backends.hub_weights import WeightsNotInstalled  # noqa: E402
from service.jobs import JobManager, failure_kind_of  # noqa: E402


class OutOfMemoryError(RuntimeError):
    """Named like torch.cuda.OutOfMemoryError, which this test does not import."""


def _finished(manager: JobManager, job_id: str, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _run(tmp_path, exc: BaseException) -> dict:
    def runner(intent, params, progress, cancel):
        raise exc

    manager = JobManager(runner=runner, jobs_dir=tmp_path)
    return _finished(manager, manager.submit("music", {}))


def test_weights_not_on_disk_are_model_not_installed(tmp_path):
    job = _run(tmp_path, WeightsNotInstalled("ACE-Step is not installed"))
    assert job["status"] == "error" and job["error_kind"] == "model_not_installed"
    assert job["error"] == "WeightsNotInstalled: ACE-Step is not installed"


def test_out_of_memory_by_type_or_message_is_oom(tmp_path):
    assert _run(tmp_path / "a", OutOfMemoryError("CUDA error"))["error_kind"] == "oom"
    assert _run(tmp_path / "b", RuntimeError("CUDA out of memory. Tried to allocate"))["error_kind"] == "oom"


def test_a_cancelled_job_is_cancelled(tmp_path):
    job = _run(tmp_path, GenerationCancelled())
    assert job["status"] == "cancelled" and job["error_kind"] == "cancelled"


def test_anything_else_has_no_kind_and_the_record_round_trips(tmp_path):
    job = _run(tmp_path, ValueError("bad lyrics"))
    assert job["status"] == "error" and job["error_kind"] is None
    saved = json.loads((tmp_path / f"{job['id']}.json").read_text())
    assert saved["error_kind"] is None and saved["error"] == "ValueError: bad lyrics"


def test_classification_never_raises():
    assert failure_kind_of(KeyError("x")) is None
