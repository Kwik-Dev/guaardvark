"""Consent gate for the OpenAI-compatible route (#214 review).

Two rules:

1. The endpoint is always explicit. ``GUAARDVARK_OPENAI_BASE_URL`` is required;
   there is no implicit ``api.openai.com`` default ("a remote default is not the
   same thing as a remote option").
2. A bare ``OPENAI_API_KEY`` — common on developer machines for unrelated tools —
   is never read. Only the namespaced ``GUAARDVARK_OPENAI_API_KEY`` is accepted,
   and it is optional (local vLLM / Ollama need no key).
"""
import inspect

import pytest

try:
    from backend import config
    from backend.services import llm_provider, openai_provider
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def test_config_does_not_read_a_bare_openai_api_key():
    src = inspect.getsource(config)
    assert 'os.environ.get("OPENAI_API_KEY"' not in src
    assert "os.environ.get('OPENAI_API_KEY'" not in src


def test_no_base_url_means_unavailable(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "")

    assert openai_provider.available() is False
    assert llm_provider._openai_available() is False


def test_namespaced_key_alone_does_not_enable_a_remote_default(monkeypatch):
    # A key is not a destination: without an explicit base URL the route is off.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-namespaced")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "")

    assert openai_provider.available() is False
    assert llm_provider._openai_available() is False


def test_namespaced_key_plus_explicit_base_url_is_consent(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-namespaced")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1")

    assert openai_provider.available() is True
    assert llm_provider._openai_available() is True
    assert openai_provider._base_url() == "https://api.openai.com/v1"
    assert openai_provider.describe("gpt-4o-mini") == (
        "endpoint=https://api.openai.com/v1 model=gpt-4o-mini"
    )


def test_explicit_local_base_url_is_consent_without_a_key(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "http://localhost:11434/v1")

    assert openai_provider.available() is True
    assert openai_provider._base_url() == "http://localhost:11434/v1"
    assert openai_provider.describe() == (
        f"endpoint=http://localhost:11434/v1 model={config.OPENAI_DEFAULT_MODEL}"
    )


# ── the gate is consulted, and the right env var is named ─────────────────────

def test_selecting_openai_names_the_base_url_not_the_optional_key(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "")
    monkeypatch.setattr(llm_provider, "cloud_models_enabled", lambda: True)

    with pytest.raises(ValueError) as exc:
        llm_provider.set_active_provider("openai")

    msg = str(exc.value)
    assert "GUAARDVARK_OPENAI_BASE_URL" in msg
    assert "GUAARDVARK_OPENAI_API_KEY" not in msg


def test_provider_state_exposes_both_env_names(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "")
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")

    by_id = {p["id"]: p for p in llm_provider.provider_state()["providers"]}
    assert by_id["openai"]["required_env"] == "GUAARDVARK_OPENAI_BASE_URL"
    assert by_id["openai"]["key_env"] == "GUAARDVARK_OPENAI_API_KEY"
    assert by_id["mistral"]["required_env"] == "MISTRAL_API_KEY"


def _stub_ollama(monkeypatch):
    import ollama

    monkeypatch.setattr(
        ollama, "chat",
        lambda **kwargs: {"message": {"content": '{"ok": true}'}},
    )


def test_character_generator_respects_the_master_cloud_gate(monkeypatch):
    """A configured base URL is not consent: the Character Generator must stay on
    Ollama while cloud models are off / Ollama is the active provider."""
    from backend.services import character_generator_service as cgs
    from backend.services import openai_provider as op

    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    calls = {"openai": 0}

    def fake_chat(**kwargs):
        calls["openai"] += 1
        return {"message": {"content": '{"ok": true}'}}

    monkeypatch.setattr(op, "chat", fake_chat)
    monkeypatch.setattr(llm_provider, "get_active_provider", lambda: llm_provider.OLLAMA)
    _stub_ollama(monkeypatch)

    assert cgs._default_llm(system="s", user="u") == '{"ok": true}'
    assert calls["openai"] == 0


def test_character_generator_uses_openai_when_it_is_the_active_provider(monkeypatch):
    from backend.services import character_generator_service as cgs
    from backend.services import openai_provider as op

    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    calls = {"openai": 0}

    def fake_chat(**kwargs):
        calls["openai"] += 1
        return {"message": {"content": '{"ok": true}'}}

    monkeypatch.setattr(op, "chat", fake_chat)
    monkeypatch.setattr(llm_provider, "get_active_provider", lambda: llm_provider.OPENAI)
    monkeypatch.setattr(llm_provider, "get_openai_model", lambda: "gpt-4o-mini")

    assert cgs._default_llm(system="s", user="u") == '{"ok": true}'
    assert calls["openai"] == 1
