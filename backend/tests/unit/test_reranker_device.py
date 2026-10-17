"""The reranker loads where this process can run it: a hidden GPU means CPU.

NVML reports the card even where CUDA_VISIBLE_DEVICES hides it, as in every
Celery worker, so NVML alone must not choose "cuda".
"""

import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest

from backend.utils import reranker


@pytest.fixture(autouse=True)
def _clean_reranker_state():
    saved = (reranker._model, reranker._model_device, reranker._model_vram_mb,
             reranker._inflight, reranker._load_failed_reason)
    yield
    (reranker._model, reranker._model_device, reranker._model_vram_mb,
     reranker._inflight, reranker._load_failed_reason) = saved


@pytest.fixture
def card_with_room(monkeypatch):
    """NVML sees a card with plenty free; records whether it was asked."""
    probe = ModuleType("backend.services.gpu_resource_coordinator")
    calls = []

    def has_gpu():
        calls.append("has_gpu")
        return True

    def get_available_vram():
        calls.append("get_available_vram")
        return {"success": True, "available_mb": 24000}

    probe.has_gpu = has_gpu
    probe.get_available_vram = get_available_vram
    monkeypatch.setitem(sys.modules, probe.__name__, probe)
    monkeypatch.delenv("GUAARDVARK_MCP_PROCESS", raising=False)
    monkeypatch.setenv("GUAARDVARK_RERANK_MIN_VRAM_MB", "3000")
    return calls


def _fake_torch(available: bool) -> ModuleType:
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: available)
    torch.float16 = "float16"
    return torch


def test_an_empty_cuda_visible_devices_means_no_gpu_here(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    # Decided from the variable alone: torch may have seen the card before it was blanked.
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True))
    assert reranker._cuda_unusable_reason() == "CUDA_VISIBLE_DEVICES is empty"


@pytest.mark.parametrize("available, expected", [
    (True, None),
    (False, "torch sees no CUDA device"),
])
def test_otherwise_torch_decides(monkeypatch, available, expected):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(available))
    assert reranker._cuda_unusable_reason() == expected


def test_a_broken_cuda_check_counts_as_no_gpu(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    torch = _fake_torch(True)

    def _raise():
        raise RuntimeError("driver mismatch")

    torch.cuda = SimpleNamespace(is_available=_raise)
    monkeypatch.setitem(sys.modules, "torch", torch)
    assert reranker._cuda_unusable_reason() == "CUDA check failed (RuntimeError)"


def test_a_hidden_gpu_picks_the_cpu_and_says_why(monkeypatch, card_with_room, caplog):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    with caplog.at_level(logging.INFO, logger=reranker.logger.name):
        assert reranker._pick_device() == "cpu"
    assert card_with_room == [], "NVML sees the card regardless; it must not decide here"
    assert any("no usable GPU in this process" in r.getMessage()
               and "CUDA_VISIBLE_DEVICES is empty" in r.getMessage()
               for r in caplog.records)


def test_a_visible_gpu_with_room_still_takes_the_card(monkeypatch, card_with_room):
    monkeypatch.setattr(reranker, "_cuda_unusable_reason", lambda: None)
    assert reranker._pick_device() == "cuda"
    assert card_with_room == ["has_gpu", "get_available_vram"]


def test_a_worker_loads_the_model_on_cpu_in_full_precision_and_reranks(monkeypatch, card_with_room):
    """The worker path end to end, with the CrossEncoder stubbed: a CPU load, no
    fp16, no VRAM reported, and a rerank that applies and names its device."""
    loads = []

    class _FakeCrossEncoder:
        def __init__(self, name, **kwargs):
            loads.append((name, kwargs))

        def predict(self, pairs, show_progress_bar=False):
            return [float(len(p[1])) for p in pairs]

    st = ModuleType("sentence_transformers")
    st.CrossEncoder = _FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    monkeypatch.setattr(reranker, "is_installed", lambda: True)
    monkeypatch.setattr(reranker, "is_enabled", lambda: True)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    reranker._model = None
    reranker._model_device = None
    reranker._model_vram_mb = 0
    reranker._load_failed_reason = None

    out, info = reranker.rerank("q", [{"text": "short"}, {"text": "a much longer passage"}])

    assert len(loads) == 1
    _name, kwargs = loads[0]
    assert kwargs["device"] == "cpu"
    assert kwargs["local_files_only"] is True
    assert "model_kwargs" not in kwargs, "CPU stays fp32"
    assert reranker._model_device == "cpu" and reranker._model_vram_mb == 0
    assert info["applied"] is True and info["device"] == "cpu"
    assert out[0]["text"] == "a much longer passage"
    assert reranker._load_failed_reason is None
