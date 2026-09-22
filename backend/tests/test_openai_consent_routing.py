"""Feature-level call sites must gate on cloud consent, not capability.

``openai_provider.available()`` is only "an endpoint is configured". These four
sites used to treat that as permission to call out, so an env-configured install
could reach the cloud with the master switch off. They now gate on
``llm_provider.is_openai_active()`` (capability AND consent) and fall back local.
"""
import pytest

try:
    from backend.services import llm_provider, openai_provider
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def capability_on(monkeypatch):
    """An endpoint is configured (capability), independent of consent."""
    monkeypatch.setattr(openai_provider, "available", lambda: True)


def _forbid_cloud(monkeypatch):
    calls = {"n": 0}

    def fake_chat(**kwargs):
        calls["n"] += 1
        return {"message": {"content": "cloud"}}

    monkeypatch.setattr(openai_provider, "chat", fake_chat)
    return calls


# ── character_bible_from_refs._default_consensus_llm ──────────────────────────

def test_consensus_llm_stays_local_without_consent(monkeypatch, capability_on):
    from backend.services import character_bible_from_refs as cbr
    import ollama

    calls = _forbid_cloud(monkeypatch)
    monkeypatch.setattr(llm_provider, "is_openai_active", lambda: False)
    monkeypatch.setattr("backend.utils.llm_service.get_saved_active_model_name", lambda: "gemma4:12b")
    monkeypatch.setattr("backend.utils.ollama_resource_manager.think_payload", lambda m: {})
    monkeypatch.setattr(ollama, "chat", lambda **k: {"message": {"content": '{"bible": "local"}'}})

    assert cbr._default_consensus_llm(system="s", user="u") == '{"bible": "local"}'
    assert calls["n"] == 0  # capability alone must not route


def test_consensus_llm_uses_cloud_with_consent(monkeypatch):
    from backend.services import character_bible_from_refs as cbr

    calls = _forbid_cloud(monkeypatch)
    monkeypatch.setattr(llm_provider, "is_openai_active", lambda: True)
    monkeypatch.setattr(llm_provider, "get_openai_model", lambda: "gpt-4o-mini")

    assert cbr._default_consensus_llm(system="s", user="u") == "cloud"
    assert calls["n"] == 1


# ── production_swarm_tasks._default_ollama_llm (Film Crew) ────────────────────

def test_filmcrew_llm_stays_local_without_consent(monkeypatch, capability_on):
    from backend.tasks import production_swarm_tasks as pst
    import ollama

    calls = _forbid_cloud(monkeypatch)
    monkeypatch.setattr(llm_provider, "is_openai_active", lambda: False)
    monkeypatch.setattr("backend.utils.ollama_resource_manager.think_payload", lambda m: {})
    monkeypatch.setattr("backend.services.ollama_chat_model.resolve_chat_model", lambda m: m)
    monkeypatch.setattr(ollama, "chat", lambda **k: {"message": {"content": "local"}})

    assert pst._default_ollama_llm(system="s", user="u") == "local"
    assert calls["n"] == 0


# ── music_video_director._director_chat ───────────────────────────────────────

class _LocalOllama:
    def chat(self, **kwargs):
        return {"message": {"content": '{"prompts": []}'}}


def test_director_chat_stays_local_without_consent(monkeypatch, capability_on):
    from backend.services import music_video_director as mvd

    calls = _forbid_cloud(monkeypatch)
    monkeypatch.setattr(llm_provider, "is_openai_active", lambda: False)
    monkeypatch.setattr("backend.utils.ollama_resource_manager.think_payload", lambda m: {})

    _parsed, raw = mvd._director_chat(
        ollama=_LocalOllama(), model="m", system="s", user="u", batch_len=1, rich=False,
    )
    assert raw == '{"prompts": []}'
    assert calls["n"] == 0


# ── music_prompt_rewriter.rewrite_music_prompt ────────────────────────────────

class _LocalResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": '{"style_prompt": "indie pop, piano", "negative_prompt": "no vocals", "tags_used": ["indie pop"]}'}}


def test_music_rewrite_stays_local_without_consent(monkeypatch, capability_on):
    from backend.utils import music_prompt_rewriter as mpr

    calls = _forbid_cloud(monkeypatch)
    monkeypatch.setattr(llm_provider, "is_openai_active", lambda: False)
    monkeypatch.setattr(mpr, "think_payload", lambda m: {})
    monkeypatch.setattr(mpr, "get_saved_active_model_name", lambda: "gemma4:e4b")
    monkeypatch.setattr(mpr.requests, "post", lambda *a, **k: _LocalResp())

    out = mpr.rewrite_music_prompt("calm piano")

    assert out and out["style_prompt"] == "indie pop, piano"
    assert calls["n"] == 0
