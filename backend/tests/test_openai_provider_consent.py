"""Consent gate for the OpenAI-compatible route (#214 review).

A bare ``OPENAI_API_KEY`` in the environment — common on developer machines for
unrelated tools — must never enable a cloud route. Consent is the namespaced
``GUAARDVARK_OPENAI_API_KEY`` or an explicit ``GUAARDVARK_OPENAI_BASE_URL``.
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


def test_no_consent_means_unavailable(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "")

    assert openai_provider.available() is False
    assert llm_provider._openai_available() is False


def test_namespaced_key_is_consent_and_defaults_to_public_endpoint(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-namespaced")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "")

    assert openai_provider.available() is True
    assert llm_provider._openai_available() is True
    # The public default is only reached because consent exists.
    assert openai_provider._base_url() == "https://api.openai.com/v1"
    assert "endpoint=https://api.openai.com/v1" in openai_provider.describe()


def test_explicit_local_base_url_is_consent_without_a_key(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "http://localhost:11434/v1")

    assert openai_provider.available() is True
    assert openai_provider._base_url() == "http://localhost:11434/v1"
    assert openai_provider.describe() == (
        f"endpoint=http://localhost:11434/v1 model={config.OPENAI_DEFAULT_MODEL}"
    )
