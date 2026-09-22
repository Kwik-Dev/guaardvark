"""The music prompt rewriter must gate on cloud consent, not capability.

``openai_provider.available()`` is only "an endpoint is configured". With the
master switch off, the rewriter must stay on local Ollama even when a base URL is
set. Mirrors ``test_default_llm_gate.py`` for this call site.
"""
import pytest

try:
    from backend.services import llm_provider, openai_provider
    from backend.utils import music_prompt_rewriter as mpr
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def _cloud_json(text: str = "indie pop, piano") -> dict:
    return {
        "message": {
            "content": (
                '{"style_prompt": "%s", "negative_prompt": "no vocals", '
                '"tags_used": ["indie pop"]}' % text
            )
        }
    }


class _LocalResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return _cloud_json("indie pop, piano")


def test_rewrite_stays_local_without_consent(monkeypatch):
    # Capability present (endpoint configured), consent off.
    monkeypatch.setattr(openai_provider, "available", lambda: True)
    monkeypatch.setattr(llm_provider, "is_openai_active", lambda: False)
    cloud = {"n": 0}
    monkeypatch.setattr(
        openai_provider, "chat", lambda **k: cloud.__setitem__("n", cloud["n"] + 1)
    )
    monkeypatch.setattr(mpr, "think_payload", lambda m: {})
    monkeypatch.setattr(mpr, "get_saved_active_model_name", lambda: "gemma4:e4b")
    monkeypatch.setattr(mpr.requests, "post", lambda *a, **k: _LocalResp())

    out = mpr.rewrite_music_prompt("calm piano")

    assert out and out["style_prompt"] == "indie pop, piano"
    assert cloud["n"] == 0


def test_rewrite_uses_cloud_with_consent(monkeypatch):
    monkeypatch.setattr(llm_provider, "is_openai_active", lambda: True)
    monkeypatch.setattr(llm_provider, "get_openai_model", lambda: "gpt-4o-mini")
    monkeypatch.setattr(openai_provider, "chat", lambda **k: _cloud_json("indie pop, piano"))

    out = mpr.rewrite_music_prompt("calm piano")

    assert out and out["style_prompt"] == "indie pop, piano"
