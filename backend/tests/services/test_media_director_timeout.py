"""The media director's Ollama calls carry a per-attempt timeout and fall back on it.

The seam is ``ollama.Client``: media_director builds a client per call (imported at
call time) so the limit applies to that call only.
"""
from __future__ import annotations

import logging

import httpx
import pytest

from backend.services import media_director as md


class _StalledClient:
    """Stands in for ollama.Client; every chat call times out like a hung model."""

    instances: list["_StalledClient"] = []

    def __init__(self, host=None, **kwargs):
        self.timeout = kwargs.get("timeout")
        self.calls: list[dict] = []
        _StalledClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        raise httpx.ReadTimeout("timed out")


@pytest.fixture
def stalled(monkeypatch):
    import ollama
    _StalledClient.instances = []
    monkeypatch.setattr(ollama, "Client", _StalledClient)
    monkeypatch.setattr(md, "verbatim_prompts_enabled", lambda: False)
    monkeypatch.setattr(md, "_director_candidates", lambda preferred=None: ["m1", "m2", "m3"])
    monkeypatch.setattr(md, "_resolve_model", lambda preferred: "m1")
    monkeypatch.setattr("backend.utils.ollama_resource_manager.think_payload", lambda m: {})
    return _StalledClient


def test_timeout_scales_with_batch_and_is_capped():
    assert md._director_timeout_s(1) == md.DIRECTOR_LOAD_ALLOWANCE_S + md.DIRECTOR_PER_PROMPT_S
    assert md._director_timeout_s(10) > md._director_timeout_s(2)
    assert md._director_timeout_s(500) == md.DIRECTOR_MAX_TIMEOUT_S


def test_enhance_prompts_returns_originals_after_one_timed_out_attempt(stalled, caplog):
    prompts = ["a red fox", "a quiet harbour"]
    with caplog.at_level(logging.WARNING, logger=md.log.name):
        out = md.enhance_prompts(prompts)

    assert out == prompts
    # One attempt: a stalled Ollama would make the next model wait out the same stall.
    assert len(stalled.instances) == 1
    assert [c["model"] for c in stalled.instances[0].calls] == ["m1"]
    timeout = stalled.instances[0].timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == md._director_timeout_s(2)
    assert timeout.connect == md.DIRECTOR_CONNECT_TIMEOUT_S
    assert any("timed out" in r.getMessage() for r in caplog.records)


def test_storyboard_from_concept_falls_back_on_timeout(stalled, caplog):
    with caplog.at_level(logging.WARNING, logger=md.log.name):
        res = md.storyboard_from_concept("a lighthouse at dawn", 3)

    assert res == {"treatment": None, "prompts": ["a lighthouse at dawn"] * 3}
    assert stalled.instances[0].timeout.read == md._director_timeout_s(3)
    assert any("timed out" in r.getMessage() for r in caplog.records)


def test_refine_edit_instruction_keeps_the_original_on_timeout(stalled, caplog):
    with caplog.at_level(logging.WARNING, logger=md.log.name):
        out = md.refine_edit_instruction("make the sky purple")

    assert out == "make the sky purple"
    assert stalled.instances[0].timeout.read == md._director_timeout_s(1)
    assert any("timed out" in r.getMessage() for r in caplog.records)


def test_a_non_timeout_failure_still_tries_the_next_model(monkeypatch):
    import ollama
    seen = []

    class _Client(_StalledClient):
        def chat(self, **kwargs):
            seen.append(kwargs["model"])
            if kwargs["model"] == "m1":
                raise RuntimeError("model not found")
            return {"message": {"content": '{"prompts": ["DIR a", "DIR b"]}'}}

    monkeypatch.setattr(ollama, "Client", _Client)
    monkeypatch.setattr(md, "verbatim_prompts_enabled", lambda: False)
    monkeypatch.setattr(md, "_director_candidates", lambda preferred=None: ["m1", "m2", "m3"])
    monkeypatch.setattr("backend.utils.ollama_resource_manager.think_payload", lambda m: {})

    assert md.enhance_prompts(["a", "b"]) == ["DIR a", "DIR b"]
    assert seen == ["m1", "m2"]
