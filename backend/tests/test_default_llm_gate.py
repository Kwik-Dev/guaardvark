"""The master cloud switch must gate every non-chat call site.

``GUAARDVARK_OPENAI_BASE_URL`` / ``_MODEL`` only supply the endpoint — they are
not consent. ``get_default_llm`` / ``get_llm_for_startup`` / ``get_llm_instance``
must resolve through ``llm_provider.get_active_provider()`` (master switch +
selection), exactly like ``unified_chat_engine`` and
``character_generator_service._default_llm``.
"""
from unittest.mock import patch

import pytest

try:
    from backend import config
    from backend.services import llm_provider
    from backend.utils import llm_service
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def test_active_cloud_llm_is_none_when_master_switch_off(monkeypatch):
    # Endpoint configured in the environment, but the DB master switch is off
    # (get_active_provider() returns Ollama) — the call must stay local.
    monkeypatch.setenv("GUAARDVARK_OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("GUAARDVARK_OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(llm_provider, "get_active_provider", lambda: llm_provider.OLLAMA)

    assert llm_service._active_cloud_llm() is None


def test_active_cloud_llm_uses_the_active_provider(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(llm_provider, "get_active_provider", lambda: llm_provider.OPENAI)
    monkeypatch.setattr(llm_provider, "get_active_cloud_model", lambda: "gpt-4o-mini")

    with patch("backend.services.openai_provider.make_llamaindex_llm", lambda model=None: sentinel):
        assert llm_service._active_cloud_llm() is sentinel


def test_active_cloud_llm_uses_mistral_when_active(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(llm_provider, "get_active_provider", lambda: llm_provider.MISTRAL)
    monkeypatch.setattr(llm_provider, "get_active_cloud_model", lambda: "mistral-large-latest")

    with patch("backend.services.mistral_provider.make_llamaindex_llm", lambda model=None: sentinel):
        assert llm_service._active_cloud_llm() is sentinel


def test_openai_compatible_llm_delegates_to_the_gate(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(llm_service, "_active_cloud_llm", lambda: sentinel)
    assert llm_service._openai_compatible_llm() is sentinel


class _Decision:
    num_ctx = 4096
    resolved = True


def _stub_local_path(monkeypatch, local_sentinel):
    monkeypatch.setattr(llm_service, "Ollama", lambda **kwargs: local_sentinel)
    monkeypatch.setattr(llm_service, "DEFAULT_LLM", "gemma4:12b")
    monkeypatch.setattr(llm_service, "get_saved_active_model_name", lambda: None)
    monkeypatch.setattr(llm_service, "_default_chat_sampling", lambda: (0.0, {}))
    monkeypatch.setattr(
        "backend.utils.ollama_resource_manager.resolve_num_ctx_decision", lambda m: _Decision()
    )
    monkeypatch.setattr("backend.utils.ollama_resource_manager.mark_provisional", lambda *a, **k: None)
    monkeypatch.setattr("backend.utils.ollama_resource_manager.thinking_kwargs", lambda m: {})


def test_get_default_llm_stays_local_when_cloud_is_off(monkeypatch):
    local = object()
    monkeypatch.setattr(llm_service, "_active_cloud_llm", lambda: None)
    _stub_local_path(monkeypatch, local)

    assert llm_service.get_default_llm() is local


def test_get_default_llm_uses_the_cloud_llm_when_active(monkeypatch):
    cloud = object()
    monkeypatch.setattr(llm_service, "_active_cloud_llm", lambda: cloud)

    assert llm_service.get_default_llm() is cloud


def test_worker_without_db_context_stays_local(monkeypatch):
    # A Celery/worker call with no Flask app context: the setting read degrades
    # to unset, so cloud_models_enabled() is False and the gate must stay local
    # even though the endpoint is configured in the environment.
    monkeypatch.setenv("GUAARDVARK_OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(llm_provider, "_get_setting", lambda key: None)

    assert llm_provider.cloud_models_enabled() is False
    assert llm_provider.get_active_provider() == llm_provider.OLLAMA
    assert llm_service._active_cloud_llm() is None
