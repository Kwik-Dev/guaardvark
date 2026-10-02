"""Research tasks and the outreach YouTube recon need web access on, as the
web_search tool does, and every page a research task reads, given or found by
a search, is fetched from a public address only.

Web access is the real Setting row in an in-memory SQLite database. The search
client, the weather lookup and requests' transport are replaced, and every
address is numeric, so nothing is sent and no name is looked up."""

import io
import types
from unittest.mock import patch

import pytest
import requests
from flask import Flask
from requests.adapters import HTTPAdapter

from backend.api import web_search_api
from backend.models import Setting, SocialOutreachLog, db
from backend.services.social_outreach.recon import RecondAgent
from backend.services.task_handlers.web_research_handler import WebResearchHandler
from backend.tools import web_tools
from backend.utils import web_scraper
from backend.utils.web_fetch import PUBLIC_ONLY_NOTE

PRIVATE = "http://127.0.0.1:5000/api/health"
PUBLIC = "http://93.184.216.34"
PAGE = b"<html><head><title>Public guide</title></head><body><main>How to run a local model.</main></body></html>"
OFF = "Web access is disabled. Enable it in Settings to run web research."


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def set_web_access(on: bool) -> None:
    row = db.session.get(Setting, "allow_web_search") or Setting(key="allow_web_search")
    row.value = "true" if on else "false"
    db.session.add(row)
    db.session.commit()


@pytest.fixture
def outbound(monkeypatch):
    """Everything that would leave the machine, recorded instead of sent."""
    calls = {"sent": [], "searched": [], "weather": []}

    def fake_send(self, request, **kwargs):
        calls["sent"].append(request.url)
        response = requests.Response()
        response.request = request
        response.url = request.url
        body = b"missing"
        response.status_code = 404
        if request.url == f"{PUBLIC}/redirect":
            response.status_code = 302
            response.headers["Location"] = PRIVATE
            body = b""
        elif request.url == f"{PUBLIC}/guide":
            response.status_code = 200
            response.headers["Content-Type"] = "text/html"
            body = PAGE
        response.raw = io.BytesIO(body)
        return response

    def search(query, max_results=5):
        calls["searched"].append(query)
        results = [{"title": "Guide", "url": f"{PUBLIC}/guide", "snippet": "s"},
                   {"title": "Router", "url": PRIVATE, "snippet": "s"},
                   {"title": "Bounce", "url": f"{PUBLIC}/redirect", "snippet": "s"}]
        return {"success": True, "results": results, "snippet": "s", "total_results": len(results)}

    def weather(location):
        calls["weather"].append(location)
        return {"success": True, "location": location}

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    monkeypatch.setattr(web_search_api, "perform_duckduckgo_search", search)
    monkeypatch.setattr(web_search_api, "get_weather_info", weather)
    # The scraper pauses between requests, to be polite to a real site.
    monkeypatch.setattr(web_scraper, "time", types.SimpleNamespace(sleep=lambda seconds: None))
    return calls


def run(config):
    return WebResearchHandler().execute(None, config, lambda *args, **kwargs: None)


CONFIGS = [
    {"research_type": "search", "queries": ["how to run a local model"]},
    {"research_type": "scrape", "urls": [f"{PUBLIC}/guide"]},
    {"research_type": "analyze_website", "urls": [f"{PUBLIC}/guide"]},
    {"research_type": "batch_scrape", "urls": [f"{PUBLIC}/guide"]},
    {"research_type": "weather", "location": "Paris"},
    {"research_type": "combined_research", "queries": ["how to run a local model"]},
]


@pytest.mark.parametrize("config", CONFIGS, ids=[c["research_type"] for c in CONFIGS])
def test_every_research_type_needs_web_access(app, outbound, config):
    set_web_access(False)
    result = run(config)
    assert result.status.value == "failed" and result.message == OFF
    assert outbound == {"sent": [], "searched": [], "weather": []}


def test_research_and_the_web_tools_read_the_same_setting(app):
    set_web_access(False)
    assert web_tools._web_access_block_reason("run web research") == OFF
    set_web_access(True)
    assert web_tools._web_access_block_reason("run web research") is None


def test_a_given_private_url_is_refused_without_a_request(app, outbound):
    set_web_access(True)
    result = run({"research_type": "scrape", "urls": [PRIVATE]})
    assert result.status.value == "failed"
    assert f"Refused to fetch {PRIVATE}" in result.message and PUBLIC_ONLY_NOTE in result.message
    assert outbound["sent"] == []


def test_batch_scrape_reads_public_pages_and_refuses_the_rest(app, outbound):
    set_web_access(True)
    result = run({"research_type": "batch_scrape", "urls": [f"{PUBLIC}/guide", PRIVATE, f"{PUBLIC}/redirect"]})
    by_url = {row["url"]: row for row in result.output_data["results"]}
    assert by_url[f"{PUBLIC}/guide"]["title"] == "Public guide"
    assert by_url[PRIVATE]["error"].startswith(f"Refused to fetch {PRIVATE}")
    assert by_url[f"{PUBLIC}/redirect"]["error"].startswith(f"Refused to fetch {PRIVATE}")
    assert not any(url.startswith("http://127.") for url in outbound["sent"])


def test_combined_research_reads_only_public_results_and_says_which_it_skipped(app, outbound):
    set_web_access(True)
    result = run({"research_type": "combined_research", "queries": ["how to run a local model"]})
    found = result.output_data["results"][0]
    assert [page["url"] for page in found["scraped_content"]] == [f"{PUBLIC}/guide"]
    assert [skipped["url"] for skipped in found["scrape_errors"]] == [PRIVATE, f"{PUBLIC}/redirect"]
    assert all(skipped["error"].startswith(f"Refused to fetch {PRIVATE}") for skipped in found["scrape_errors"])
    assert outbound["searched"] == ["how to run a local model"]
    assert not any(url.startswith("http://127.") for url in outbound["sent"])


def test_the_youtube_recon_searches_only_with_web_access_on(app, outbound):
    with patch("backend.services.social_outreach.recon.kill_switch.is_enabled", return_value=True), \
            patch("backend.services.social_outreach.recon.external_grader.score_thread_relevance",
                  return_value={"grade": 0.0, "skipped": True, "reason": "test"}):
        set_web_access(False)
        off = RecondAgent().scout_youtube("Ollama local LLM")
        assert off["reason"] == "web_access_off" and off["candidates"] == 0
        assert outbound["searched"] == [] and SocialOutreachLog.query.count() == 0

        set_web_access(True)
        on = RecondAgent().scout_youtube("Ollama local LLM")
        assert outbound["searched"] == ["site:youtube.com Ollama local LLM"]
        # The stub's results are not YouTube videos, so none becomes a candidate.
        assert on["reason"] is None and on["skipped_non_video"] == 3
