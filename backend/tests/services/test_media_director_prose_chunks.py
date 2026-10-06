"""Prose director batches are split into chunks of DIRECTOR_BATCH_SIZE.

A single prose call over the 4096-token window returns nothing, so a large batch
would lose every rewrite. The seam is ``ollama.Client``, which media_director
imports at call time.
"""
from __future__ import annotations

import json
import re

import httpx
import pytest

from backend.services import media_director as md


def _ideas(user_msg: str) -> list[str]:
    """The numbered ideas the director was sent, in order."""
    block = user_msg.split("INPUT IDEAS", 1)[1]
    return re.findall(r"^\d+\. (.+)$", block, flags=re.MULTILINE)


class _Client:
    """Stands in for ollama.Client; rewrites every idea it is sent as 'DIR <idea>'."""

    calls: list[dict] = []
    fail_when = None  # callable(call_index, ideas) -> Exception | "short" | None

    def __init__(self, host=None, **kwargs):
        self.timeout = kwargs.get("timeout")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def chat(self, **kwargs):
        user = next(m["content"] for m in kwargs["messages"] if m["role"] == "user")
        ideas = _ideas(user)
        idx = len(_Client.calls)
        _Client.calls.append({"model": kwargs["model"], "ideas": ideas,
                              "options": kwargs.get("options"),
                              "system": kwargs["messages"][0]["content"]})
        outcome = _Client.fail_when(idx, ideas) if _Client.fail_when else None
        if isinstance(outcome, BaseException):
            raise outcome
        out = [f"DIR {i}" for i in ideas]
        if outcome == "short":
            out = out[:-1]
        return {"message": {"content": json.dumps({"prompts": out})}}


@pytest.fixture
def client(monkeypatch):
    import ollama
    _Client.calls = []
    _Client.fail_when = None
    monkeypatch.setattr(ollama, "Client", _Client)
    monkeypatch.setattr(md, "verbatim_prompts_enabled", lambda: False)
    monkeypatch.setattr(md, "_director_candidates", lambda preferred=None: ["m1", "m2", "m3"])
    monkeypatch.setattr("backend.utils.ollama_resource_manager.think_payload", lambda m: {})
    return _Client


def test_batch_size_is_twelve():
    assert md.DIRECTOR_BATCH_SIZE == 12


def test_twenty_prose_prompts_make_two_calls_and_return_twenty_rewrites(client):
    prompts = [f"idea {i}" for i in range(20)]

    out = md.enhance_prompts(prompts, prompt_style="natural")

    assert out == [f"DIR idea {i}" for i in range(20)]
    assert len(client.calls) == 2
    assert client.calls[0]["ideas"] == prompts[:12]
    assert client.calls[1]["ideas"] == prompts[12:]
    assert all(c["system"] == md._SYSTEM_ENHANCE_IMAGE_NATURAL for c in client.calls)
    # Each chunk's output budget is sized to that chunk, not to the whole batch.
    assert client.calls[1]["options"]["num_predict"] == min(4096, 320 * 8 + 256)


def test_a_prose_batch_within_the_size_is_one_call(client):
    prompts = [f"idea {i}" for i in range(12)]

    assert md.enhance_prompts(prompts, prompt_style="natural") == [f"DIR {p}" for p in prompts]
    assert len(client.calls) == 1


def test_phrase_batches_are_not_chunked(client):
    prompts = [f"idea {i}" for i in range(20)]

    assert md.enhance_prompts(prompts) == [f"DIR {p}" for p in prompts]
    assert len(client.calls) == 1
    assert client.calls[0]["system"] == md._SYSTEM_ENHANCE_IMAGE


def test_a_chunk_no_model_rewrites_keeps_its_originals_and_the_rest_still_run(client):
    prompts = [f"idea {i}" for i in range(20)]
    # The second chunk comes back one short from every model on the ladder.
    client.fail_when = lambda idx, ideas: "short" if ideas[0] == "idea 12" else None

    out = md.enhance_prompts(prompts, prompt_style="natural")

    assert out[:12] == [f"DIR idea {i}" for i in range(12)]
    assert out[12:] == prompts[12:]
    assert [c["model"] for c in client.calls] == ["m1", "m1", "m2", "m3"]


def test_a_timeout_keeps_the_originals_for_that_chunk_and_every_later_one(client):
    prompts = [f"idea {i}" for i in range(30)]
    client.fail_when = lambda idx, ideas: httpx.ReadTimeout("timed out") if idx == 1 else None

    out = md.enhance_prompts(prompts, prompt_style="natural")

    assert out[:12] == [f"DIR idea {i}" for i in range(12)]
    assert out[12:] == prompts[12:]
    # No third chunk and no second model: the stalled queue would only stall again.
    assert len(client.calls) == 2
