"""Host hooks on ``UnifiedChatEngine.chat`` options.

A host (an embedded assistant with its own persona, tools and answer checks)
runs the engine's tool loop with: ``tool_names`` (allow-list),
``system_prompt`` (persona override), ``finalize_fn`` (audit before the
answer is emitted or saved), ``skip_direct_intercepts``, ``skip_nudges``,
``skip_memory_capture``, ``skip_escalation`` and ``persist``.
"""

import pytest

import backend.services.unified_chat_engine as uce


class _Tool:
    def __init__(self, name):
        self.name = name
        self.description = f"{name} tool"
        self.parameters = {}
        self.category = "test"


class _Registry:
    def __init__(self, names):
        self._tools = {n: _Tool(n) for n in names}

    def list_tools(self):
        return list(self._tools)

    def get_tool(self, name):
        return self._tools.get(name)

    def get_tool_names(self):
        return list(self._tools)

    def as_ollama_tools(self, tool_names=None):
        return []


class _LLM:
    model = "test-model"


def _engine(monkeypatch, reply="Final answer.", tools=("web_search", "generate_csv", "generate_image")):
    """An engine built without ``__init__``; every network/DB path is a stub."""
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.registry = _Registry(tools)
    e.llm = _LLM()
    e.max_iterations = 8
    e._image_data = None
    e._skip_tools = False
    e._brain_state = None
    e.calls = {"direct": 0, "saved": [], "summarized": 0, "llm_messages": []}

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
    e._maybe_summarize_session = lambda session_id: e.calls.__setitem__("summarized", e.calls["summarized"] + 1)

    def _save(session_id, role, content, extra_data=None):
        e.calls["saved"].append((role, content))

    e._save_message = _save

    def _direct(message, session_id, options, emit_fn, request_id):
        e.calls["direct"] += 1
        return None

    e._try_direct_tool = _direct
    for name in (
        "_try_image_generate_retry", "_try_image_edit_retry", "_try_media_direct",
        "_try_image_edit_direct", "_try_music_video_direct", "_try_film_crew_direct",
        "_try_video_generate_direct", "_try_image_generate_direct",
    ):
        setattr(e, name, lambda *a, **k: None)

    def _llm(messages, emit_fn, session_id, emit_tokens=True, max_tokens=768, iteration=1):
        e.calls["llm_messages"].append(list(messages))
        return reply, 1, 1

    e._call_llm_streaming = _llm
    e._last_llm_call_meta = {}

    monkeypatch.setattr(uce, "is_aborted", lambda session_id: False)
    monkeypatch.setattr(uce, "match_workstation_direct", lambda message: None)
    import backend.utils.settings_utils as su
    monkeypatch.setattr(su, "get_setting", lambda key, default=None: default)
    return e


def _run(e, message, options):
    events = []

    def emit(name, payload):
        events.append((name, payload))

    result = e._run_chat("sess-host", message, {"think": False, **options}, emit, "req-1", [])
    return result, events


def _complete(events):
    return [p for n, p in events if n == "chat:complete"][-1]


class TestToolAllowList:
    def test_only_named_known_tools_reach_the_prompt(self, monkeypatch):
        e = _engine(monkeypatch)
        _run(e, "make me a spreadsheet of the counties", {"tool_names": ["generate_csv", "no_such_tool"]})
        system = e.calls["llm_messages"][0][0]["content"]
        assert "generate_csv tool" in system
        assert "generate_image tool" not in system
        assert "no_such_tool" not in system

    def test_without_the_option_the_engine_selects_its_own(self, monkeypatch):
        e = _engine(monkeypatch)
        _run(e, "make me a spreadsheet of the counties", {})
        system = e.calls["llm_messages"][0][0]["content"]
        assert "generate_csv tool" in system and "generate_image tool" in system


class TestSystemPromptOverride:
    def test_override_replaces_the_persona_and_keeps_the_tool_tail(self):
        e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
        prompt = e._build_system_prompt(
            "ENGINE PERSONA", "- web_search(query:str) - search",
            options={"system_prompt": "HOST PERSONA"},
        )
        assert prompt.startswith("HOST PERSONA")
        assert "ENGINE PERSONA" not in prompt
        assert "web_search" in prompt

    def test_override_applies_to_the_lean_prompt_too(self):
        e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
        prompt = e._build_system_prompt("ENGINE PERSONA", "", options={"system_prompt": "HOST PERSONA"})
        assert prompt.startswith("HOST PERSONA")

    def test_brain_state_prompt_yields_to_the_override(self, monkeypatch):
        e = _engine(monkeypatch)

        class _Brain:
            _initialized = True

            def get_system_prompt(self, **kw):
                return "BRAIN STATE PROMPT"

        e._brain_state = _Brain()
        _run(e, "hello there, how are you today", {"system_prompt": "HOST PERSONA"})
        system = e.calls["llm_messages"][0][0]["content"]
        assert system.startswith("HOST PERSONA")
        assert "BRAIN STATE" not in system

    def test_brain_state_prompt_used_without_the_override(self, monkeypatch):
        e = _engine(monkeypatch)

        class _Brain:
            _initialized = True

            def get_system_prompt(self, **kw):
                return "BRAIN STATE PROMPT"

        e._brain_state = _Brain()
        _run(e, "hello there, how are you today", {})
        assert e.calls["llm_messages"][0][0]["content"] == "BRAIN STATE PROMPT"


class TestFinalizer:
    def test_revised_answer_is_emitted_and_saved(self, monkeypatch):
        e = _engine(monkeypatch, reply="Draft with a defect.")
        seen = {}

        def finalize(draft, steps):
            seen["draft"] = draft
            seen["steps"] = steps
            return "Corrected answer."

        result, events = _run(e, "what is the ridge cap count", {"finalize_fn": finalize})
        assert seen["draft"] == "Draft with a defect."
        assert isinstance(seen["steps"], list)
        assert _complete(events)["response"] == "Corrected answer."
        assert result["response"] == "Corrected answer."
        assert ("assistant", "Corrected answer.") in e.calls["saved"]

    def test_empty_or_failing_finalizer_keeps_the_draft(self, monkeypatch):
        e = _engine(monkeypatch, reply="Draft.")
        _, events = _run(e, "what is the ridge cap count", {"finalize_fn": lambda d, s: ""})
        assert _complete(events)["response"] == "Draft."

        def boom(d, s):
            raise RuntimeError("audit crashed")

        e = _engine(monkeypatch, reply="Draft.")
        _, events = _run(e, "what is the ridge cap count", {"finalize_fn": boom})
        assert _complete(events)["response"] == "Draft."


class TestIntercepts:
    def test_direct_intercepts_run_by_default(self, monkeypatch):
        e = _engine(monkeypatch)
        _run(e, "draw a cat", {})
        assert e.calls["direct"] == 1

    def test_direct_intercepts_skipped_on_request(self, monkeypatch):
        e = _engine(monkeypatch)
        _run(e, "draw a cat", {"skip_direct_intercepts": True})
        assert e.calls["direct"] == 0


class TestNudges:
    def test_realtime_disclaimer_is_prepended_by_default(self, monkeypatch):
        e = _engine(monkeypatch, reply="Sunny.")
        _, events = _run(e, "what is the weather today in Akron", {})
        assert _complete(events)["response"].startswith("Note: I was unable to verify")

    def test_realtime_disclaimer_skipped_when_the_host_checks_sources(self, monkeypatch):
        e = _engine(monkeypatch, reply="Sunny.")
        _, events = _run(e, "what is the weather today in Akron", {"skip_nudges": True})
        assert _complete(events)["response"] == "Sunny."


class TestPersistence:
    def test_persist_false_saves_nothing(self, monkeypatch):
        e = _engine(monkeypatch)
        _run(e, "what is the ridge cap count", {"persist": False})
        assert e.calls["saved"] == []
        assert e.calls["summarized"] == 0

    def test_default_saves_both_turns(self, monkeypatch):
        e = _engine(monkeypatch)
        _run(e, "what is the ridge cap count", {})
        assert [r for r, _ in e.calls["saved"]] == ["user", "assistant"]

    def test_memory_capture_can_be_skipped(self, monkeypatch):
        import backend.services.memory_capture as mc
        captured = []
        monkeypatch.setattr(mc, "capture_from_message", lambda m, **kw: captured.append(m))
        e = _engine(monkeypatch)
        _run(e, "remember that my name is Sam", {"skip_memory_capture": True})
        assert captured == []
        e = _engine(monkeypatch)
        _run(e, "remember that my name is Sam", {})
        assert captured == ["remember that my name is Sam"]


class TestEscalation:
    def test_always_mode_is_bypassed_on_request(self, monkeypatch):
        import backend.utils.settings_utils as su
        import backend.services.claude_advisor_service as cas
        asked = []

        def _always(key, default=None):
            return "always" if key == "claude_escalation_mode" else default

        class _Advisor:
            def is_available(self):
                return True

            def escalate(self, message, history):
                asked.append(message)
                return {"available": True, "response": "From the advisor."}

        monkeypatch.setattr(cas, "get_claude_advisor", lambda: _Advisor())
        e = _engine(monkeypatch, reply="Local.")
        monkeypatch.setattr(su, "get_setting", _always)
        _, events = _run(e, "what is the ridge cap count", {"skip_escalation": True})
        assert _complete(events)["response"] == "Local."
        assert asked == []
        e = _engine(monkeypatch, reply="Local.")
        monkeypatch.setattr(su, "get_setting", _always)
        _, events = _run(e, "what is the ridge cap count", {})
        assert _complete(events)["response"] == "From the advisor."
