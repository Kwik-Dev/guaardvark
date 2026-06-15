"""VRAM admission tests for OfflineImageGenerator.

Born from the 2026-06-10 chat image-gen OOM: a flat 3500MB estimate let the
free-VRAM check pass while Z-Image actually allocated 9.9GB into a card already
holding ~9.3GB of resident Ollama models. These tests pin the fix:
family-aware estimates + canonical Ollama eviction when the card is too full.
"""
import pytest

import backend.services.offline_image_generator as oig
import backend.services.gpu_resource_policy as grp


GB = 1024 * 1024 * 1024


@pytest.fixture
def gen():
    return oig.OfflineImageGenerator()


@pytest.fixture
def spy(monkeypatch, gen):
    """Record eviction + orchestrator calls; pretend we're on a CUDA box."""
    calls = {"evicted": 0, "requests": []}
    monkeypatch.setattr(grp, "evict_ollama_models", lambda: calls.__setitem__("evicted", calls["evicted"] + 1) or True)

    class _Orch:
        def request_model(self, slot_id, vram_estimate_mb, priority=50, **kw):
            calls["requests"].append((slot_id, vram_estimate_mb, priority))

    import backend.services.gpu_memory_orchestrator as gmo
    monkeypatch.setattr(gmo, "get_orchestrator", lambda: _Orch())
    monkeypatch.setattr(gen, "_device", "cuda")
    return calls


def _set_vram(monkeypatch, free_gb, total_gb=16):
    monkeypatch.setattr(oig.torch.cuda, "mem_get_info", lambda: (int(free_gb * GB), int(total_gb * GB)))


# --- Family-aware estimates (the 3500 flat estimate is dead) -----------------

def test_estimate_zimage(gen):
    assert gen._vram_estimate_mb("Tongyi-MAI/Z-Image-Turbo") == 11000


def test_estimate_sdxl(gen):
    assert gen._vram_estimate_mb("stabilityai/stable-diffusion-xl-base-1.0") == 8000


def test_estimate_sd15(gen):
    assert gen._vram_estimate_mb("runwayml/stable-diffusion-v1-5") == 4000


# --- Eviction decision --------------------------------------------------------

def test_tight_vram_evicts_ollama_and_registers_real_estimate(gen, spy, monkeypatch):
    # The exact 2026-06-10 scenario: ~6GB free, Z-Image needs ~11GB.
    _set_vram(monkeypatch, free_gb=6)
    gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")
    assert spy["evicted"] == 1
    assert spy["requests"] == [("sd:pipeline", 11000, 85)]


def test_roomy_vram_does_not_evict(gen, spy, monkeypatch):
    # Negative case: 15GB free fits SDXL + margin — chat model must survive.
    _set_vram(monkeypatch, free_gb=15)
    gen._ensure_vram_for_pipeline("stabilityai/stable-diffusion-xl-base-1.0")
    assert spy["evicted"] == 0
    assert spy["requests"] == [("sd:pipeline", 8000, 85)]


def test_margin_tips_the_decision(gen, spy, monkeypatch):
    # 12GB free fits 11000MB raw but NOT with the 1.6GB margin (10% of 16GB).
    _set_vram(monkeypatch, free_gb=12)
    gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")
    assert spy["evicted"] == 1


def test_already_resident_pipeline_skips_admission(gen, spy, monkeypatch):
    _set_vram(monkeypatch, free_gb=1)
    gen._pipeline = object()
    gen._current_model = "Tongyi-MAI/Z-Image-Turbo"
    gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")
    assert spy["evicted"] == 0
    assert spy["requests"] == []


def test_cpu_device_skips_vram_check_but_still_registers(gen, spy, monkeypatch):
    monkeypatch.setattr(gen, "_device", "cpu")
    monkeypatch.setattr(
        oig.torch.cuda, "mem_get_info",
        lambda: pytest.fail("mem_get_info must not be called on a CPU box"),
    )
    gen._ensure_vram_for_pipeline("runwayml/stable-diffusion-v1-5")
    assert spy["evicted"] == 0
    assert spy["requests"] == [("sd:pipeline", 4000, 85)]


def test_admission_failure_never_raises(gen, monkeypatch):
    # Orchestrator down, CUDA query exploding — generation must still proceed.
    monkeypatch.setattr(gen, "_device", "cuda")
    monkeypatch.setattr(oig.torch.cuda, "mem_get_info", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    gen._ensure_vram_for_pipeline("Tongyi-MAI/Z-Image-Turbo")  # must not raise
