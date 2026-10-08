"""Chat website analysis, the bulk CSV generator's web research, the
enhanced-context generator's competitor page and a queued YouTube scout all
need web access on (Settings, off by default), the check the web tools make.
With it off nothing is fetched: website analysis and the YouTube scout say how
to turn it on; the two generators go on without the web and say they did.

The fetchers are replaced, so nothing is sent; the check runs in an app
context, as it does in the backend."""

import asyncio
import re
from urllib.parse import urlsplit

import pytest
from flask import Flask

from backend.api import web_search_api
from backend.utils import bulk_csv_generator, settings_utils


@pytest.fixture
def web_access(monkeypatch):
    state = {"on": False}
    monkeypatch.setattr(settings_utils, "get_web_access", lambda: state["on"])
    with Flask(__name__).app_context():
        yield state


def test_chat_website_analysis_reads_nothing_with_web_access_off(web_access, monkeypatch):
    from backend.api.enhanced_chat_api import EnhancedChatManager

    searched = []

    def search(query, max_results=5):
        searched.append(query)
        return {"success": False, "error": f"Refused to fetch {query}: a private address.", "data": {}}

    monkeypatch.setattr(web_search_api, "enhanced_web_search", search)
    manager = EnhancedChatManager.__new__(EnhancedChatManager)

    off = manager._handle_website_analysis_request("s1", "analyze https://example.com/pricing")
    assert off["success"] is False and searched == []
    assert off["response"] == ("I can't read https://example.com/pricing: Web access is disabled. "
                               "Enable it in Settings to analyze websites.")

    web_access["on"] = True
    on = manager._handle_website_analysis_request("s1", "analyze https://10.0.0.1/")
    assert searched == ["https://10.0.0.1/"]
    # The reason the fetch failed reaches the person.
    assert "Refused to fetch https://10.0.0.1/" in on["response"]


@pytest.fixture
def recorded_search(monkeypatch):
    searched = []

    def search(query, max_results=5):
        searched.append(query)
        return {"success": False, "error": "stub", "data": {}}

    monkeypatch.setattr(web_search_api, "enhanced_web_search", search)
    return searched


def test_chat_reads_only_a_link_the_person_typed(web_access, recorded_search):
    """settings.py is a file name, though .py is a real domain ending; the
    word "website" is not a link either. Neither is fetched, even with web
    access on."""
    from backend.api.enhanced_chat_api import EnhancedChatManager

    web_access["on"] = True
    manager = EnhancedChatManager.__new__(EnhancedChatManager)

    message = "is my website config in settings.py right?"
    assert manager._fallback_intent_detection(message) != "website_analysis"
    result = manager._handle_website_analysis_request("s1", message)
    assert result["success"] is False and recorded_search == []

    typed = "summarize https://example.com"
    assert manager._fallback_intent_detection(typed) == "website_analysis"
    manager._handle_website_analysis_request("s1", typed)
    assert recorded_search == ["https://example.com"]


def test_analyze_with_a_typed_link_reaches_website_analysis(web_access, recorded_search):
    """"analyze <link>" is the documented example; it used to fall out of the
    analyze branch with no intent at all. File words inside the link are part
    of the address, not a file to analyze."""
    from backend.api.enhanced_chat_api import EnhancedChatManager

    web_access["on"] = True
    manager = EnhancedChatManager.__new__(EnhancedChatManager)

    for message in ("analyze https://example.com", "review www.example.com/pricing",
                    "check https://example.com/code/data.json"):
        assert manager._fallback_intent_detection(message) == "website_analysis", message
    assert manager._fallback_intent_detection("analyze the code in settings.py") == "file_analysis"
    assert manager._fallback_intent_detection("analyze my week for me") == "general_chat"

    manager._handle_website_analysis_request("s1", "analyze https://example.com")
    assert recorded_search == ["https://example.com"]


def test_chat_website_analysis_skips_a_long_message(web_access, recorded_search):
    from backend.api.enhanced_chat_api import EnhancedChatManager

    web_access["on"] = True
    manager = EnhancedChatManager.__new__(EnhancedChatManager)
    paste = "Notes from the call, the deck is at https://example.com for review. " * 5
    assert len(paste) > EnhancedChatManager._WEB_SEARCH_MAX_CHARS
    assert manager._handle_website_analysis_request("s1", paste) is None
    assert recorded_search == []


def test_chat_hands_website_analysis_the_typed_message_and_falls_back_to_chat():
    """The intent check reads the message with the time context added; the
    handler gets the person's own text, and a skipped analysis goes on as
    ordinary chat."""
    from unittest.mock import MagicMock

    from backend.api.enhanced_chat_api import EnhancedChatManager

    manager = EnhancedChatManager.__new__(EnhancedChatManager)
    manager._update_session_activity = MagicMock()
    manager._cleanup_old_sessions = MagicMock()
    manager._try_media_command = MagicMock(return_value=None)
    manager._detect_intent_with_rules = MagicMock(return_value="website_analysis")
    manager._handle_website_analysis_request = MagicMock(return_value=None)
    manager._process_regular_chat = MagicMock(return_value={"response": "chat"})

    message = "what time is it on https://example.com"
    result = manager.process_chat_message("s1", message)

    assert manager._detect_intent_with_rules.call_args.args[0] != message
    manager._handle_website_analysis_request.assert_called_once_with("s1", message, project_id=None)
    assert result == {"response": "chat"}


@pytest.fixture
def generator(monkeypatch, tmp_path):
    monkeypatch.setattr(bulk_csv_generator, "get_default_llm", lambda: None)
    fetched = []

    def fetch(url, query=None, public_only=False):
        fetched.append(url)
        return {"success": True, "url": url, "title": "t", "content": "c"}

    monkeypatch.setattr(bulk_csv_generator, "extract_website_content", fetch)
    gen = bulk_csv_generator.BulkCSVGenerator(output_dir=str(tmp_path))
    gen.fetched = fetched
    return gen


def test_bulk_csv_research_is_skipped_and_reported_with_web_access_off(web_access, generator, monkeypatch):
    monkeypatch.setattr(generator, "generate_bulk_csv", lambda tasks, output_filename: ("out.csv", {"total_rows": 1}))
    assert generator._gather_web_research("cooling") == ""
    _path, stats = generator.generate_bulk_csv_with_web_research(tasks=[object()], output_filename="out.csv")
    assert generator.fetched == []
    assert stats["total_rows"] == 1 and stats["web_research_enabled"] is False
    assert stats["web_research_skipped"].startswith("Web research was skipped because web access is off")


def test_bulk_csv_research_reads_its_three_sites_with_web_access_on(web_access, generator):
    web_access["on"] = True
    context = generator._gather_web_research("cooling towers", max_sources=3)
    assert [urlsplit(url).hostname for url in generator.fetched] == [
        "datacenterknowledge.com", "www.datacenterjournal.com", "www.datacenterdynamics.com"]
    assert context.startswith("RESEARCH CONTEXT:") and generator.web_research_skipped is None


def test_the_competitor_page_is_skipped_with_a_note_with_web_access_off(web_access, monkeypatch):
    from backend.utils import enhanced_context_csv_generator as ecg

    fetched = []

    def fetch(url, query=None, public_only=False):
        fetched.append((url, public_only))
        return {"success": True, "url": url, "title": "Guards", "content": "Grille guards for tractors."}

    monkeypatch.setattr(ecg, "extract_website_content", fetch)
    generator = ecg.EnhancedContextCSVGenerator.__new__(ecg.EnhancedContextCSVGenerator)

    off = asyncio.run(generator._analyze_competitor_content("https://competitor.example/"))
    assert off["content"] == "" and fetched == []
    assert off["skipped"].startswith("Competitor analysis was skipped because web access is off")

    web_access["on"] = True
    on = asyncio.run(generator._analyze_competitor_content("https://competitor.example/"))
    assert fetched == [("https://competitor.example/", True)] and "skipped" not in on


def test_a_youtube_scout_is_not_queued_with_web_access_off(web_access, monkeypatch):
    from backend.services.social_outreach import job_service, kill_switch

    monkeypatch.setattr(kill_switch, "is_enabled", lambda: True)
    expected = "Web access is disabled. Enable it in Settings to scout YouTube for outreach."
    with pytest.raises(RuntimeError, match=f"^{re.escape(expected)}$"):
        job_service.queue_outreach_run("youtube", keyword_profiles=["ComfyUI"])
