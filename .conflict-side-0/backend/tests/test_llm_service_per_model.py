"""get_llm_instance(model=...) forwards its per-call knobs to build_ollama."""
from unittest.mock import patch

from backend.utils import llm_service


def _capture():
    calls = []

    def fake_build_ollama(model_name, **kwargs):
        calls.append((model_name, kwargs))
        return object()

    return calls, fake_build_ollama


def test_default_call_leaves_thinking_to_build_ollama():
    calls, fake = _capture()
    with patch("backend.utils.ollama_resource_manager.build_ollama", fake):
        llm = llm_service.get_llm_instance(model="gemma4:12b")
    assert llm is not None
    model, kwargs = calls[0]
    assert model == "gemma4:12b"
    assert "thinking" not in kwargs
    assert kwargs["json_mode"] is False
    assert kwargs["request_timeout"] == 180.0
    assert "context_window" not in kwargs


def test_explicit_knobs_are_forwarded():
    calls, fake = _capture()
    with patch("backend.utils.ollama_resource_manager.build_ollama", fake):
        llm_service.get_llm_instance(
            model="qwen3.5:9b", thinking=True, request_timeout=30,
            json_mode=True, num_ctx=4096, num_predict=512,
        )
    _, kwargs = calls[0]
    assert kwargs["thinking"] is True
    assert kwargs["request_timeout"] == 30.0
    assert kwargs["json_mode"] is True
    assert kwargs["context_window"] == 4096
    assert kwargs["additional_kwargs"] == {"num_predict": 512}


def test_thinking_false_is_forwarded_not_dropped():
    calls, fake = _capture()
    with patch("backend.utils.ollama_resource_manager.build_ollama", fake):
        llm_service.get_llm_instance(model="gemma4:12b", thinking=False)
    assert calls[0][1]["thinking"] is False


def test_construction_failure_returns_none():
    def boom(model_name, **kwargs):
        raise RuntimeError("no ollama")

    with patch("backend.utils.ollama_resource_manager.build_ollama", boom):
        assert llm_service.get_llm_instance(model="gemma4:12b") is None
