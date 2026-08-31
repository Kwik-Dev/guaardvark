"""The platform layer answers the same way everywhere, and its messages never
name a card the user does not have."""
from __future__ import annotations

import pytest

from backend.utils import platform as plat


def test_device_kind_prefers_cuda_then_mps_then_cpu(monkeypatch):
    monkeypatch.setattr(plat, "has_cuda", lambda: True)
    monkeypatch.setattr(plat, "has_mps", lambda: True)
    assert plat.device_kind() == "cuda"
    monkeypatch.setattr(plat, "has_cuda", lambda: False)
    assert plat.device_kind() == "mps"
    monkeypatch.setattr(plat, "has_mps", lambda: False)
    assert plat.device_kind() == "cpu"


def test_require_cuda_passes_on_cuda(monkeypatch):
    monkeypatch.setattr(plat, "has_cuda", lambda: True)
    plat.require_cuda("LoRA training")


def test_require_cuda_tells_a_mac_user_the_metal_status(monkeypatch):
    monkeypatch.setattr(plat, "has_cuda", lambda: False)
    monkeypatch.setattr(plat, "has_mps", lambda: True)
    monkeypatch.setattr(plat, "os_name", lambda: "Darwin")
    monkeypatch.setattr(plat, "gpu_name", lambda: "Apple Silicon (Metal)")
    with pytest.raises(plat.PlatformUnsupported) as exc:
        plat.require_cuda("FX Lab (Stable Audio)", metal_status="untested")
    msg = str(exc.value)
    assert "FX Lab (Stable Audio) needs an NVIDIA GPU" in msg
    assert "untested" in msg and "Darwin, mps" in msg


def test_require_cuda_on_a_gpu_less_linux_box(monkeypatch):
    monkeypatch.setattr(plat, "has_cuda", lambda: False)
    monkeypatch.setattr(plat, "has_mps", lambda: False)
    monkeypatch.setattr(plat, "os_name", lambda: "Linux")
    monkeypatch.setattr(plat, "gpu_name", lambda: None)
    with pytest.raises(plat.PlatformUnsupported) as exc:
        plat.require_cuda("LoRA training")
    assert "none is available" in str(exc.value) and "Linux, cpu" in str(exc.value)


def test_screen_agent_is_linux_only(monkeypatch):
    monkeypatch.setattr(plat, "os_name", lambda: "Darwin")
    assert plat.screen_agent_available() is False
    monkeypatch.setattr(plat, "os_name", lambda: "Linux")
    assert plat.screen_agent_available() is True


def test_lora_train_time_budgets_stock_cuda_unchanged_from_main():
    """The non-Darwin path must keep main's numbers exactly, so a future edit
    cannot silently widen the CUDA caps (a wedged NVIDIA run would then hold the
    GPU for hours)."""
    load, train, task_soft, task_hard, reap = plat.lora_train_time_budgets(False)
    assert (load, train) == (900, 1800)
    # None → the global task_soft_time_limit / task_time_limit apply.
    assert task_soft is None and task_hard is None
    assert reap == 45 * 60


def test_lora_train_time_budgets_apple_silicon_derived_from_one_flag():
    load, train, task_soft, task_hard, reap = plat.lora_train_time_budgets(True)
    assert (load, train) == (3600, 10800)
    # The task limits and reaper are derived, not independent literals.
    assert task_soft == load + train + 300
    assert task_hard == load + train + 900
    assert reap == task_hard + 900
    assert task_hard > load + train  # the daemon train cap is never cut off
