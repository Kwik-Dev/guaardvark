"""GPU orchestrator hard_fit: refuse admit when free VRAM stays short."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_request_model_hard_fit_refuses_when_short():
    from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator

    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._lock = __import__("threading").RLock()
    orch._registry = {}
    orch._eviction_grace_s = 30
    orch._idle_timeout_s = 600

    def fake_vram():
        return {"success": True, "available_mb": 400, "total_mb": 16000}

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=0):
            with patch.object(orch, "_physical_reclaim_untracked", return_value=0):
                with pytest.raises(RuntimeError, match="GPU short|refusing|only"):
                    orch.request_model(
                        "sd:pipeline",
                        vram_estimate_mb=11000,
                        priority=85,
                        hard_fit=True,
                    )


def test_request_model_soft_fit_admits_when_short():
    from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator, SlotState

    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._lock = __import__("threading").RLock()
    orch._registry = {}
    orch._eviction_grace_s = 30
    orch._idle_timeout_s = 600

    def fake_vram():
        return {"success": True, "available_mb": 400, "total_mb": 16000}

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=0):
            with patch.object(orch, "_physical_reclaim_untracked", return_value=0):
                slot = orch.request_model(
                    "sd:pipeline",
                    vram_estimate_mb=11000,
                    priority=85,
                    hard_fit=False,
                )
    assert slot.slot_id == "sd:pipeline"
    assert slot.state == SlotState.LOADING
