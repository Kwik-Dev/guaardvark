"""media_director must use the consented cloud provider instead of local Ollama (issue #2).

Every local director call in ``media_director`` wakes the chat model — ~16 GB on a 48 GB
Mac — in the SAME request that then needs the offline image family's 21 GB, so
GlobalLoadGate refuses the render the request itself just made impossible. With cloud
consent the rewrite stops costing the render its budget.

These tests also pin the trap that made a naive fix a no-op: the consent gate reads a DB
``Setting`` row, and ``db.session`` raises outside an app context, so a bare worker thread
(``BatchImageGenerator._queue_worker``) reads "no consent" regardless of the operator's
choice. ``llm_provider.app_context_if_needed`` is the opt-in wrap for that.

No network, no model loads, no app context: every provider boundary is stubbed.
"""
from __future__ import annotations

import json
import threading
from contextlib import nullcontext

import pytest

try:
    from backend.services import llm_provider, media_director
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


ENHANCED = ["a lighthouse in a storm, wide shot"]
PROMPTS_JSON = json.dumps({"prompts": ENHANCED})
STORYBOARD_JSON = json.dumps({"treatment": "a quiet town", "prompts": ["p1", "p2"]})
EDIT_JSON = json.dumps({"instruction": "make the jacket red, keeping the face and framing"})


class _LocalOllama:
    """Stands in for the local director chain."""

    def __init__(self, content: str = "", *, explode: bool = False):
        self.calls: list = []
        self._content = content
        self._explode = explode

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self._explode:
            raise AssertionError("local Ollama must not be reached on a consented cloud route")
        return {"message": {"content": self._content}}


def _wire(monkeypatch, *, consent: bool, cloud_content: str = "", local_content: str = "",
          explode_local: bool = False):
    """Stub the consent gate and both provider boundaries. Returns (cloud_calls, local)."""
    cloud_calls: list = []

    monkeypatch.setattr(media_director, "verbatim_prompts_enabled", lambda: False)
    # The real helper pushes a Flask app context; these tests run without one.
    monkeypatch.setattr(llm_provider, "app_context_if_needed", nullcontext)
    monkeypatch.setattr(llm_provider, "is_openai_active", lambda: consent)
    monkeypatch.setattr(llm_provider, "get_openai_model", lambda: "deepseek-v4-flash:cloud")
    monkeypatch.setattr(media_director, "_director_candidates", lambda preferred=None: ["fake-local:1b"])
    monkeypatch.setattr(media_director, "_resolve_model", lambda m: m or "fake-local:1b")

    from backend.services import openai_provider

    def _cloud_chat(**kwargs):
        cloud_calls.append(kwargs)
        return {"message": {"content": cloud_content}}

    monkeypatch.setattr(openai_provider, "chat", _cloud_chat)

    local = _LocalOllama(local_content, explode=explode_local)
    import sys
    monkeypatch.setitem(sys.modules, "ollama", local)
    return cloud_calls, local


# --- enhance_prompts --------------------------------------------------------

def test_enhance_prompts_uses_cloud_and_never_touches_ollama_when_consented(monkeypatch):
    cloud_calls, local = _wire(
        monkeypatch, consent=True, cloud_content=PROMPTS_JSON, explode_local=True
    )

    out = media_director.enhance_prompts(["a lighthouse"], prompt_style="natural")

    assert out == ENHANCED
    assert len(cloud_calls) == 1
    assert cloud_calls[0]["model"] == "deepseek-v4-flash:cloud"
    assert cloud_calls[0]["stream"] is False
    # JSON mode, so a prose model cannot answer with a preamble the parser must strip.
    assert cloud_calls[0]["options"]["response_format"] == {"type": "json_object"}
    assert local.calls == []


def test_enhance_prompts_stays_local_without_consent(monkeypatch):
    cloud_calls, local = _wire(
        monkeypatch, consent=False, local_content=PROMPTS_JSON
    )

    out = media_director.enhance_prompts(["a lighthouse"], prompt_style="natural")

    assert out == ENHANCED
    assert cloud_calls == []          # endpoint may be configured; consent is what routes
    assert len(local.calls) == 1


def test_enhance_prompts_falls_back_to_local_when_cloud_returns_the_wrong_count(monkeypatch):
    # The enrich contract is exactly N prompts; a model that answers with fewer must not
    # silently truncate the batch — the local chain still gets its turn.
    cloud_calls, local = _wire(
        monkeypatch,
        consent=True,
        cloud_content=json.dumps({"prompts": ["only-one"]}),
        local_content=json.dumps({"prompts": ["local-a", "local-b"]}),
    )

    out = media_director.enhance_prompts(["a", "b"], prompt_style="natural")

    assert out == ["local-a", "local-b"]
    assert len(cloud_calls) == 1
    assert len(local.calls) == 1


def test_enhance_prompts_falls_back_to_local_when_the_cloud_call_raises(monkeypatch):
    cloud_calls, local = _wire(monkeypatch, consent=True, local_content=PROMPTS_JSON)
    from backend.services import openai_provider

    def _boom(**_kwargs):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(openai_provider, "chat", _boom)

    out = media_director.enhance_prompts(["a lighthouse"], prompt_style="natural")

    assert out == ENHANCED           # best-effort contract preserved
    assert len(local.calls) == 1


def test_enhance_prompts_routes_to_cloud_from_a_bare_worker_thread(monkeypatch):
    """The batch-image shape: _queue_worker is a bare thread with no app context."""
    from flask import has_app_context

    cloud_calls, local = _wire(
        monkeypatch, consent=True, cloud_content=PROMPTS_JSON, explode_local=True
    )
    out: dict = {}

    def worker():
        out["has_app_context"] = has_app_context()
        out["prompts"] = media_director.enhance_prompts(["a lighthouse"], prompt_style="natural")

    thread = threading.Thread(target=worker, name="batch-image-worker")
    thread.start()
    thread.join()

    assert out["has_app_context"] is False   # genuinely the worker shape
    assert out["prompts"] == ENHANCED
    assert len(cloud_calls) == 1
    assert local.calls == []


# --- storyboard_from_concept / refine_edit_instruction ----------------------

def test_storyboard_from_concept_uses_cloud_when_consented(monkeypatch):
    cloud_calls, local = _wire(
        monkeypatch, consent=True, cloud_content=STORYBOARD_JSON, explode_local=True
    )

    res = media_director.storyboard_from_concept("a quiet town", 2)

    assert res["prompts"] == ["p1", "p2"]
    assert res["treatment"] == "a quiet town"
    assert len(cloud_calls) == 1
    assert local.calls == []


def test_refine_edit_instruction_uses_cloud_when_consented(monkeypatch):
    cloud_calls, local = _wire(
        monkeypatch, consent=True, cloud_content=EDIT_JSON, explode_local=True
    )

    out = media_director.refine_edit_instruction("make the jacket red")

    assert out == "make the jacket red, keeping the face and framing"
    assert len(cloud_calls) == 1
    assert local.calls == []


def test_refine_edit_instruction_keeps_the_original_without_consent(monkeypatch):
    cloud_calls, local = _wire(monkeypatch, consent=False, explode_local=True)

    out = media_director.refine_edit_instruction("make the jacket red")

    assert out == "make the jacket red"   # unchanged on any failure, as before
    assert cloud_calls == []


# --- the consent read itself ------------------------------------------------

def test_app_context_if_needed_is_a_noop_when_a_context_exists():
    try:
        from backend.app import app
    except Exception:
        pytest.skip("no app singleton in this environment")
    from flask import has_app_context

    with app.app_context():
        with llm_provider.app_context_if_needed():
            assert has_app_context()


def test_app_context_if_needed_makes_a_setting_readable_from_a_worker_thread():
    """The trap: outside an app context the gate cannot read the DB at all.

    Uses a real ``Setting`` row already in this install and only READS it — no write, no
    fixture DB — so the assertion is "the wrap restores the operator's actual value to a
    worker thread", not a stubbed one.
    """
    try:
        from backend.app import app
    except Exception:
        pytest.skip("no app singleton in this environment")

    with app.app_context():
        from backend.models import Setting
        keys = [row.key for row in Setting.query.limit(50).all()]
        key = next((k for k in keys if llm_provider._get_setting(k) not in (None, "")), None)
        if key is None:
            pytest.skip("no readable Setting row in this install")

    out: dict = {}

    def worker():
        out["bare"] = llm_provider._get_setting(key)          # no context: the gate's view
        with llm_provider.app_context_if_needed():
            out["wrapped"] = llm_provider._get_setting(key)   # opt-in wrap

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert out["bare"] is None, "expected the documented no-context degradation"
    assert out["wrapped"] is not None


def test_brain_state_skips_the_warmup_when_cloud_chat_is_active(monkeypatch):
    try:
        from backend.services.brain_state import BrainState
    except Exception:
        pytest.skip("brain_state not importable in this environment")

    monkeypatch.setattr(llm_provider, "app_context_if_needed", nullcontext)
    monkeypatch.setattr(llm_provider, "cloud_active", lambda: True)
    assert BrainState._cloud_chat_active() is True

    monkeypatch.setattr(llm_provider, "cloud_active", lambda: False)
    assert BrainState._cloud_chat_active() is False


def test_brain_state_warms_locally_when_consent_cannot_be_determined(monkeypatch):
    """Unknown consent keeps today's behaviour rather than silently disabling warm-up."""
    try:
        from backend.services.brain_state import BrainState
    except Exception:
        pytest.skip("brain_state not importable in this environment")

    def _boom():
        raise RuntimeError("provider layer unavailable")

    monkeypatch.setattr(llm_provider, "app_context_if_needed", _boom)
    assert BrainState._cloud_chat_active() is False


def test_the_cloud_route_needs_the_context_push_from_a_worker_thread(monkeypatch):
    """End-to-end through the REAL helper, gate and provider dispatch.

    The DB is emulated to fail exactly the way the real one does — ``db.session`` raises
    outside an app context and ``_get_setting`` swallows it into "unset", which the gate
    reads as NO CONSENT. Nothing here is stubbed between ``enhance_prompts`` and the
    provider call except the HTTP client itself, so this is the assertion that the fix is
    not a no-op on the batch path.
    """
    import sys

    from flask import has_app_context

    from backend import config

    values = {"cloud_models_enabled": "true", "llm_provider": "openai"}

    def _db_setting(key):
        try:
            if not has_app_context():
                raise RuntimeError("Working outside of application context")
            return values.get(key)
        except Exception:
            return None          # exactly what llm_provider._get_setting does

    monkeypatch.setattr(llm_provider, "_get_setting", _db_setting)
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://endpoint.invalid/v1")
    monkeypatch.setattr(config, "OPENAI_DEFAULT_MODEL", "stub-cloud-model")
    monkeypatch.setattr(media_director, "verbatim_prompts_enabled", lambda: False)
    monkeypatch.setattr(media_director, "_director_candidates", lambda preferred=None: ["fake-local:1b"])
    monkeypatch.setitem(sys.modules, "ollama", _LocalOllama(PROMPTS_JSON))

    cloud_calls: list = []

    def _cloud_chat(**kwargs):
        cloud_calls.append(kwargs)
        return {"message": {"content": PROMPTS_JSON}}

    from backend.services import openai_provider
    monkeypatch.setattr(openai_provider, "chat", _cloud_chat)

    out: dict = {}

    def worker():
        # The gate's own view of the box, from the worker's thread.
        out["consent_without_push"] = llm_provider.cloud_models_enabled()
        with llm_provider.app_context_if_needed():
            out["consent_with_push"] = llm_provider.cloud_models_enabled()
        out["prompts"] = media_director.enhance_prompts(["a lighthouse"], prompt_style="natural")

    thread = threading.Thread(target=worker, name="batch-image-worker")
    thread.start()
    thread.join()

    assert out["consent_without_push"] is False, "expected the documented no-context fallback to local"
    assert out["consent_with_push"] is True
    assert out["prompts"] == ENHANCED
    assert len(cloud_calls) == 1
    assert cloud_calls[0]["model"] == "stub-cloud-model"


def test_music_video_director_reads_consent_from_a_worker_thread(monkeypatch):
    """The same trap hit four pre-existing "cloud-aware" sites.

    ``music_video_director._director_chat`` (with music_prompt_rewriter,
    character_bible_from_refs and production_swarm_tasks) checked
    ``is_openai_active()`` from Celery worker threads, where the DB-backed read fails
    closed to LOCAL — so the cloud route those files were built around never fired.
    """
    import threading

    from flask import has_app_context

    from backend import config
    from backend.services import music_video_director as mvd

    values = {"cloud_models_enabled": "true", "llm_provider": "openai"}

    def _db_setting(key):
        try:
            if not has_app_context():
                raise RuntimeError("Working outside of application context")
            return values.get(key)
        except Exception:
            return None

    monkeypatch.setattr(llm_provider, "_get_setting", _db_setting)
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://endpoint.invalid/v1")
    monkeypatch.setattr(config, "OPENAI_DEFAULT_MODEL", "stub-cloud-model")

    cloud_calls: list = []

    def _cloud_chat(**kwargs):
        cloud_calls.append(kwargs)
        return {"message": {"content": json.dumps({"prompts": {"0": "a shot"}})} }

    from backend.services import openai_provider
    monkeypatch.setattr(openai_provider, "chat", _cloud_chat)

    out: dict = {}

    def worker():
        out["content"] = mvd._director_chat(
            ollama=_LocalOllama(explode=True),   # any local call is a failure
            model="fake-local:1b",
            system="s",
            user="u",
            batch_len=1,
            rich=True,
        )[1]

    thread = threading.Thread(target=worker, name="music-video-worker")
    thread.start()
    thread.join()

    assert len(cloud_calls) == 1
    assert cloud_calls[0]["model"] == "stub-cloud-model"
    assert out["content"].strip()
