"""The retries inside _call_llm_streaming stream their answer and keep the call.

When Ollama's stream fails with the thinking-model serialization crash
("invalid character") or drops with EOF, the retry's tokens must reach
chat:token as they arrive, and the retry must be the same call as the first
attempt (keep_alive, native tools). Only ollama.chat is replaced.
"""
from unittest.mock import MagicMock, patch

import pytest

import backend.services.unified_chat_engine as uce

TOOLS = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]


def _done():
    return {"message": {"content": ""}, "done": True, "done_reason": "stop",
            "prompt_eval_count": 3, "eval_count": 2}


@pytest.fixture
def engine():
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.llm = MagicMock(model="gemma4:12b", context_window=8192)
    e._native_toolcalls_active = True
    e._native_tools_schema = TOOLS
    e._native_pending_tool_calls = None
    e._think = False
    return e


def _run(engine, chat_impl):
    events = []
    with patch("ollama.chat", chat_impl), \
            patch("backend.services.unified_chat_engine.is_aborted", return_value=False), \
            patch("backend.services.unified_chat_engine._ollama_chat_model_loaded", return_value=False), \
            patch("backend.services.unified_chat_engine.classify_ollama_eof_kind", return_value="runner_eof"), \
            patch("requests.post"), patch("time.sleep"):
        result = engine._call_llm_streaming(
            [{"role": "user", "content": "hi"}],
            lambda name, payload: events.append((name, payload)),
            "sess-retry", emit_tokens=True, max_tokens=64,
        )
    return result, events


def _tokens(events):
    return [p["content"] for n, p in events if n == "chat:token"]


def test_invalid_character_retry_streams_and_keeps_the_call(engine):
    calls = []

    def chat(**kw):
        calls.append(kw)
        if len(calls) == 1:
            def broken():
                raise Exception("invalid character '<' looking for beginning of value")
                yield  # pragma: no cover
            return broken()

        def ok():
            yield {"message": {"content": "Hello "}}
            yield {"message": {"content": "there."}}
            yield _done()
        return ok()

    (content, _in, _out), events = _run(engine, chat)

    assert content == "Hello there."
    assert _tokens(events) == ["Hello ", "there."]
    retry = calls[1]
    assert retry.get("keep_alive") == calls[0].get("keep_alive")
    assert "keep_alive" in retry
    assert retry.get("tools") == TOOLS


def test_eof_runner_reload_retry_streams_its_tokens(engine):
    calls = []

    def chat(**kw):
        calls.append(kw)
        if not kw.get("stream", True):
            return {"message": {"content": ""}}  # non-stream retries give nothing
        if len([c for c in calls if c.get("stream", True)]) == 1:
            def dropped():
                raise Exception("EOF (status code: -1)")
                yield  # pragma: no cover
            return dropped()

        def ok():
            yield {"message": {"content": "Back "}}
            yield {"message": {"content": "again."}}
            yield _done()
        return ok()

    (content, _in, _out), events = _run(engine, chat)

    assert content == "Back again."
    assert _tokens(events) == ["Back ", "again."]
