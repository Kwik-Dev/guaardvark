"""What a background model call waits for: the GPU lease and ComfyUI, not VRAM."""
from unittest.mock import MagicMock, patch

import pytest

from backend.utils import gpu_check


def _lease(held: bool):
    coord = MagicMock()
    coord.lock_held.return_value = held
    return patch("backend.services.gpu_resource_coordinator.get_gpu_coordinator",
                 return_value=coord)


def test_a_held_lease_is_a_reason_to_wait():
    with _lease(True), patch.object(gpu_check, "comfyui_generating", return_value=False):
        assert "GPU lease" in gpu_check.gpu_reserved_reason()


def test_a_comfyui_job_is_a_reason_to_wait():
    with _lease(False), patch.object(gpu_check, "comfyui_generating", return_value=True):
        assert gpu_check.gpu_reserved_reason() == "ComfyUI is rendering"


def test_vram_in_use_alone_is_not_a_reason_to_wait():
    """The research run's own models in Ollama fill VRAM; it must not wait on itself."""
    with _lease(False), patch.object(gpu_check, "comfyui_generating", return_value=False), \
         patch.object(gpu_check, "vram_used_pct", return_value=95.0):
        assert gpu_check.gpu_reserved_reason() is None


def test_a_broken_lease_probe_falls_back_to_comfyui():
    with patch("backend.services.gpu_resource_coordinator.get_gpu_coordinator",
               side_effect=RuntimeError("no pids dir")), \
         patch.object(gpu_check, "comfyui_generating", return_value=False):
        assert gpu_check.gpu_reserved_reason() is None


def test_the_wait_lasts_while_the_gpu_is_held_and_reports_each_reason_once():
    reasons = iter(["a render holds it", "a render holds it", "ComfyUI is rendering", None])
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s
    seen = []
    with patch.object(gpu_check, "gpu_reserved_reason", side_effect=lambda: next(reasons)):
        waited = gpu_check.wait_for_gpu_turn(on_wait=seen.append, poll_s=5.0,
                                             sleep=sleep, clock=lambda: clock["t"])
    assert waited == pytest.approx(15.0)
    assert seen == ["a render holds it", "ComfyUI is rendering"]


def test_a_free_gpu_costs_no_wait():
    with patch.object(gpu_check, "gpu_reserved_reason", return_value=None):
        assert gpu_check.wait_for_gpu_turn(sleep=lambda s: pytest.fail("slept")) == pytest.approx(0.0, abs=0.5)


def test_a_stop_ends_the_wait_before_another_look():
    looks = []
    stop = iter([False, True])
    with patch.object(gpu_check, "gpu_reserved_reason",
                      side_effect=lambda: looks.append(1) or "a render holds it"):
        assert gpu_check.wait_for_gpu_turn(should_stop=lambda: next(stop),
                                           sleep=lambda s: None) is None
    assert len(looks) == 1


def test_between_experiments_a_held_lease_counts_as_busy_whatever_the_vram():
    with patch.object(gpu_check, "gpu_reserved_reason", return_value="a render holds it"), \
         patch.object(gpu_check, "vram_used_pct", return_value=5.0):
        assert gpu_check.gpu_busy() is True
    with patch.object(gpu_check, "gpu_reserved_reason", return_value=None), \
         patch.object(gpu_check, "vram_used_pct", return_value=5.0):
        assert gpu_check.gpu_busy() is False
