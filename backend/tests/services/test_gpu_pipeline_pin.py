"""Pinned sd:pipeline must not be idle-evicted mid-batch."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from backend.services.gpu_memory_orchestrator import (
    GPUMemoryOrchestrator,
    ModelSlot,
    ModelType,
    SlotState,
)


def _bare_orch(*, idle_timeout_s: float = 1.0) -> GPUMemoryOrchestrator:
    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._lock = threading.RLock()
    orch._registry = {}
    orch._eviction_grace_s = 0
    orch._idle_timeout_s = idle_timeout_s
    return orch


def test_begin_use_blocks_idle_eviction():
    orch = _bare_orch(idle_timeout_s=1.0)
    now = 1_000_000.0
    slot = ModelSlot(
        slot_id="sd:pipeline",
        model_type=ModelType.SD_PIPELINE,
        vram_mb=11000,
        loaded_at=now - 100,
        last_used=now - 100,  # well past idle timeout
        use_count=5,
        priority=85,
        state=SlotState.LOADED,
        in_use=0,
    )
    orch._registry["sd:pipeline"] = slot

    with patch("time.time", return_value=now):
        orch.begin_use("sd:pipeline")
    assert slot.in_use == 1

    unloaded = []

    def fake_unload(s):
        unloaded.append(s.slot_id)
        return True

    with patch.object(orch, "_unload_model", side_effect=fake_unload):
        with patch("time.time", return_value=now + 10):
            with patch(
                "backend.services.gpu_resource_coordinator.has_gpu",
                return_value=True,
            ):
                orch._evict_idle_models()

    assert unloaded == []
    assert "sd:pipeline" in orch._registry

    with patch("time.time", return_value=now + 20):
        orch.end_use("sd:pipeline")
    assert slot.in_use == 0

    with patch.object(orch, "_unload_model", side_effect=fake_unload):
        with patch("time.time", return_value=now + 100):
            with patch(
                "backend.services.gpu_resource_coordinator.has_gpu",
                return_value=True,
            ):
                orch._evict_idle_models()

    assert unloaded == ["sd:pipeline"]


def test_unload_model_refuses_when_in_use():
    orch = _bare_orch()
    slot = ModelSlot(
        slot_id="sd:pipeline",
        model_type=ModelType.SD_PIPELINE,
        vram_mb=11000,
        state=SlotState.LOADED,
        in_use=2,
    )
    assert orch._unload_model(slot) is False
    assert slot.state == SlotState.LOADED


def test_unload_pipeline_refuses_when_generation_lock_held():
    from backend.services.offline_image_generator import OfflineImageGenerator

    gen = OfflineImageGenerator.__new__(OfflineImageGenerator)
    gen._generation_lock = threading.RLock()
    gen._pipeline = MagicMock()
    gen._pipeline.scheduler = MagicMock()
    gen._img2img_pipeline = None
    gen._img2img_family = None
    gen._current_model = "Tongyi-MAI/Z-Image-Turbo"
    gen._pipeline_offload_mode = "sequential"
    gen._compile_unet_orig = None
    gen._compile_vae_orig = None
    gen._loaded_lora_adapters = []

    held = threading.Event()
    release = threading.Event()

    def holder():
        with gen._generation_lock:
            held.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert held.wait(timeout=2)
    try:
        assert gen._unload_pipeline(wait=False) is False
        assert gen._pipeline is not None
        assert gen._pipeline.scheduler is not None
    finally:
        release.set()
        t.join(timeout=2)
