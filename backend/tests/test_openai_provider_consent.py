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
