"""An out-of-process caller (the swarm plugin) can hold an Ollama model slot
over HTTP: preload sizes it from Ollama, begin-use pins it, end-use unpins it."""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from flask import Flask

from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator

API = "backend.api.gpu_orchestrator_api"
SLOT = "ollama:gemma4:e4b"


@pytest.fixture
def orch():
    """An orchestrator with no background thread and no GPU probe."""
    o = object.__new__(GPUMemoryOrchestrator)  # not the process singleton
    o._lock = threading.RLock()
    o._registry = {}
    o._ollama_expiry = {}
    o._eviction_grace_s = 0
    o._idle_timeout_s = 300
    with patch.object(o, "_get_vram_info", return_value={"success": False}):
        yield o


@pytest.fixture
def client(orch):
    from backend.api.gpu_orchestrator_api import gpu_orchestrator_bp
    app = Flask(__name__)
    app.register_blueprint(gpu_orchestrator_bp)
    with patch(f"{API}._get_orch", return_value=orch):
        yield app.test_client()


def test_preload_without_a_size_plans_for_the_ollama_model_weights(client, orch):
    with patch("backend.utils.ollama_resource_manager.get_model_info",
               return_value={"size_mb": 9216.4}) as info:
        res = client.post("/api/gpu/memory/preload", json={"slot_id": SLOT, "priority": 70})
    assert res.status_code == 200
    info.assert_called_once_with("gemma4:e4b")
    assert orch._registry[SLOT].vram_mb == 9216


def test_preload_without_a_size_falls_back_when_ollama_cannot_say(client, orch):
    with patch("backend.utils.ollama_resource_manager.get_model_info", return_value=None):
        client.post("/api/gpu/memory/preload", json={"slot_id": SLOT})
    assert orch._registry[SLOT].vram_mb == 4000


def test_an_explicit_size_is_used_as_given(client, orch):
    with patch("backend.utils.ollama_resource_manager.get_model_info",
               side_effect=AssertionError("not asked when the caller gave a size")):
        client.post("/api/gpu/memory/preload", json={"slot_id": "audio_foundry:x", "vram_mb": 1234})
    assert orch._registry["audio_foundry:x"].vram_mb == 1234


def test_begin_use_pins_and_end_use_unpins(client, orch):
    with patch("backend.utils.ollama_resource_manager.get_model_info", return_value=None):
        client.post("/api/gpu/memory/preload", json={"slot_id": SLOT})
    client.post("/api/gpu/memory/mark-loaded", json={"slot_id": SLOT})

    assert client.post("/api/gpu/memory/begin-use", json={"slot_id": SLOT}).status_code == 200
    assert orch._registry[SLOT].in_use == 1

    unloaded = []
    with patch.object(orch, "_unload_ollama_model", side_effect=lambda s: unloaded.append(s) or True):
        orch._evict_until_free(100_000)
        assert unloaded == [], "a pinned slot is not evicted to make room"

        assert client.post("/api/gpu/memory/end-use", json={"slot_id": SLOT}).status_code == 200
        assert orch._registry[SLOT].in_use == 0
        orch._evict_until_free(100_000)
    assert unloaded == [SLOT]


def test_begin_use_on_an_unknown_slot_is_404(client, orch):
    res = client.post("/api/gpu/memory/begin-use", json={"slot_id": SLOT})
    assert res.status_code == 404
    assert res.get_json()["success"] is False


@pytest.mark.parametrize("path", ["/api/gpu/memory/begin-use", "/api/gpu/memory/end-use"])
def test_slot_id_is_required(client, path):
    assert client.post(path, json={}).status_code == 400
