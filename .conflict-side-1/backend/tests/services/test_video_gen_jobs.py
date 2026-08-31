"""VideoGen job adapter + cancel wiring for the unified /api/jobs surface."""
from datetime import datetime

from backend.services.job_registry import adapt_video_gen, get_job
from backend.services.job_types import JobKind, JobStatus


def test_adapt_video_gen_maps_running_status():
    job = adapt_video_gen({
        "batch_id": "VideoBatch_test_001",
        "status": "running",
        "total_videos": 2,
        "completed_videos": 1,
        "failed_videos": 0,
        "start_time": datetime(2026, 6, 9, 12, 0, 0).isoformat(),
        "metadata": {"display_name": "test prompt"},
        "is_running": True,
    })
    assert job.kind == JobKind.VIDEO_GEN
    assert job.status == JobStatus.RUNNING
    assert job.id == "video_gen:VideoBatch_test_001"
    assert job.cancellable is True
    assert job.progress == 50.0
    assert "test prompt" in job.label


def test_adapt_video_gen_maps_queued_to_pending():
    job = adapt_video_gen({
        "batch_id": "VideoBatch_test_002",
        "status": "queued",
        "total_videos": 1,
        "completed_videos": 0,
        "failed_videos": 0,
        "metadata": {},
    })
    assert job.status == JobStatus.PENDING
    assert job.cancellable is True


def test_cancel_video_gen_dispatch(monkeypatch):
    cancelled = []

    class _FakeGen:
        def cancel_batch(self, batch_id):
            cancelled.append(batch_id)
            return True

    import backend.services.batch_video_generator as bvg
    monkeypatch.setattr(bvg, "get_batch_video_generator", lambda: _FakeGen())

    from backend.services.job_cancel import cancel_job

    ok = cancel_job(JobKind.VIDEO_GEN, "VideoBatch_test_003")
    assert ok is True
    assert cancelled == ["VideoBatch_test_003"]