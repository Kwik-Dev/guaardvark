"""P0.3a — the GpuResourcePolicy front door + canonical VRAM reclaim.

Locks: gpu_session delegates to the real JobOperationGate (preserving fail-fast
GpuBusyError + slot semantics), runs reclaim ONLY when the slot was actually won,
debits/releases the orchestrator budget when asked, and releases on exception. The
reclaim primitives are best-effort (never raise) and route by flag.
"""
import pytest

try:
    import backend.services.gpu_resource_policy as grp
    import backend.services.job_operation_gate as jog
    from backend.services.job_operation_gate import JobOperationGate, GpuBusyError
    from backend.services.job_types import JobKind
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def fresh_gate(monkeypatch):
    # A clean gate per test → no 8s-cooldown bleed across tests.
    gate = JobOperationGate()
    monkeypatch.setattr(jog, "get_gate", lambda: gate)
    return gate


# --- canonical reclaim primitives -------------------------------------------

def test_free_comfyui_vram_posts_free(monkeypatch):
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        return object()
    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    assert grp.free_comfyui_vram() is True
    assert calls["url"].endswith("/free")
    assert calls["json"] == {"unload_models": True, "free_memory": True}


def test_free_comfyui_vram_swallows_errors(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert grp.free_comfyui_vram() is False   # non-fatal


def test_evict_ollama_delegates_to_coordinator(monkeypatch):
    import backend.services.gpu_resource_coordinator as coord
    called = {}
    monkeypatch.setattr(coord, "unload_ollama_models", lambda *a, **k: called.setdefault("hit", True))
    assert grp.evict_ollama_models() is True
    assert called.get("hit") is True


def test_reclaim_routes_flags(monkeypatch):
    seen = []
    monkeypatch.setattr(grp, "free_comfyui_vram", lambda: seen.append("comfy"))
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: seen.append("ollama"))
    grp.reclaim_gpu(evict_ollama=True, free_comfyui=True)
    assert set(seen) == {"comfy", "ollama"}
    seen.clear()
    grp.reclaim_gpu()  # defaults: reclaim nothing
    assert seen == []


# --- gpu_session front door --------------------------------------------------

def test_gpu_session_default_is_gate_passthrough(fresh_gate):
    with grp.gpu_session(JobKind.VIDEO_RENDER, "op1") as acquired:
        assert acquired is True
        # slot is genuinely held: a different id is refused
        ok, _ = fresh_gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "other")
        assert ok is False
    assert fresh_gate._gpu_holder is None      # released after the block


def test_gpu_session_busy_raises(fresh_gate):
    fresh_gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "held")
    with pytest.raises(GpuBusyError):
        with grp.gpu_session(JobKind.VIDEO_RENDER, "loser"):
            pass


def test_gpu_session_runs_reclaim_only_when_acquired(fresh_gate, monkeypatch):
    seen = []
    monkeypatch.setattr(grp, "reclaim_gpu", lambda **kw: seen.append(kw))
    with grp.gpu_session(JobKind.VIDEO_RENDER, "op", evict_ollama=True, free_comfyui=True):
        pass
    assert seen == [{"evict_ollama": True, "free_comfyui": True}]


def test_gpu_session_register_degrade_skips_reclaim(fresh_gate, monkeypatch):
    # Pre-hold the slot; on_busy='register' yields False (degraded) → never evict for
    # a job that didn't actually win the card.
    fresh_gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "held")
    seen = []
    monkeypatch.setattr(grp, "reclaim_gpu", lambda **kw: seen.append(kw))
    with grp.gpu_session(JobKind.VIDEO_RENDER, "deg", on_busy="register", evict_ollama=True) as acquired:
        assert acquired is False
    assert seen == []


def test_gpu_session_vram_budget_requests_and_releases(fresh_gate, monkeypatch):
    events = []
    monkeypatch.setattr(grp, "_orchestrator_request", lambda slot, mb: events.append(("req", slot, mb)))
    monkeypatch.setattr(grp, "_orchestrator_release", lambda slot: events.append(("rel", slot)))
    with grp.gpu_session(JobKind.VIDEO_RENDER, "op", vram_estimate_mb=8000, slot_id="video:mv"):
        pass
    assert events == [("req", "video:mv", 8000), ("rel", "video:mv")]


def test_gpu_session_releases_on_exception(fresh_gate, monkeypatch):
    released = []
    monkeypatch.setattr(grp, "_orchestrator_request", lambda slot, mb: None)
    monkeypatch.setattr(grp, "_orchestrator_release", lambda slot: released.append(slot))
    with pytest.raises(RuntimeError):
        with grp.gpu_session(JobKind.VIDEO_RENDER, "op", vram_estimate_mb=8000, slot_id="s"):
            raise RuntimeError("boom")
    assert fresh_gate._gpu_holder is None       # gate released despite the raise
    assert released == ["s"]                     # orchestrator released too
