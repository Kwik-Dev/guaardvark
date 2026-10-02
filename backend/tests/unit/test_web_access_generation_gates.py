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
