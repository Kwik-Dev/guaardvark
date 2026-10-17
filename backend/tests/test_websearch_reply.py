"""A direct web_search (/websearch, or the offer chip, which sends one) answers in
sentences with source links; the tool card keeps the raw results. Stub registry
and stub model, nothing is searched."""

from types import SimpleNamespace

import pytest

import backend.services.unified_chat_engine as uce

RESULTS = {
    "query": "who won the world cup in 2022",
    "results": [
        {"title": "2022 FIFA World Cup final", "url": "https://example.org/final",
         "snippet": "Argentina beat France on penalties in the final."},
        {"title": "Messi lifts the trophy", "url": "https://example.net/messi", "snippet": ""},
    ],
    "summary": "",
    "source": "Bing",
}


class _Registry:
    def __init__(self, output):
        self.output = output

    def get_tool(self, name):
        return SimpleNamespace(name=name, requires_approval=False)

    def execute_tool(self, name, **params):
        return SimpleNamespace(success=True, output=self.output, error=None, metadata={})


def _engine(monkeypatch, output, reply):
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.registry = _Registry(output)
    e._image_data = None
    e.saved = []
    e.prompts = []
    e._save_message = lambda sid, role, content, extra_data=None: e.saved.append((role, content, extra_data))

    def _llm(messages, emit_fn, session_id, emit_tokens=True, max_tokens=768, iteration=1):
        e.prompts.append(messages)
        if isinstance(reply, Exception):
            raise reply
        return reply, 1, 1

    e._call_llm_streaming = _llm
    monkeypatch.setattr(uce, "is_aborted", lambda sid: False)
    monkeypatch.setattr(uce, "_preapproved_tool_names", lambda sid: set())
    return e


def _search(e):
    events = []
    result = e._run_direct_tool_execution(
        "web_search", {"query": RESULTS["query"]}, "s1", lambda n, p: events.append((n, p)),
        "r1", f"/websearch {RESULTS['query']}", {})
    return result, events


def test_the_reply_is_the_models_sentences(monkeypatch):
    answer = "Argentina won, beating France on penalties.\n\n- [2022 FIFA World Cup final](https://example.org/final)"
    e = _engine(monkeypatch, RESULTS, answer)

    result, events = _search(e)

    assert result["response"] == answer
    system, user = e.prompts[0]
    assert system["content"] == uce._WEB_SEARCH_PROSE_SYSTEM
    assert RESULTS["query"] in user["content"]
    assert "[2022 FIFA World Cup final](https://example.org/final)" in user["content"]
    assert "{'query'" not in user["content"]
    card = [p for n, p in events if n == "chat:tool_result"][0]
    assert "example.net/messi" in card["result"]["output"], "the card keeps the raw results"
    assert e.saved[-1][1] == answer


@pytest.mark.parametrize("reply", ["", RuntimeError("model unloaded")])
def test_without_the_model_the_reply_lists_the_results(monkeypatch, reply):
    e = _engine(monkeypatch, RESULTS, reply)

    result, _events = _search(e)

    assert result["response"] == (
        "- [2022 FIFA World Cup final](https://example.org/final): "
        "Argentina beat France on penalties in the final.\n"
        "- [Messi lifts the trophy](https://example.net/messi)"
    )


def test_no_results_answers_in_the_engines_words_without_the_model(monkeypatch):
    nothing = {"query": "zzqx", "results": [], "summary": "Bing found nothing for zzqx.",
               "source": "Bing", "no_results": True}
    e = _engine(monkeypatch, nothing, "unused")

    result, _events = _search(e)

    assert result["response"] == "Bing found nothing for zzqx."
    assert e.prompts == []
