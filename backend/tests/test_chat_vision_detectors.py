"""Vision support: one answer when the resolver runs, three when it cannot.

chat_utils.is_vision_model and ollama_resource_manager.is_vision_model both
return model_capability_resolver.sees_natively. Only their fallbacks, used when
the resolver cannot be imported, differ from each other and from the
resolver's own offline name guess. Pinned so a merge is deliberate.
"""
import re

import pytest

import backend.services.model_capability_resolver as resolver
from backend.services.model_capability_data import name_looks_vision
from backend.utils import chat_utils, ollama_resource_manager as orm


@pytest.mark.parametrize("answer", [True, False])
def test_both_wrappers_return_the_resolver_answer(monkeypatch, answer):
    monkeypatch.setattr(resolver, "sees_natively", lambda tag: answer)
    assert chat_utils.is_vision_model("any:tag") is answer
    assert orm.is_vision_model("any:tag") is answer


def _orm_fallback(tag):
    return any(re.search(p, tag.lower()) for p in orm.VISION_MODEL_PATTERNS)


CASES = [
    # tag, chat_utils fallback, ollama_resource_manager fallback, resolver name guess
    ("qwen3-vl:8b", False, True, True),
    ("gpt-4o", True, False, False),
    ("qwen3.5:9b", False, False, True),
    ("pixtral:12b", True, False, True),
    ("devstral:24b", False, False, True),
    ("gemma4:12b", True, True, True),
    ("deepseek-vl2", True, False, True),
    ("phi-3-vision", True, True, False),
]


@pytest.mark.parametrize("tag,chat_fb,orm_fb,guess", CASES)
def test_offline_fallbacks(tag, chat_fb, orm_fb, guess):
    assert chat_utils._is_vision_capable_by_name(tag) is chat_fb
    assert _orm_fallback(tag) is orm_fb
    assert name_looks_vision(tag) is guess
