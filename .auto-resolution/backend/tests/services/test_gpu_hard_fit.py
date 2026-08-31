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


# --- Compositor VRAM reserve (2026-08-04 client box 2048² incident) --------------
# Opt-in per caller: reserved MB are treated as not-free so the desktop
# compositor's share of the card survives the job. Default 0 keeps existing
# (incl. near-full-card video) admissions byte-identical.

def _bare_orch():
    from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator

    orch = GPUMemoryOrchestrator.__new__(GPUMemoryOrchestrator)
    orch._lock = __import__("threading").RLock()
    orch._registry = {}
    orch._eviction_grace_s = 30
    orch._idle_timeout_s = 600
    return orch


def test_request_model_reserve_refuses_within_reserve_band():
    orch = _bare_orch()

    def fake_vram():
        return {"success": True, "available_mb": 15600, "total_mb": 16000}

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=0):
            with patch.object(orch, "_physical_reclaim_untracked", return_value=0):
                with pytest.raises(RuntimeError, match="GPU short"):
                    orch.request_model(
                        "sd:pipeline",
                        vram_estimate_mb=15400,
                        priority=85,
                        hard_fit=True,
                        vram_reserve_mb=800,
                    )


def test_request_model_reserve_zero_keeps_video_admission():
    # The same near-full-card estimate admits with reserve=0 (mostly-free bypass)
    # — the video path must not regress.
    orch = _bare_orch()

    def fake_vram():
        return {"success": True, "available_mb": 15600, "total_mb": 16000}

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=0):
            with patch.object(orch, "_physical_reclaim_untracked", return_value=0):
                slot = orch.request_model(
                    "video_render:ltx",
                    vram_estimate_mb=15400,
                    priority=85,
                    hard_fit=True,
                )
    assert slot.slot_id == "video_render:ltx"


def test_request_model_reserve_admits_when_it_fits_beside_reserve():
    orch = _bare_orch()

    def fake_vram():
        return {"success": True, "available_mb": 15600, "total_mb": 16000}

    with patch.object(orch, "_get_vram_info", side_effect=fake_vram):
        with patch.object(orch, "_evict_until_free", return_value=0):
            with patch.object(orch, "_physical_reclaim_untracked", return_value=0):
                slot = orch.request_model(
                    "sd:pipeline",
                    vram_estimate_mb=14000,
                    priority=85,
                    hard_fit=True,
                    vram_reserve_mb=800,
                )
    assert slot.slot_id == "sd:pipeline"
