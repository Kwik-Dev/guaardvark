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


# ── What the progress footer reads from a video_gen job ─────────────────────

def test_adapt_video_gen_carries_the_wait_reason_and_current_item():
    job = adapt_video_gen({
        "batch_id": "VideoBatch_test_004",
        "status": "running",
        "stage": "gpu_wait",
        "current_item": None,
        "total_videos": 1,
        "metadata": {"gpu_wait_reason": "Queued behind Image Gen — needs ~9.0 GB, 3.0 GB free"},
    })
    assert job.metadata["stage"] == "gpu_wait"
    assert job.metadata["gpu_wait_reason"] == "Queued behind Image Gen — needs ~9.0 GB, 3.0 GB free"
    assert job.metadata["current_item"] is None


def test_adapt_video_gen_from_a_live_status():
    from backend.services.batch_video_generator import BatchVideoStatus

    status = BatchVideoStatus(batch_id="VideoBatch_test_005", status="running", total_videos=2,
                              stage="generate", current_item="item-a", progress_pct=40)
    job = adapt_video_gen(status)
    assert job.metadata["current_item"] == "item-a"
    assert job.metadata["gpu_wait_reason"] is None
    assert job.progress == 40.0


def test_the_jobs_snapshot_row_keeps_stage_progress_and_item():
    from backend.services.batch_video_generator import BatchVideoGenerator, BatchVideoStatus

    status = BatchVideoStatus(batch_id="VideoBatch_test_006", status="running", total_videos=1,
                              stage="keyframe", current_item="item-b", progress_pct=10,
                              metadata={"gpu_wait_reason": None})
    row = BatchVideoGenerator._batch_status_to_job_row(status, position=1, is_running=True)
    job = adapt_video_gen(row)
    assert (row["stage"], row["progress_pct"], row["current_item"]) == ("keyframe", 10, "item-b")
    assert job.metadata["stage"] == "keyframe" and job.metadata["current_item"] == "item-b"
    assert "[keyframe]" in job.label


def test_adapt_unified_progress_reads_a_flattened_event():
    from backend.services.job_registry import adapt_unified_progress

    # ProgressEvent.to_dict() spreads additional_data into the top level.
    job = adapt_unified_progress({
        "job_id": "ImageBatch_1", "progress": 0, "status": "processing",
        "message": "Queued behind Video Gen — needs ~11.7 GB, 2.6 GB free",
        "process_type": "image_generation", "timestamp": "2026-10-07T12:00:00+00:00",
        "batch_id": "ImageBatch_1", "gpu_wait_reason": "Queued behind Video Gen — needs ~11.7 GB, 2.6 GB free",
    })
    assert job.metadata["additional_data"] == {
        "batch_id": "ImageBatch_1",
        "gpu_wait_reason": "Queued behind Video Gen — needs ~11.7 GB, 2.6 GB free",
    }


def test_adapt_unified_progress_prefers_the_nested_additional_data():
    from backend.services.job_registry import adapt_unified_progress

    job = adapt_unified_progress({
        "process_id": "p2", "status": "processing", "process_type": "indexing",
        "additional_data": {"batch_id": "B"}, "stray": "ignored",
    })
    assert job.metadata["additional_data"] == {"batch_id": "B"}