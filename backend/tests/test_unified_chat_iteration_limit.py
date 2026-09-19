"""Running out of iterations must not end the turn with an empty answer.

The tool loop only sets a response when the model stops calling tools. A model
that keeps calling them until the budget is gone left accumulated_response at
"", and chat:complete emitted nothing — observed on a 5-iteration run. The
engine now makes one more call with tools off and answers from what the tools
returned.
"""

import pytest

import backend.services.unified_chat_engine as uce


class _Tool:
    requires_approval = False
    read_only = True
    observation_chars = 500

    def __init__(self, name):
        self.name = name
        self.description = f"{name} tool"
        self.parameters = {}
        self.category = "test"


class _Result:
    def __init__(self, output):
        self.success = True
        self.output = output
        self.error = None
        self.metadata = {}


class _Registry:
    def __init__(self, names):
        self._tools = {n: _Tool(n) for n in names}
        self.executions = []

    def list_tools(self):
        return list(self._tools)

    def get_tool(self, name):
        return self._tools.get(name)

    def get_tool_names(self):
        return list(self._tools)

    def as_ollama_tools(self, tool_names=None):
        return []

    def execute_tool(self, name, on_output=None, agent_context=None, **params):
        self.executions.append((name, params))
        return _Result("Guaardvark runs entirely on the user's own GPU.")


class _LLM:
    model = "test-model"


def tool_call(query):
    """A reply that calls web_search. Distinct queries: the guard blocks repeats."""
    return f"<tool_call>\n<tool>web_search</tool>\n<query>{query}</query>\n</tool_call>"


TOOL_CALL = tool_call("what is it")


def _engine(monkeypatch, replies, max_iterations=3):
    """The harness from test_unified_chat_host_hooks, with a scripted LLM."""
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.registry = _Registry(["web_search"])
    e.llm = _LLM()
    e.max_iterations = max_iterations
    e._image_data = None
    e._skip_tools = False
    e._brain_state = None
    e.calls = {"llm_messages": [], "saved": []}

    class _Selector:
        def select(self, message, registry):
            return registry.list_tools()

    e._semantic_selector = _Selector()
    e._load_history = lambda session_id, limit=None: []
    e._load_rules = lambda model_name: "ENGINE PERSONA"
    e._get_routed_tools = lambda message: []
    e._retrieve_rag_context = lambda message: ""
    e._should_skip_rag = lambda message: True
    e._format_interface_context = lambda options: ""
    e._compact_history = lambda history, *a, **k: history
    e._analyze_pasted_image = lambda *a, **k: None
    e._warmup_chat_llm_async = lambda *a, **k: None
    e._maybe_summarize_session = lambda session_id: None
    e._save_message = lambda session_id, role, content, extra_data=None: e.calls["saved"].append((role, content))
    e._try_direct_tool = lambda *a, **k: None
    for name in (
        "_try_image_generate_retry", "_try_image_edit_retry", "_try_media_direct",
        "_try_image_edit_direct", "_try_music_video_direct", "_try_film_crew_direct",
        "_try_video_generate_direct", "_try_image_generate_direct",
    ):
        setattr(e, name, lambda *a, **k: None)

    scripted = list(replies)

    def _llm(messages, emit_fn, session_id, emit_tokens=True, max_tokens=768, iteration=1):
        e.calls["llm_messages"].append(list(messages))
        reply = scripted.pop(0) if scripted else TOOL_CALL
        return reply, 1, 1

    e._call_llm_streaming = _llm
    e._last_llm_call_meta = {}

    monkeypatch.setattr(uce, "is_aborted", lambda session_id: False)
    monkeypatch.setattr(uce, "match_workstation_direct", lambda message: None)
    import backend.utils.settings_utils as su
    monkeypatch.setattr(su, "get_setting", lambda key, default=None: default)
    return e


def _run(e, message="what does this thing do", options=None):
    events = []

    def emit(name, payload):
        events.append((name, payload))

    result = e._run_chat("sess-iter", message, {"think": False, **(options or {})}, emit, "req-1", [])
    return result, events


def _complete(events):
    return [p for n, p in events if n == "chat:complete"][-1]


def test_exhausted_loop_answers_from_the_tool_results(monkeypatch):
    """Every iteration calls a tool; the synthesis call is the one after."""
    e = _engine(monkeypatch, replies=[tool_call("one"), tool_call("two"), tool_call("three"), "It runs on your own GPU."])

    result, events = _run(e)

    assert result["response"] == "It runs on your own GPU."
    assert result["synthesized"] is True
    assert _complete(events)["response"] == "It runs on your own GPU."
    assert _complete(events)["synthesized"] is True
    assert len(e.registry.executions) == 3, "the loop still stopped at max_iterations"


def test_the_synthesis_call_sees_the_observations_and_is_told_not_to_call_tools(monkeypatch):
    e = _engine(monkeypatch, replies=[tool_call("one"), tool_call("two"), tool_call("three"), "Answered."])

    _run(e)

    final_messages = e.calls["llm_messages"][-1]
    assert "used every tool call available" in final_messages[-1]["content"]
    assert "Do not call another tool" in final_messages[-1]["content"]
    assert any("own GPU" in m["content"] for m in final_messages), "tool results are in the prompt"


def test_the_synthesis_step_is_marked_for_the_ui(monkeypatch):
    e = _engine(monkeypatch, replies=[tool_call("one"), tool_call("two"), tool_call("three"), "Answered."])

    result, _ = _run(e)

    assert result["steps"][-1]["synthesized"] is True
    assert result["steps"][-1]["tool_calls"] == []


def test_an_empty_synthesis_leaves_the_response_empty(monkeypatch):
    """No invention: if the extra call says nothing, nothing is claimed."""
    e = _engine(monkeypatch, replies=[tool_call("one"), tool_call("two"), tool_call("three"), "   "])

    result, events = _run(e)

    assert result["response"] == ""
    assert result["synthesized"] is False
    assert _complete(events)["synthesized"] is False


def test_a_normal_final_answer_is_not_synthesised(monkeypatch):
    e = _engine(monkeypatch, replies=[tool_call("one"), "Here is the answer."])

    result, events = _run(e)

    assert result["response"] == "Here is the answer."
    assert result["synthesized"] is False
    assert len(e.calls["llm_messages"]) == 2, "no extra call when the model answered"


def test_an_aborted_turn_is_left_alone(monkeypatch):
    e = _engine(monkeypatch, replies=[tool_call("one"), tool_call("two"), tool_call("three"), "Should never be asked for."])
    monkeypatch.setattr(uce, "is_aborted", lambda session_id: True)

    result, events = _run(e)

    assert result["synthesized"] is False
    assert any(p.get("aborted") for n, p in events if n == "chat:complete")
    assert e.calls["llm_messages"] == [], "the abort fires before the first call"
