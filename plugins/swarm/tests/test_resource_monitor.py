"""The swarm's resource monitor reads free VRAM from the backend's GPU status
payload, in the shape the GPU memory orchestrator actually returns."""

import json
import threading

import pytest


def _status_payload(vram_info):
    """What GET /api/gpu/memory/status returns for this VRAM probe result."""
    from unittest.mock import patch

    from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator

    orch = object.__new__(GPUMemoryOrchestrator)  # not the process singleton
    orch._lock = threading.RLock()
    orch._registry = {}
    orch._quality_tier = "balanced"
    orch._idle_timeout_s = 300
    orch._eviction_grace_s = 30
    with patch.object(orch, "_get_vram_info", return_value=vram_info):
        return json.loads(json.dumps(orch.get_registry_snapshot()))


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def backend_answers(monkeypatch):
    """Point the monitor's status request at a payload; returns the URLs asked for."""
    import service.resource_monitor as rm

    asked = []

    def answer(payload):
        def get(url, timeout=None):
            asked.append(url)
            return _Response(payload)
        monkeypatch.setattr(rm.requests, "get", get)
        return asked

    return answer


def _monitor():
    from service.resource_monitor import ResourceMonitor

    return ResourceMonitor()


def test_free_vram_is_read_from_the_status_payload(backend_answers):
    asked = backend_answers(_status_payload({
        "success": True, "total_mb": 16311, "used_mb": 4311, "available_mb": 12000,
        "gpu_name": "Test GPU", "utilization_percent": 3,
    }))
    monitor = _monitor()

    assert monitor._get_vram_from_backend() == 12000
    assert asked and asked[0].endswith("/api/gpu/memory/status")


def test_the_backend_answer_is_used_without_nvidia_smi(backend_answers, monkeypatch):
    backend_answers(_status_payload({
        "success": True, "total_mb": 16311, "used_mb": 15911, "available_mb": 400,
    }))
    monitor = _monitor()
    monkeypatch.setattr(monitor, "_get_free_vram_local",
                        lambda: pytest.fail("nvidia-smi is the fallback, not the source"))

    assert monitor.get_system_stats()["vram_free_mb"] == 400


def test_a_payload_without_a_gpu_total_is_unknown_not_full(backend_answers, monkeypatch):
    backend_answers(_status_payload({"success": False, "available_mb": 0, "total_mb": 0, "used_mb": 0}))
    monitor = _monitor()
    monkeypatch.setattr(monitor, "_get_free_vram_local", lambda: None)

    assert monitor._get_vram_from_backend() is None
    assert monitor.get_system_stats()["vram_free_mb"] is None
