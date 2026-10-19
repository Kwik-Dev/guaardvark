"""Real-time query detection: the three answers chat can get today.

UnifiedChatEngine._is_realtime_query decides whether the ReACT loop forces a
web search; intent_classifier.is_realtime_query (its keyword fallback, used
when the semantic model is not loaded) drives the web-search offer; and
capability_awareness.requires_realtime_data drives the honesty note. They
answer the same question differently; pinned here so a merge is deliberate.
"""
import pytest

import backend.utils.intent_classifier as ic
from backend.services.unified_chat_engine import UnifiedChatEngine
from backend.utils.capability_awareness import requires_realtime_data

CASES = [
    # message, engine, intent_classifier keyword fallback, capability_awareness
    ("what is the weather in Boston right now?", True, True, True),
    ("lottery numbers tonight", False, True, True),
    ("latest news about nvidia", True, True, True),
    ("write a story about the weather", False, False, True),
    ("search the web for python 3.14 release", False, False, False),
    ("stock price of AAPL", True, True, True),
    ("what is the score of the game", False, True, True),
    ("current events in France", True, True, False),
]


@pytest.mark.parametrize("message,engine,fallback,awareness", CASES)
def test_realtime_detectors(monkeypatch, message, engine, fallback, awareness):
    monkeypatch.setattr(ic, "_semantic_classifier", None)
    assert UnifiedChatEngine._is_realtime_query(message) is engine
    assert ic.intent_classifier.is_realtime_query(message)[0] is fallback
    assert requires_realtime_data(message)[0] is awareness
