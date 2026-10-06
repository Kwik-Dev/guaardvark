"""A question that looks like it needs current information gets an offer to
search the web, never a search.

offer_web_search puts IntentClassifier.should_use_web_search to work: with web
access on the reply carries {"action": "search", "query": <message>}, with it
off {"action": "enable_web_access", ...}. Unified chat (chat:complete, the
returned dict and the saved row) and enhanced chat (the reply and the saved
row) carry the same flag; nothing is offered once a search ran for the turn.
Stubs stand in for the model, the tools and the web-access setting; the
history test uses an in-memory SQLite app. No network, no live database.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import backend.utils.intent_classifier as ic
import backend.utils.settings_utils as su
from backend.utils.intent_classifier import IntentType, offer_web_search


@pytest.fixture
def web_access(monkeypatch):
    """Sets what get_web_access answers; on unless a test says otherwise."""
    state = {"on": True}

    def _get():
        if isinstance(state["on"], Exception):
            raise state["on"]
        return state["on"]

    monkeypatch.setattr(su, "get_web_access", _get)

    def set_to(value):
        state["on"] = value

    return set_to


@pytest.fixture
def keyword_classifier(monkeypatch):
    """Keyword classification only, whatever semantic model this box has."""
    monkeypatch.setattr(ic, "_semantic_classifier", None)


@pytest.fixture
def stock_classifier(monkeypatch):
    """The semantic classifier as a fresh install runs it: no model on disk,
    so its own keyword fallback answers."""
    from backend.services.intent_service import SemanticIntentClassifier

    fallback = SemanticIntentClassifier.__new__(SemanticIntentClassifier)
    fallback._model = None
    fallback._onnx_session = None
    fallback._use_onnx = False
    monkeypatch.setattr(ic, "_semantic_classifier", fallback)


CURRENT_INFO = "what is the latest news on the merger"
ORDINARY = "write a haiku about autumn"


class TestOfferWebSearch:
    def test_a_current_info_question_offers_a_search(self, web_access, keyword_classifier):
        assert offer_web_search(CURRENT_INFO) == {"action": "search", "query": CURRENT_INFO}

    def test_the_game_question_is_offered_on_a_fresh_install(self, web_access, stock_classifier):
        message = "who won the game last night?"
        assert offer_web_search(message) == {"action": "search", "query": message}

    def test_with_web_access_off_the_offer_is_to_turn_it_on(self, web_access, keyword_classifier):
        web_access(False)
        assert offer_web_search(CURRENT_INFO) == {"action": "enable_web_access", "query": CURRENT_INFO}

    def test_an_unreadable_setting_offers_to_turn_it_on(self, web_access, keyword_classifier):
        web_access(RuntimeError("no app context"))
        assert offer_web_search(CURRENT_INFO)["action"] == "enable_web_access"

    def test_ordinary_chat_offers_nothing(self, web_access, keyword_classifier):
        assert offer_web_search(ORDINARY) is None

    def test_a_slash_command_offers_nothing(self, web_access, keyword_classifier):
        assert offer_web_search("/websearch latest news") is None

    def test_a_long_message_offers_nothing(self, web_access, keyword_classifier):
        message = "latest news " + "x" * ic.WEB_SEARCH_OFFER_MAX_CHARS
        assert offer_web_search(message) is None

    def test_an_empty_message_offers_nothing(self, web_access):
        assert offer_web_search("") is None
        assert offer_web_search("   ") is None
        assert offer_web_search(None) is None

    def test_the_query_is_the_message_without_surrounding_space(self, web_access, keyword_classifier):
        assert offer_web_search(f"  {CURRENT_INFO}\n")["query"] == CURRENT_INFO

    def test_the_callers_classification_is_used(self, web_access, monkeypatch):
        def _no_second_classification(_message):
            raise AssertionError("classified again")

        monkeypatch.setattr(ic.intent_classifier, "classify_intent", _no_second_classification)
        assert offer_web_search("is it on?", intent_type=IntentType.WEB_SEARCH)["action"] == "search"
        assert offer_web_search("is it on?", intent_type=IntentType.DATABASE_QUERY) is None


# ── Unified chat ────────────────────────────────────────────────────────────


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
    def __init__(self, success, output=None, error=None):
        self.success = success
        self.output = output
        self.error = error
        self.metadata = {}


class _Registry:
    def __init__(self, names, result):
        self._tools = {n: _Tool(n) for n in names}
        self._result = result
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
        return self._result


def _search_call(query):
    return f"<tool_call>\n<tool>web_search</tool>\n<query>{query}</query>\n</tool_call>"


def _engine(monkeypatch, replies, tool_result=None):
    """An engine built without __init__ (as test_unified_chat_iteration_limit
    builds it), with a scripted model and a registry holding web_search."""
    import backend.services.unified_chat_engine as uce

    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.registry = _Registry(["web_search"], tool_result or _Result(True, "Results: none relevant."))
    e.llm = SimpleNamespace(model="test-model")
    e.max_iterations = 3
    e._image_data = None
    e._skip_tools = False
    e._brain_state = None
    e.saved = []

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
    e._save_message = lambda session_id, role, content, extra_data=None: e.saved.append((role, content, extra_data))
    e._try_direct_tool = lambda *a, **k: None
    for name in (
        "_try_image_generate_retry", "_try_image_edit_retry", "_try_media_direct",
        "_try_named_image_direct", "_try_image_edit_direct", "_try_music_video_direct",
        "_try_film_crew_direct", "_try_video_generate_direct", "_try_image_generate_direct",
    ):
        setattr(e, name, lambda *a, **k: None)

    scripted = list(replies)

    def _llm(messages, emit_fn, session_id, emit_tokens=True, max_tokens=768, iteration=1):
        return (scripted.pop(0) if scripted else "Done."), 1, 1

    e._call_llm_streaming = _llm
    e._last_llm_call_meta = {}

    monkeypatch.setattr(uce, "is_aborted", lambda session_id: False)
    monkeypatch.setattr(uce, "match_workstation_direct", lambda message: None)
    monkeypatch.setattr(su, "get_setting", lambda key, default=None: default)
    return e


def _run(e, message):
    events = []
    result = e._run_chat("sess-offer", message, {"think": False}, lambda n, p: events.append((n, p)), "req-1", [])
    complete = [p for n, p in events if n == "chat:complete"][-1]
    saved = [extra for role, _content, extra in e.saved if role == "assistant"][-1]
    return result, complete, saved


class TestUnifiedChatOffer:
    def test_an_unsearched_current_info_reply_carries_the_offer(self, monkeypatch, web_access, keyword_classifier):
        e = _engine(monkeypatch, ["I can't see today's news from here."])

        result, complete, saved = _run(e, CURRENT_INFO)

        offer = {"action": "search", "query": CURRENT_INFO}
        assert complete["web_search_offer"] == offer
        assert result["web_search_offer"] == offer
        assert saved["web_search_offer"] == offer
        assert e.registry.executions == [], "the offer runs nothing"

    def test_with_web_access_off_the_offer_is_to_turn_it_on(self, monkeypatch, web_access, keyword_classifier):
        web_access(False)
        e = _engine(monkeypatch, ["I can't see today's news from here."])

        _result, complete, _saved = _run(e, CURRENT_INFO)

        assert complete["web_search_offer"] == {"action": "enable_web_access", "query": CURRENT_INFO}

    def test_no_offer_once_the_model_has_searched(self, monkeypatch, web_access, keyword_classifier):
        e = _engine(monkeypatch, [_search_call("merger news"), "Here is what the search found."])

        result, complete, saved = _run(e, CURRENT_INFO)

        assert [name for name, _ in e.registry.executions] == ["web_search"]
        assert complete["web_search_offer"] is None
        assert result["web_search_offer"] is None
        assert "web_search_offer" not in saved

    def test_a_search_refused_for_web_access_still_offers_to_turn_it_on(
            self, monkeypatch, web_access, keyword_classifier):
        web_access(False)
        refused = _Result(False, error="Web access is disabled. Enable it in Settings to use web search.")
        e = _engine(monkeypatch, [_search_call("merger news"), "I could not search the web."], tool_result=refused)

        _result, complete, _saved = _run(e, CURRENT_INFO)

        assert complete["web_search_offer"] == {"action": "enable_web_access", "query": CURRENT_INFO}

    def test_ordinary_chat_carries_no_offer(self, monkeypatch, web_access, keyword_classifier):
        e = _engine(monkeypatch, ["Leaves drift and settle."])

        result, complete, saved = _run(e, ORDINARY)

        assert complete["web_search_offer"] is None
        assert result["web_search_offer"] is None
        assert not saved or "web_search_offer" not in saved

    def test_no_offer_for_host_supplied_facts_or_a_stopped_turn(self, monkeypatch, web_access, keyword_classifier):
        import backend.services.unified_chat_engine as uce

        e = _engine(monkeypatch, [])
        e._local_facts_this_turn = True
        assert e._web_search_offer(CURRENT_INFO, [], "sess-offer") is None

        e._local_facts_this_turn = False
        monkeypatch.setattr(uce, "is_aborted", lambda session_id: True)
        assert e._web_search_offer(CURRENT_INFO, [], "sess-offer") is None

    def test_a_page_read_counts_as_having_searched(self, monkeypatch, web_access, keyword_classifier):
        e = _engine(monkeypatch, [])
        steps = [{"iteration": 1, "tool_calls": [{"tool_name": "fetch_url", "success": True}]}]
        assert e._web_search_offer(CURRENT_INFO, steps, "sess-offer") is None
        failed = [{"iteration": 1, "tool_calls": [{"tool_name": "fetch_url", "success": False}]}]
        assert e._web_search_offer(CURRENT_INFO, failed, "sess-offer")["action"] == "search"


# ── Enhanced chat ───────────────────────────────────────────────────────────


@pytest.fixture
def eca():
    from backend.api import enhanced_chat_api
    return enhanced_chat_api


def _manager(eca):
    return eca.EnhancedChatManager.__new__(eca.EnhancedChatManager)


class TestEnhancedChatOffer:
    @pytest.fixture
    def offered(self, eca, monkeypatch):
        calls = []

        def _offer(message, intent_type=None):
            calls.append((message, intent_type))
            return {"action": "search", "query": message}

        monkeypatch.setattr(eca, "offer_web_search", _offer)
        return calls

    def test_a_turn_without_a_search_is_offered_one(self, eca, offered):
        offer = _manager(eca)._web_search_offer(CURRENT_INFO, eca.IntentType.WEB_SEARCH, None)
        assert offer == {"action": "search", "query": CURRENT_INFO}
        assert offered == [(CURRENT_INFO, eca.IntentType.WEB_SEARCH)]

    @pytest.mark.parametrize("strategy", ["disabled", "skipped_length"])
    def test_a_search_that_sent_nothing_still_offers(self, eca, offered, strategy):
        result = {"success": False, "strategy_used": strategy}
        assert _manager(eca)._web_search_offer(CURRENT_INFO, None, result) is not None

    @pytest.mark.parametrize("result", [
        {"success": True, "strategy_used": "duckduckgo"},
        {"success": False, "strategy_used": "failed"},
    ])
    def test_a_turn_that_sent_a_search_offers_nothing(self, eca, offered, result):
        assert _manager(eca)._web_search_offer(CURRENT_INFO, None, result) is None
        assert offered == []

    def test_a_failing_offer_leaves_the_reply_alone(self, eca, monkeypatch):
        def _broken(message, intent_type=None):
            raise RuntimeError("classifier unavailable")

        monkeypatch.setattr(eca, "offer_web_search", _broken)
        assert _manager(eca)._web_search_offer(CURRENT_INFO, None, None) is None
        monkeypatch.setattr(eca, "offer_web_search", None)
        assert _manager(eca)._web_search_offer(CURRENT_INFO, None, None) is None


class _StopAfterOffer(Exception):
    pass


class _StopOnLookup:
    """Stands in for session_messages, the first thing the turn reads after
    the web search offer, so the test ends before any model call."""

    def __contains__(self, _key):
        raise _StopAfterOffer


class TestEnhancedChatTurnAsksForTheOffer:
    @pytest.fixture
    def run_turn(self, eca, monkeypatch):
        monkeypatch.setattr(
            eca, "classify_user_intent",
            lambda message: (eca.IntentType.WEB_SEARCH, 0.9, {"keywords_found": ["game"]}),
        )

        def run(message, search_result):
            manager = _manager(eca)
            manager._get_or_create_session = MagicMock(return_value=SimpleNamespace(id=1))
            manager._save_message = MagicMock()
            manager._get_active_model = MagicMock(return_value="test-model")
            manager._get_model_config = MagicMock(return_value={})
            manager._perform_web_search_safe = MagicMock(return_value=search_result)
            manager._web_search_offer = MagicMock(return_value=None)
            manager.session_messages = _StopOnLookup()
            with pytest.raises(_StopAfterOffer):
                manager._process_regular_chat(
                    "s1", message, use_rag=False, debug_mode=False, simple_mode=False)
            return manager

        return run

    def test_an_unsearched_question_is_offered_with_its_classification(self, eca, run_turn):
        message = "who won the game last night?"
        manager = run_turn(message, None)
        manager._perform_web_search_safe.assert_not_called()
        manager._web_search_offer.assert_called_once_with(message, eca.IntentType.WEB_SEARCH, None)

    def test_the_offer_sees_the_search_that_ran(self, eca, run_turn):
        message = "what is the weather in Boston now"
        searched = {"success": True, "strategy_used": "duckduckgo", "formatted_context": "ctx"}
        manager = run_turn(message, searched)
        manager._perform_web_search_safe.assert_called_once_with(message)
        manager._web_search_offer.assert_called_once_with(message, eca.IntentType.WEB_SEARCH, searched)


# ── History ─────────────────────────────────────────────────────────────────


@pytest.fixture
def history_client(eca):
    from flask import Flask

    from backend.models import LLMMessage, LLMSession, db

    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    app.register_blueprint(eca.enhanced_chat_bp)
    with app.app_context():
        db.create_all()
        db.session.add(LLMSession(id="s1", user="default"))
        db.session.flush()
        db.session.add(LLMMessage(
            session_id="s1", role="assistant", content="I can't see last night's results.",
            extra_data={"web_search_offer": {"action": "search", "query": "who won the game last night?"}},
        ))
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_history_keeps_the_offer_under_its_reply(history_client):
    message = history_client.get("/api/enhanced-chat/s1/history").get_json()["messages"][0]
    assert message["web_search_offer"] == {"action": "search", "query": "who won the game last night?"}
