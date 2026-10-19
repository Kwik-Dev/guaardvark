"""History compaction and prompt pruning: which window, which token estimate.

Default (setting off): compaction keeps its historical 8192-token window and
chars/4 estimate, so when history gets compacted does not change. With
GUAARDVARK_COMPACT_TO_MODEL_WINDOW on, compaction uses the window the chat
call itself uses (ollama_resource_manager.refresh_context_window) and the
same chars/3 + 4 estimate as _prune_messages_to_fit. The summarising model
call (ollama.chat) is the only stand-in.
"""
from unittest.mock import MagicMock, patch

import pytest

import backend.services.unified_chat_engine as uce

FLAG = "GUAARDVARK_COMPACT_TO_MODEL_WINDOW"


@pytest.fixture
def engine():
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.llm = MagicMock(model="some-model:latest", context_window=32768)
    return e


def _history(n_messages, chars_each):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * chars_each}
            for i in range(n_messages)]


def _summarised(engine, history, window):
    calls = []

    def chat(**kw):
        calls.append(kw)
        return {"message": {"content": "summary"}}

    with patch("ollama.chat", chat):
        out = engine._compact_history(history, context_window=window)
    return out, calls


def test_default_window_is_the_fixed_8192(engine, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    with patch("backend.utils.ollama_resource_manager.refresh_context_window", return_value=32768):
        assert engine._compaction_window() == 8192


def test_flag_uses_the_chat_calls_window(engine, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    with patch("backend.utils.ollama_resource_manager.refresh_context_window", return_value=32768):
        assert engine._compaction_window() == 32768
        assert engine._chat_context_window() == 32768


def test_default_estimate_is_chars_over_4(engine, monkeypatch):
    """Threshold is 0.7 * 8192 = 5,734 tokens, counted as chars / 4."""
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr("backend.config.COMPACTION_THRESHOLD", 0.7, raising=False)
    # 10 messages * 2,200 chars = 22,000 chars -> 5,500 tokens at /4: below 5,734, kept.
    kept, calls = _summarised(engine, _history(10, 2200), 8192)
    assert calls == [] and len(kept) == 10
    # 10 * 2,400 = 24,000 chars -> 6,000 tokens at /4: compacted.
    out, calls = _summarised(engine, _history(10, 2400), 8192)
    assert len(calls) == 1 and out[0]["content"] == "Conversation summary: summary"
    assert len(out) == 9  # summary + last 8


def test_flag_estimate_matches_pruning(engine, monkeypatch):
    """With the flag, 22,000 chars is 7,373 tokens at /3 + 4 per message: compacted."""
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setattr("backend.config.COMPACTION_THRESHOLD", 0.7, raising=False)
    history = _history(10, 2200)
    assert engine._history_tokens(history) == engine._estimate_tokens(history)
    out, calls = _summarised(engine, history, 8192)
    assert len(calls) == 1 and len(out) == 9


def test_window_resolution_falls_back_like_the_chat_call(engine):
    with patch("backend.utils.ollama_resource_manager.refresh_context_window", side_effect=RuntimeError), \
            patch("backend.utils.ollama_resource_manager.compute_optimal_num_ctx", return_value=4096):
        engine.llm.context_window = 0
        assert engine._chat_context_window() == 4096
    with patch("backend.utils.ollama_resource_manager.refresh_context_window", side_effect=RuntimeError), \
            patch("backend.utils.ollama_resource_manager.compute_optimal_num_ctx", side_effect=RuntimeError):
        assert engine._chat_context_window() == 8192
