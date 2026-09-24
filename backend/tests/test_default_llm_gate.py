"""The master cloud switch must gate every cloud route — and only per-call ones.

``GUAARDVARK_OPENAI_BASE_URL`` / ``_MODEL`` only supply the endpoint — they are
not consent. Cloud routing happens in one place: :func:`get_llm_instance`, which
resolves ``llm_provider.get_active_provider()`` on every call, exactly like
``unified_chat_engine`` and ``character_generator_service._default_llm``.

:func:`get_default_llm` and :func:`get_llm_for_startup` are deliberately
LOCAL-ONLY. Their results are held for the process lifetime (``OrchestratorService.llm``,
``brain_state.llm``, ``agent_router._llm``, the tools' ``_llm``,
``app.config["LLAMA_INDEX_LLM"]``), so a cloud LLM handed out there would keep
calling the endpoint after the operator turns the switch off — breaking the
consent guarantee the switch advertises.
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


def test_get_default_llm_stays_local_when_cloud_is_active(monkeypatch):
    # The long-lived holders of this result never re-resolve the provider, so it
    # must not be the cloud LLM even while cloud is on.
    cloud = object()
    local = object()
    monkeypatch.setattr(llm_service, "_active_cloud_llm", lambda: cloud)
    _stub_local_path(monkeypatch, local)

    assert llm_service.get_default_llm() is local


def test_get_llm_for_startup_stays_local_when_cloud_is_active(monkeypatch):
    # This instance lands in app.config["LLAMA_INDEX_LLM"] and is held for the
    # process lifetime; pinning a cloud provider here is the leak from item 1 of
    # the #214 review, so it must stay local regardless of the switch.
    cloud = object()
    local = object()
    monkeypatch.setattr(llm_service, "_active_cloud_llm", lambda: cloud)
    _stub_local_path(monkeypatch, local)
    # No Ollama to probe at startup; the local path must not need it.
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no ollama in tests")),
    )

    assert llm_service.get_llm_for_startup() is local


def test_get_llm_instance_routes_to_the_cloud_provider(monkeypatch):
    # This is the ONE call site that may hand out a cloud LLM: it resolves per
    # call, so turning the switch off takes effect on the next request.
    sentinel = object()
    monkeypatch.setattr(llm_service, "_active_cloud_llm", lambda: sentinel)

    assert llm_service.get_llm_instance() is sentinel


class _LocalOllama:
    model = "gemma4:e4b"


class _CloudWrapper:
    # What openai_provider/mistral_provider make_llamaindex_llm hands back.
    model = "gpt-4o-mini"


def test_local_chat_model_name_trusts_a_local_ollama_instance(monkeypatch):
    monkeypatch.setattr(llm_service, "Ollama", _LocalOllama)

    assert llm_service.local_chat_model_name(_LocalOllama()) == "gemma4:e4b"


def test_local_chat_model_name_rejects_a_cloud_model_id(monkeypatch):
    # A cloud wrapper's .model is the provider's id; passing it to local Ollama
    # fails with model-not-found once the switch is off.
    monkeypatch.setattr(llm_service, "Ollama", _LocalOllama)
    monkeypatch.setattr(llm_service, "get_saved_active_model_name", lambda: "gemma4:12b")

    assert llm_service.local_chat_model_name(_CloudWrapper()) == "gemma4:12b"


def test_local_chat_model_name_falls_back_to_the_default(monkeypatch):
    monkeypatch.setattr(llm_service, "Ollama", _LocalOllama)
    monkeypatch.setattr(llm_service, "get_saved_active_model_name", lambda: None)
    monkeypatch.setattr(llm_service, "DEFAULT_LLM", "gemma4:12b")

    assert llm_service.local_chat_model_name(_CloudWrapper()) == "gemma4:12b"


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
