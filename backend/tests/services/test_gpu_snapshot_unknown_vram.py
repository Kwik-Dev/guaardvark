"""A failed GPU query is reported as unknown, not as an empty card.

The registry snapshot (the GPU status API and the gpu:status pushes) used
to carry total_mb 0 and free_mb 0 whenever the query failed, and the pages
showed "0.0 GB free" next to "unknown".
"""

import threading

from backend.services import gpu_memory_orchestrator as gmo
from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator


def _orchestrator(vram):
    o = object.__new__(GPUMemoryOrchestrator)
    o._lock = threading.RLock()
    o._registry = {}
    o._quality_tier = "balanced"
    o._idle_timeout_s = 300
    o._eviction_grace_s = 30
    o._get_vram_info = lambda: vram
    return o


def test_failed_query_sends_null_numbers():
    o = _orchestrator({"success": False, "available_mb": 0, "total_mb": 0, "used_mb": 0})

    vram = GPUMemoryOrchestrator.get_registry_snapshot(o)["vram"]

    assert vram["queried"] is False
    assert vram["total_mb"] is None
    assert vram["free_mb"] is None
    assert vram["used_mb"] is None
    assert vram["gpu_name"] is None
    assert vram["utilization_percent"] is None


def test_successful_query_keeps_its_numbers():
    o = _orchestrator({
        "success": True, "total_mb": 16376, "used_mb": 1638, "available_mb": 14738,
        "gpu_name": "test card", "utilization_percent": 3,
    })

    vram = GPUMemoryOrchestrator.get_registry_snapshot(o)["vram"]

    assert vram["queried"] is True
    assert vram["total_mb"] == 16376
    assert vram["free_mb"] == 14738
    assert vram["gpu_name"] == "test card"
    assert gmo.QUALITY_TIERS  # the snapshot's tier_config comes from the same table
