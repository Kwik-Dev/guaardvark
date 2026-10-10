"""A recorded demo step is a finished job, never a pending one.

DemoStep has no status column. Before the fix, adapt_demo_step passed the
missing value through map_status, which reads None as PENDING, so every
recorded learning-mode step appeared in /api/jobs/active for good.
"""
from datetime import datetime
from types import SimpleNamespace

from backend.services.job_registry import adapt_demo_step
from backend.services.job_types import JobKind, JobStatus


def _demo(**over):
    base = dict(
        id=3,
        name="Save the report",
        is_complete=True,
        created_at=datetime(2026, 10, 10, 9, 0, 0),
        updated_at=datetime(2026, 10, 10, 9, 5, 0),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _step(demo=None, **over):
    demo = demo or _demo()
    base = dict(
        id=7,
        demonstration_id=demo.id,
        demonstration=demo,
        step_index=2,
        action_type="click",
        target_description="Save button",
        is_mistake=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_stored_step_is_completed_not_pending():
    job = adapt_demo_step(_step())
    assert job.kind == JobKind.DEMO
    assert job.status == JobStatus.COMPLETED
    assert not job.status.is_active
    assert job.status.is_terminal


def test_step_of_unfinished_demonstration_is_still_completed():
    # The step row only exists because the action already happened; the
    # demonstration being open (or abandoned) does not put the step in flight.
    job = adapt_demo_step(_step(_demo(is_complete=False)))
    assert job.status == JobStatus.COMPLETED
    assert job.metadata["demonstration_complete"] is False


def test_times_and_duration_come_from_the_demonstration():
    job = adapt_demo_step(_step())
    assert job.started_at == datetime(2026, 10, 10, 9, 0, 0)
    assert job.finished_at == datetime(2026, 10, 10, 9, 5, 0)
    assert job.duration_s == 300.0


def test_ids_label_and_metadata():
    job = adapt_demo_step(_step())
    assert job.id == "demo:7"
    assert job.native_id == 7
    assert job.parent_id == "demo:3"
    assert job.label == "Demo step #2: click Save button"
    assert job.cancellable is False
    assert job.metadata["step_index"] == 2
    assert job.metadata["action_type"] == "click"
    assert job.metadata["is_mistake"] is False
    assert job.metadata["demonstration_name"] == "Save the report"


def test_row_without_parent_still_adapts():
    job = adapt_demo_step(_step(demonstration=None))
    assert job.status == JobStatus.COMPLETED
    assert job.started_at is None
    assert job.finished_at is None
    assert job.duration_s is None
    assert job.metadata["demonstration_name"] is None


def test_explicit_status_still_maps_when_a_row_carries_one():
    # Forward-compatible: a future status column is honoured, not ignored.
    job = adapt_demo_step(_step(status="running"))
    assert job.status == JobStatus.RUNNING
    assert job.finished_at is None


def test_to_dict_is_json_safe():
    d = adapt_demo_step(_step()).to_dict()
    assert d["kind"] == "demo"
    assert d["status"] == "completed"
    assert d["started_at"] == "2026-10-10T09:00:00"
