"""model_digests and same_model tell Ollama model builds apart by manifest digest.

The /api/tags lists below have the shapes Ollama 0.40 returns: a pulled tag
listed twice (a ggml and a llamacpp manifest of the same pull) and a
``llamacpp:<digest>`` alias for the llamacpp one. Digests are made up.
"""
from __future__ import annotations

import pytest

from backend.utils import ollama_resource_manager as orm

LLAMACPP = "2cf98652fdc6" + "a" * 52
GGML = "da406dc37ca0" + "b" * 52
E4B = "c6eb396dbd59" + "0" * 52
QWEN = "bdbd181c33f2" + "1" * 52

TAGS = [
    {"name": "gemma4:e2b", "model": "gemma4:e2b", "digest": LLAMACPP},
    {"name": "gemma4:e2b", "model": "gemma4:e2b", "digest": GGML},
    {"name": f"llamacpp:{LLAMACPP}", "model": f"llamacpp:{LLAMACPP}", "digest": LLAMACPP},
    {"name": "gemma4:e4b", "model": "gemma4:e4b", "digest": E4B},
    {"name": "gemma4:latest", "model": "gemma4:latest", "digest": E4B},
    {"name": "qwen3:14b", "model": "qwen3:14b", "digest": QWEN},
]


def test_a_tag_listed_twice_has_both_digests():
    assert orm.model_digests("gemma4:e2b", TAGS) == {LLAMACPP[:12], GGML[:12]}


def test_a_llamacpp_alias_is_its_digest():
    assert orm.model_digests(f"llamacpp:{LLAMACPP}", TAGS) == {LLAMACPP[:12]}
    assert orm.model_digests(f"llamacpp:{LLAMACPP}", []) == {LLAMACPP[:12]}


def test_a_name_without_a_tag_is_latest():
    assert orm.model_digests("gemma4", TAGS) == {E4B[:12]}


@pytest.mark.parametrize("a, b, same", [
    ("gemma4:e2b", f"llamacpp:{LLAMACPP}", True),   # the alias is the same build
    ("gemma4:e4b", "gemma4:latest", True),           # two names, one digest
    ("gemma4:e2b", "gemma4", False),                  # same family, different build
    ("gemma4:e2b", "qwen3:14b", False),
    ("gemma4:e2b", "gemma4:e2b", True),
    ("Gemma4:E2B", "gemma4:e2b", True),
    ("gemma4:e2b", "not-installed:1b", None),
    ("gemma4:e2b", "", None),
    (None, "gemma4:e2b", None),
])
def test_same_model(a, b, same):
    assert orm.same_model(a, b, TAGS) is same


def test_an_unreachable_ollama_cannot_tell(monkeypatch):
    monkeypatch.setattr(orm, "_ollama_tags", lambda: [])

    assert orm.same_model("gemma4:e2b", "qwen3:14b") is None


def test_show_style_digests_are_read_too():
    tags = [{"name": "m:1", "digest": f"sha256:{QWEN}"}, {"name": "n:1", "digest": QWEN.upper()}]

    assert orm.same_model("m:1", "n:1", tags) is True
