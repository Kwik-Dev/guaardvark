"""An image batch queued at the job gate reports that it is waiting.

The gate's own wait (``on_busy="wait"``) can last two minutes. Until it ends the
batch had only its "Starting image_generation..." message, so the progress footer
showed it as starting. ``on_wait`` reports the wait as it begins, naming the job
that holds the card.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend.services import gpu_resource_policy as grp
from backend.services import job_operation_gate as jog
from backend.services.batch_image_generator import (
    BatchGenerationStatus,
    BatchImageGenerator,
    BatchImageRequest,
    BatchPrompt,
)
from backend.services.job_operation_gate import (
    GpuBusyError,
    GpuCapacityError,
    JobOperationGate,
)
from backend.services.job_types import JobKind

VIDEO_HOLDS = "GPU is held by video_render:VideoBatch_1 — wait for completion"


class _Progress:
    def __init__(self):
        self.calls = []

    def create_process(self, **kw):
        self.calls.append(("create", kw))
        return kw.get("process_id")

    def update_process(self, **kw):
        self.calls.append(("update", kw))
        return True

    def error_process(self, **kw):
        self.calls.append(("error", kw))
        return True

    def cancel_process(self, **kw):
        self.calls.append(("cancel", kw))
        return True


def _status(batch_id="ImageBatch_2"):
    return BatchGenerationStatus(
        batch_id=batch_id, status="running", total_images=4,
        completed_images=0, failed_images=0,
    )


def test_gate_wait_reports_the_holder_once():
    gate = JobOperationGate()
    assert gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "VideoBatch_1")[0]
    seen = []

    with pytest.raises(GpuBusyError):
        with gate.gpu_exclusive(
            JobKind.VIDEO_RENDER, "ImageBatch_2",
            on_busy="wait", wait_timeout=0.3, on_wait=seen.append,
        ):
            pass

    assert seen == [VIDEO_HOLDS]


def test_cooldown_countdown_is_one_report_and_the_claim_still_lands(monkeypatch):
    monkeypatch.setattr(jog, "GPU_RELEASE_COOLDOWN_S", 1.5)
    gate = JobOperationGate()
    gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "VideoBatch_1")
    gate.release_gpu_exclusive(JobKind.VIDEO_RENDER, "VideoBatch_1")
    seen = []

    with gate.gpu_exclusive(
        JobKind.VIDEO_RENDER, "ImageBatch_2",
        on_busy="wait", wait_timeout=5.0, on_wait=seen.append,
    ) as acquired:
        assert acquired is True

    assert len(seen) == 1 and seen[0].startswith("GPU cooling down")


def test_a_failing_callback_does_not_end_the_wait():
    gate = JobOperationGate()
    gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "VideoBatch_1")

    def boom(_reason):
        raise RuntimeError("status line broke")

    with pytest.raises(GpuBusyError):
        with gate.gpu_exclusive(
            JobKind.VIDEO_RENDER, "ImageBatch_2",
            on_busy="wait", wait_timeout=0.2, on_wait=boom,
        ):
            pass


def test_gpu_session_passes_on_wait_to_the_gate(monkeypatch):
    gate = JobOperationGate()
    monkeypatch.setattr(jog, "_GATE_SINGLETON", gate)
    gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "VideoBatch_1")
    seen = []

    with pytest.raises(GpuBusyError):
        with grp.gpu_session(
            JobKind.VIDEO_RENDER, "ImageBatch_2",
            on_busy="wait", wait_timeout=0.2, on_wait=seen.append,
        ):
            pass

    assert seen == [VIDEO_HOLDS]


def test_report_names_the_holder_and_marks_the_batch_waiting(monkeypatch):
    monkeypatch.setattr(grp, "vram_probe_snapshot", lambda **_kw: {"free_mb": 2662})
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    gen.progress_system = _Progress()
    status = _status()

    msg = gen._report_gpu_wait("ImageBatch_2", status, GpuBusyError(VIDEO_HOLDS), need_mb=11981)

    assert msg == "Queued behind Video Gen — needs ~11.7 GB, 2.6 GB free"
    assert status.gpu_wait_reason == msg
    (kind, update), = gen.progress_system.calls
    assert kind == "update"
    assert update["message"] == msg
    assert update["additional_data"]["gpu_wait_reason"] == msg


def test_queued_batch_reports_the_gate_wait_before_it_is_admitted(monkeypatch, tmp_path):
    """The run path hands gpu_session a callback that reaches the progress feed."""
    monkeypatch.setattr(grp, "vram_probe_snapshot", lambda **_kw: {"free_mb": 2048})
    monkeypatch.setattr(grp, "compositor_vram_reserve_mb", lambda: 0)

    @contextmanager
    def fake_session(*_a, on_wait=None, **_kw):
        assert on_wait is not None, "the image batch must report its gate wait"
        on_wait(VIDEO_HOLDS)
        raise GpuCapacityError("end the test after the wait was reported")
        yield  # pragma: no cover

    monkeypatch.setattr(grp, "gpu_session", fake_session)

    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    gen.progress_system = _Progress()
    gen.image_generator = SimpleNamespace(_device="cuda")
    gen.cancel_events = {}
    gen._kept_model_for_batch = lambda _req: None
    gen._batch_resource_estimates = lambda _req, _reuse: (8000, 4.0)
    gen._save_batch_metadata = lambda *_a, **_kw: None

    request = BatchImageRequest(
        batch_id="ImageBatch_2",
        prompts=[BatchPrompt(id="p1", prompt="a lighthouse at dusk")],
        output_dir=str(tmp_path),
        save_metadata=False,
    )
    status = _status()

    gen._run_batch_job(request, status, tmp_path)

    updates = [kw for kind, kw in gen.progress_system.calls if kind == "update"]
    assert updates, "no wait was reported"
    assert updates[0]["message"] == "Queued behind Video Gen — needs ~8.8 GB, 2.0 GB free"
    assert updates[0]["additional_data"]["gpu_wait_reason"] == updates[0]["message"]
