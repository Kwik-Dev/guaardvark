"""Intent classification keywords match whole words, never substrings.

"now" is not in "know", "won" not in "won't", "rain" not in "train", "count"
not in "account" or "country", "list" not in "listen", "files" not in
"profiles". A keyword found inside another word sends an ordinary question
down the web-search or record-listing route.

The semantic classifier is pinned to its keyword fallback (no model on disk),
so the results do not depend on whether a trained model is installed.
"""
import pytest

from backend.services.intent_service import (
    SemanticIntentClassifier,
    find_keywords,
)
from backend.utils import intent_classifier as ic
from backend.utils.intent_classifier import IntentClassifier, IntentType


@pytest.fixture
def keyword_service(tmp_path):
    service = SemanticIntentClassifier(model_path=str(tmp_path / "no-model"))
    assert not service.is_model_loaded()
    return service


@pytest.fixture
def classifier(monkeypatch, keyword_service):
    monkeypatch.setattr(ic, "_semantic_classifier", keyword_service)
    return IntentClassifier()


class TestWholeWordKeywords:
    def test_know_is_not_now(self, classifier):
        intent, _confidence, _meta = classifier.classify_intent("do you know what a closure is?")
        assert intent == IntentType.GENERAL_CHAT

    def test_current_info_question_is_still_web_search(self, classifier):
        intent, _confidence, _meta = classifier.classify_intent("what's the latest news")
        assert intent == IntentType.WEB_SEARCH

    def test_contractions_and_longer_words_do_not_count_as_realtime(self, keyword_service):
        assert keyword_service._classify_keywords("I won't train my model") == ("general", 0.7)

    def test_only_the_whole_word_counts(self, keyword_service, classifier):
        # "won" (won't) and "rain" (train) no longer count; "today" alone is one
        # keyword, which stays at the bar classify_intent has to exceed.
        assert find_keywords(SemanticIntentClassifier._REALTIME_PATTERN, "i won't train today") == ["today"]
        _label, confidence = keyword_service._classify_keywords("I won't train today")
        assert confidence <= 0.6
        intent, _confidence, _meta = classifier.classify_intent(
            "I won't train my model today, explain overfitting")
        assert intent != IntentType.WEB_SEARCH

    @pytest.mark.parametrize("text, keyword", [
        ("i know", "now"),
        ("my account", "count"),
        ("which country", "count"),
        ("listen to this", "list"),
        ("my profiles", "files"),
        ("i won't", "won"),
        ("train it", "rain"),
    ])
    def test_keyword_inside_another_word_does_not_match(self, classifier, text, keyword):
        assert classifier._check_keywords(text, [keyword]) == (0.0, [])

    @pytest.mark.parametrize("text, keyword", [
        ("count my files", "count"),
        ("list my clients", "list"),
        ("what is today's weather", "today"),
        ("how many\nprojects", "how many"),
    ])
    def test_whole_word_still_matches(self, classifier, text, keyword):
        _confidence, found = classifier._check_keywords(text, [keyword])
        assert found == [keyword]

    def test_phrase_is_found_whole(self, classifier):
        _confidence, found = classifier._check_keywords(
            "what is the weather", ["what is the", "weather", "the"])
        assert found == ["what is the", "weather"]

    def test_country_question_is_not_a_record_count(self, classifier):
        intent, _confidence, _meta = classifier.classify_intent("how many countries are in the EU")
        assert intent != IntentType.DATABASE_QUERY
