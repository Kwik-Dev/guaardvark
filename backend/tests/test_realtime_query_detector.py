"""Real-time query detection must match whole words, never substrings.

A creative-writing prompt mentioning a "weathered" table is not a weather
question, and a long writing brief is never a real-time lookup even when a
keyword appears in it.
"""
from unittest.mock import patch

import pytest

from backend.services.unified_chat_engine import UnifiedChatEngine


class TestIsRealtimeQuery:
    def test_weathered_is_not_weather(self):
        assert UnifiedChatEngine._is_realtime_query(
            "The weathered surface of the table caught the light."
        ) is False

    def test_genuine_weather_question(self):
        assert UnifiedChatEngine._is_realtime_query(
            "what's the weather in Boston right now?"
        ) is True

    def test_phrase_keywords_still_match(self):
        assert UnifiedChatEngine._is_realtime_query("how hot is it today?") is True
        assert UnifiedChatEngine._is_realtime_query("any latest news on the merger") is True

    @pytest.mark.parametrize("blocker", [
        "write", "rewrite", "prompt", "story", "describe", "essay",
        "script", "poem", "scene", "dialogue", "lyrics",
    ])
    def test_writing_tasks_are_blocked(self, blocker):
        msg = f"Please {blocker} something about the weather forecast tonight"
        assert UnifiedChatEngine._is_realtime_query(msg) is False

    def test_generation_requests_are_blocked(self):
        assert UnifiedChatEngine._is_realtime_query(
            "generate an image of the weather over the sea"
        ) is False

    def test_long_creative_prompt_is_never_realtime(self):
        body = (
            "Write nothing yet, just consider: a lone traveller watches the temperature "
            "drop as the forecast worsens over the harbour. "
        )
        # Blockers are removed so only the length rule can reject this message.
        long_prompt = body.replace("Write nothing yet, just consider: ", "") * 8
        assert len(long_prompt) > UnifiedChatEngine._REALTIME_MAX_CHARS
        assert UnifiedChatEngine._is_realtime_query(long_prompt) is False

    def test_same_text_under_the_limit_is_realtime(self):
        short = "a lone traveller watches the temperature drop as the forecast worsens"
        assert len(short) <= UnifiedChatEngine._REALTIME_MAX_CHARS
        assert UnifiedChatEngine._is_realtime_query(short) is True

    def test_empty_message(self):
        assert UnifiedChatEngine._is_realtime_query("") is False


class TestShouldUseWebSearch:
    """enhanced_chat_api's detector shares the word-boundary rule, and searches
    only a short message with a link, a domain or a current-info word: the
    message is the query, so anything else would send the user's text out."""

    @pytest.fixture
    def manager(self):
        from backend.api.enhanced_chat_api import EnhancedChatManager
        return EnhancedChatManager.__new__(EnhancedChatManager)

    @patch("backend.utils.settings_utils.get_web_access", return_value=False)
    def test_substrings_do_not_trigger(self, _access, manager):
        # "know", "sometimes", "update" and "opposite" contain now/time/date/site.
        assert manager._should_use_web_search(
            "I know sometimes I update the opposite"
        ) is False

    @patch("backend.utils.settings_utils.get_web_access", return_value=False)
    def test_whole_word_indicator_triggers(self, _access, manager):
        assert manager._should_use_web_search("weather in Boston now") is True

    @patch("backend.utils.settings_utils.get_web_access", return_value=False)
    def test_bare_domain_triggers(self, _access, manager):
        assert manager._should_use_web_search("summarise example.com for me") is True

    # Web access on: the setting decides whether a search may run, not whether
    # a message looks like one.

    @patch("backend.utils.settings_utils.get_web_access", return_value=True)
    def test_word_count_does_not_trigger(self, _access, manager):
        assert manager._should_use_web_search(
            "please summarise this paragraph for me"
        ) is False

    @patch("backend.utils.settings_utils.get_web_access", return_value=True)
    def test_question_does_not_trigger(self, _access, manager):
        assert manager._should_use_web_search("why is my build slow?") is False

    @patch("backend.utils.settings_utils.get_web_access", return_value=True)
    def test_long_paste_does_not_trigger(self, _access, manager):
        paste = ("The contract below was signed today. " * 9)[:301]
        assert len(paste) == 301
        assert manager._should_use_web_search(paste) is False
        assert manager._should_use_web_search(paste[:300]) is True

    @patch("backend.utils.settings_utils.get_web_access", return_value=True)
    def test_url_triggers(self, _access, manager):
        assert manager._should_use_web_search("open https://example.com") is True

    @patch("backend.utils.settings_utils.get_web_access", return_value=True)
    def test_current_info_word_triggers(self, _access, manager):
        assert manager._should_use_web_search("weather in Boston now") is True

    @pytest.mark.parametrize("message", [
        "what is a closure?",
        "what are the SOLID principles",
        "find my notes on taxes",
        "check my code for bugs",
        "is my website config in settings.py right?",
        "what time is it",
    ])
    @patch("backend.utils.settings_utils.get_web_access", return_value=True)
    def test_generic_words_do_not_send_a_search(self, _access, manager, message):
        assert manager._should_use_web_search(message) is False

    @pytest.mark.parametrize("message", [
        "search the web for flights to Denver",
        "look up the opening hours of the library",
        "latest release of Ollama",
    ])
    @patch("backend.utils.settings_utils.get_web_access", return_value=True)
    def test_explicit_search_and_live_words_still_search(self, _access, manager, message):
        assert manager._should_use_web_search(message) is True

    def test_long_query_never_reaches_search(self, manager):
        query = ("latest news on " * 30)[:400]
        assert len(query) == 400
        with patch("backend.utils.settings_utils.get_web_access", return_value=True), \
                patch("backend.api.web_search_api.enhanced_web_search") as search:
            result = manager._perform_web_search_safe(query)
        search.assert_not_called()
        assert result["success"] is False
        assert result["strategy_used"] == "skipped_length"
        assert result["user_message"]


class _StopAfterSearchDecision(Exception):
    pass


class _StopOnLookup:
    """Stands in for session_messages, the first thing the turn reads after
    the web search decision, so the test ends before any model call."""

    def __contains__(self, _key):
        raise _StopAfterSearchDecision


class TestWebSearchClassificationDoesNotForceSearch:
    """The intent classifier's WEB_SEARCH label has fired on ordinary
    questions ("now" inside "know"), so it never sends a search on its own:
    enhanced chat searches only when _should_use_web_search accepts the
    message."""

    @pytest.fixture
    def run_turn(self, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from backend.api import enhanced_chat_api as eca

        monkeypatch.setattr(
            eca, "classify_user_intent",
            lambda message: (eca.IntentType.WEB_SEARCH, 0.9, {"keywords_found": ["now"]}),
        )

        def run(message):
            manager = eca.EnhancedChatManager.__new__(eca.EnhancedChatManager)
            manager._get_or_create_session = MagicMock(return_value=SimpleNamespace(id=1))
            manager._save_message = MagicMock()
            manager._get_active_model = MagicMock(return_value="test-model")
            manager._get_model_config = MagicMock(return_value={})
            manager._perform_web_search_safe = MagicMock(
                return_value={"success": False, "error": "stub", "user_message": "stub"})
            manager.session_messages = _StopOnLookup()
            with pytest.raises(_StopAfterSearchDecision):
                manager._process_regular_chat(
                    "s1", message, use_rag=False, debug_mode=False, simple_mode=False)
            return manager, manager._perform_web_search_safe

        return run

    def test_classified_question_the_rule_rejects_is_not_searched(self, run_turn):
        message = "do you know what a closure is?"
        manager, search = run_turn(message)
        assert manager._should_use_web_search(message) is False
        search.assert_not_called()

    def test_current_info_question_is_still_searched(self, run_turn):
        _manager, search = run_turn("what is the weather in Boston now")
        search.assert_called_once_with("what is the weather in Boston now")
