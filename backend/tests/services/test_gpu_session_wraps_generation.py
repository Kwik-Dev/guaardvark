"""gpu_session must cover the real work, and fit-checks must honor the reserve.

2026-08-04 client box 2048² incident: the single-image path did
`with gpu_session(...): pass` — every lease/fit-check/RAM reservation was
released BEFORE the pipeline load and denoise. And nothing anywhere reserved
VRAM for the desktop compositor, whose starvation is what kills the Wayland
session. These tests pin both fixes.
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import backend.services.offline_image_generator as oig
import backend.services.gpu_resource_policy as grp
import backend.services.gpu_memory_orchestrator as gmo
from backend.services.offline_image_generator import ImageGenerationRequest


GB = 1024 * 1024 * 1024


# --- _ensure_fits_or_busy with reserve_mb ------------------------------------

def _probe(available_mb, total_mb):
    coord = MagicMock()
    coord.get_available_vram.return_value = {
        "success": True, "available_mb": available_mb, "total_mb": total_mb,
    }
    return coord


def test_fit_check_reserve_refuses_inside_reserve_band():
    from backend.services.job_operation_gate import GpuBusyError
    with patch(
        "backend.services.gpu_resource_coordinator.get_gpu_coordinator",
        return_value=_probe(15600, 16376),
    ):
        with pytest.raises(GpuBusyError):
            grp._ensure_fits_or_busy(15800, "sd:pipeline", reserve_mb=800)


def test_fit_check_reserve_admits_when_estimate_fits_beside_reserve():
    with patch(
        "backend.services.gpu_resource_coordinator.get_gpu_coordinator",
        return_value=_probe(15600, 16376),
    ):
        grp._ensure_fits_or_busy(14000, "sd:pipeline", reserve_mb=800)  # no raise


def test_fit_check_reserve_zero_keeps_video_admission():
    # Near-full-card estimate on a mostly-idle card must still admit with the
    # default reserve — the LTX/Cog video path depends on this bypass.
    with patch(
        "backend.services.gpu_resource_coordinator.get_gpu_coordinator",
        return_value=_probe(15600, 16376),
    ):
        grp._ensure_fits_or_busy(15800, "video_render:ltx")  # no raise


# --- gpu_session wraps the real generation work ------------------------------

class _OomPipeline:
    def __init__(self):
        self.vae = SimpleNamespace(
            enable_tiling=lambda: None,
            disable_tiling=lambda: None,
            enable_slicing=lambda: None,
        )

    def __call__(self, **kw):
        raise oig.torch.cuda.OutOfMemoryError("CUDA out of memory (synthetic)")


def test_session_covers_load_and_teardown(monkeypatch):
    gen = oig.OfflineImageGenerator()
    gen.service_available = True
    gen._device = "cuda"
    gen._pipeline_offload_mode = "model"

    events = []

    @contextlib.contextmanager
    def _recording_session(*a, **kw):
        events.append("enter")
        try:
            yield True
        finally:
            events.append("exit")

    monkeypatch.setattr(grp, "gpu_session", _recording_session)

    class _Orch:
        def __getattr__(self, name):
            return lambda *a, **kw: None

    monkeypatch.setattr(gmo, "get_orchestrator", lambda: _Orch())
    monkeypatch.setattr(gen, "_notify_vision_pipeline", lambda *a, **kw: None)
    monkeypatch.setattr(gen, "_ensure_vram_for_pipeline", lambda *a, **kw: None)
    monkeypatch.setattr(gen, "_ensure_flow_scheduler", lambda *a, **kw: None)
    monkeypatch.setattr(gen, "_unload_pipeline", lambda: events.append("unload"))
    monkeypatch.setattr(
        oig.torch.cuda, "mem_get_info", lambda: (8 * GB, 16 * GB), raising=False
    )

    def _fake_load(model_id, *, force_sequential=False):
        events.append("load")
        gen._pipeline = _OomPipeline()
        return True

    monkeypatch.setattr(gen, "_load_pipeline", _fake_load)

    request = ImageGenerationRequest(
        prompt="a scenic mountain valley at dawn",
        model="zimage-turbo",
        width=2048,
        height=2048,
        auto_enhance=False,
        seed=None,
    )
    result = gen.generate_image(request)

    assert result.success is False
    assert events[0] == "enter", "session must be entered before any work"
    assert "load" in events and events.index("enter") < events.index("load"), (
        "pipeline load must happen INSIDE the session (old code released it first)"
    )
    assert events[-1] == "exit", "session must be released after all teardown"
    assert events.index("exit") > events.index("unload")
