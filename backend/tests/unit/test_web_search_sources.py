"""web_search names the services a query really goes to, sends a query to the
search engine and nowhere else, says so when the engine finds nothing or cannot
be asked, asks the engine for the number of results the caller wanted, and
sends no ~/.netrc login to the fixed hosts it calls. The search client's HTTP
call and requests' transport are both replaced, and where a fetch is
public-only its name lookup is answered too; nothing is sent."""

import json
import socket
import sys
from urllib.parse import urlsplit

import pytest
import requests
from requests.adapters import HTTPAdapter

from backend.api import web_search_api
from backend.tools import web_tools
from backend.utils.web_search_sources import (
    DEFAULT_SEARCH_RESULTS, MAX_SEARCH_RESULTS, SEARCH_ENGINE, WEATHER_SOURCE,
)

duckduckgo_search = pytest.importorskip("duckduckgo_search")
DDGS = duckduckgo_search.DDGS
from duckduckgo_search.exceptions import (  # noqa: E402
    DuckDuckGoSearchException, RatelimitException, TimeoutException,
)

WEATHER_JSON = json.dumps({"current_condition": [{
    "weatherDesc": [{"value": "Clear"}], "temp_C": "18", "temp_F": "64", "humidity": "50"}]}).encode()


@pytest.fixture
def engine(monkeypatch):
    """Replace the client's search with canned rows; record what was asked."""
    asked = []
    state = {"rows": 30}

    def fake_text(self, keywords, backend="auto", max_results=None, **kwargs):
        asked.append(max_results)
        return [{"title": f"Result {i}", "href": f"https://example.org/{i}", "body": f"snippet {i}"}
                for i in range(min(max_results or 10, state["rows"]))]

    monkeypatch.setattr(DDGS, "text", fake_text)
    return asked, state


@pytest.fixture
def transport(monkeypatch, tmp_path):
    """Replace requests' transport; a .netrc with a 'default' login is in force."""
    netrc = tmp_path / "netrc"
    netrc.write_text("machine wttr.in login weatheruser password weather-token\n"
                     "default login defaultuser password default-secret\n")
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))
    sent = []

    def fake_send(self, request, **kwargs):
        sent.append((request.url, request.headers.get("Authorization")))
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        response._content = WEATHER_JSON
        response._content_consumed = True
        return response

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    return sent


def test_the_netrc_fixture_would_leak_through_a_plain_request(transport):
    """Guards the fixture: this is the login a plain requests call attaches."""
    prepared = requests.Session().prepare_request(requests.Request("GET", "https://wttr.in/Paris?format=j1"))
    assert prepared.headers.get("Authorization", "").startswith("Basic ")


def test_the_installed_client_searches_the_engine_the_label_names(monkeypatch, transport):
    """The client decides where a text search goes. This runs the search the
    way web_search does, with only the client's HTTP call replaced. If it fails
    after a version change, SEARCH_ENGINE and the web_search description must
    be brought in line with where queries now go."""
    called = []

    class _NoResults:
        text = "There are no results for this"
        content = b""

    def fake_get_url(self, method, url, **kwargs):
        called.append(urlsplit(url).hostname)
        return _NoResults()

    monkeypatch.setattr(DDGS, "_get_url", fake_get_url)
    result = web_search_api.perform_duckduckgo_search("probe query")
    assert called and set(called) == {"www.bing.com"}
    assert SEARCH_ENGINE == "Bing"
    assert result["source"] == SEARCH_ENGINE
    assert transport == []                      # nothing went anywhere but the engine


def test_search_results_are_labelled_with_the_engine(engine, transport):
    result = web_search_api.enhanced_web_search("rust vs go")
    assert result["strategy_used"] == "duckduckgo_search"
    assert result["data"]["source"] == SEARCH_ENGINE
    assert transport == []


def test_no_results_is_said_and_the_query_goes_nowhere_else(engine, transport):
    asked, state = engine
    state["rows"] = 0
    result = web_search_api.perform_duckduckgo_search("rust vs go")
    assert not result["success"] and result["no_results"] is True
    assert result["results"] == [] and result["source"] == SEARCH_ENGINE
    assert result["error"] == f"{SEARCH_ENGINE} returned no results for this query."
    assert len(asked) == web_search_api._SEARCH_ATTEMPTS
    assert transport == []


def test_the_search_api_reports_no_results_with_the_engine_named(engine, transport):
    _, state = engine
    state["rows"] = 0
    result = web_search_api.enhanced_web_search("rust vs go")
    assert not result["success"] and result["strategy_used"] == "failed"
    data = result["data"]
    assert data["type"] == "no_results" and data["source"] == SEARCH_ENGINE
    assert data["message"] == result["error"] == f"{SEARCH_ENGINE} returned no results for this query."
    assert data["errors"]["search_error"] == data["message"]


def test_the_tool_returns_no_results_as_an_empty_answer(engine, transport, monkeypatch):
    """Nothing found is an answer, not a fault, so it does not count towards the
    failure breaker in the chat loop or the MCP server."""
    _, state = engine
    state["rows"] = 0
    monkeypatch.setattr(web_tools, "_web_access_block_reason", lambda action: None)
    result = web_tools.WebSearchTool().execute(query="rust vs go")
    assert result.success
    assert result.output["results"] == [] and result.output["no_results"] is True
    assert result.output["source"] == SEARCH_ENGINE
    assert "no results" in result.output["summary"]


@pytest.mark.parametrize("raised,expected,attempts", [
    (RatelimitException("https://www.bing.com/search?q=secret 429 Ratelimit"),
     f"{SEARCH_ENGINE} refused the search (HTTP 429)", 1),
    (RatelimitException("https://www.bing.com/search?q=secret 403 Ratelimit"),
     f"{SEARCH_ENGINE} refused the search (HTTP 403)", 1),
    (TimeoutException("https://www.bing.com/search RequestTimeout: timed out"),
     f"{SEARCH_ENGINE} did not answer in time.", web_search_api._SEARCH_ATTEMPTS),
    (DuckDuckGoSearchException("https://www.bing.com/search?q=secret return None. params={'q': 'secret'}"),
     f"{SEARCH_ENGINE} answered the search with an error.", web_search_api._SEARCH_ATTEMPTS),
])
def test_a_failed_search_says_why(monkeypatch, transport, raised, expected, attempts):
    """The error is raised by the client's own HTTP call and passes through its
    text(), which wraps it; the reason is still told apart. A refused search is
    not asked again at once."""
    calls = []

    def fake_get_url(self, method, url, **kwargs):
        calls.append(url)
        raise raised

    monkeypatch.setattr(DDGS, "_get_url", fake_get_url)
    monkeypatch.setattr(web_tools, "_web_access_block_reason", lambda action: None)
    result = web_search_api.perform_duckduckgo_search("secret")
    assert not result["success"] and "no_results" not in result
    assert result["error"].startswith(expected)
    assert "secret" not in result["error"]
    assert len(calls) == attempts
    tool = web_tools.WebSearchTool().execute(query="secret")
    assert not tool.success and tool.error.startswith(expected)
    assert transport == []


def test_the_command_route_searches_only_with_web_access_on(engine, transport, monkeypatch):
    from flask import Flask
    from backend.routes import command_api

    asked, state = engine
    app = Flask(__name__)
    app.register_blueprint(command_api.command_bp)
    client = app.test_client()

    monkeypatch.setattr(command_api, "get_web_access", lambda: False)
    refused = client.post("/api/command/websearch", json={"query": "rust vs go"})
    assert refused.status_code == 403 and asked == []

    monkeypatch.setattr(command_api, "get_web_access", lambda: True)
    found = client.post("/api/command/websearch", json={"query": "rust vs go"}).get_json()
    assert found["data"]["response"].startswith(f"Search results from {SEARCH_ENGINE}:")
    state["rows"] = 0
    empty = client.post("/api/command/websearch", json={"query": "rust vs go"})
    assert empty.status_code == 200
    assert empty.get_json()["data"]["response"] == f"{SEARCH_ENGINE} returned no results for this query."
    assert transport == []


def test_a_missing_client_is_reported_not_hidden(monkeypatch, transport):
    monkeypatch.setitem(sys.modules, "duckduckgo_search", None)
    result = web_search_api.perform_duckduckgo_search("rust vs go")
    assert not result["success"]
    assert result["error"].startswith("Web search is not available")
    assert transport == []


@pytest.fixture
def lookups(monkeypatch):
    """Answer every name lookup with a public address and record the names.

    A URL in a web search is fetched only from a public address, and the guard
    looks the host up before the first request, so a test that fakes the
    transport must answer the lookup too, or it is sent to the real resolver.
    """
    asked = []

    def fake(host, port, *args, **kwargs):
        asked.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return asked


def test_a_url_that_serves_a_file_ends_the_call_without_a_search(engine, lookups, monkeypatch):
    """The query is not sent on to the search engine when its URL is a PDF."""
    asked, _ = engine
    sent = []

    def fake_send(self, request, **kwargs):
        sent.append(request.url)
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        response.headers["Content-Type"] = "application/pdf"
        response._content = b"%PDF-1.4"
        response._content_consumed = True
        return response

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    result = web_search_api.enhanced_web_search("summarize https://site.example/report.pdf")
    assert not result["success"] and result["error"].startswith("Not a web page")
    assert sent == ["https://site.example/report.pdf"]
    assert asked == []
    assert lookups == ["site.example"]


def test_the_weather_lookup_carries_no_login_and_names_its_service(engine, transport):
    result = web_search_api.enhanced_web_search("what's the weather like in Paris today?")
    assert result["strategy_used"] == "weather_service"
    assert result["data"]["source"] == WEATHER_SOURCE == "wttr.in"
    assert [(urlsplit(url).hostname, authorization) for url, authorization in transport] == [("wttr.in", None)]


@pytest.mark.parametrize("given,asked_for", [
    (None, DEFAULT_SEARCH_RESULTS), (1, 1), (3, 3), (10, 10), (MAX_SEARCH_RESULTS, MAX_SEARCH_RESULTS),
    (50, MAX_SEARCH_RESULTS), (0, 1), (-3, 1), ("7", 7), ("abc", DEFAULT_SEARCH_RESULTS),
])
def test_max_results_reaches_the_engine_within_its_bounds(engine, transport, monkeypatch, given, asked_for):
    asked, _ = engine
    monkeypatch.setattr(web_tools, "_web_access_block_reason", lambda action: None)
    kwargs = {"query": "rust vs go performance"}
    if given is not None:
        kwargs["max_results"] = given
    result = web_tools.WebSearchTool().execute(**kwargs)
    assert asked == [asked_for]
    assert result.success and len(result.output["results"]) == asked_for
    assert result.output["source"] == SEARCH_ENGINE


def test_the_schema_declares_the_bounds():
    parameter = web_tools.WebSearchTool.parameters["max_results"]
    assert (parameter.minimum, parameter.maximum, parameter.default) == (1, MAX_SEARCH_RESULTS, DEFAULT_SEARCH_RESULTS)
    assert str(MAX_SEARCH_RESULTS) in parameter.description


def test_the_description_names_every_service_a_query_can_reach():
    description = web_tools.WebSearchTool.description
    for service in (SEARCH_ENGINE, WEATHER_SOURCE):
        assert service in description
    assert "nowhere else" in description and "no_results" in description
    everything_said = description + " ".join(
        parameter.description for parameter in web_tools.WebSearchTool.parameters.values())
    assert "jina" not in everything_said.lower()
    assert "via DuckDuckGo" not in everything_said


@pytest.mark.parametrize("url,domain", [
    ("https://bbc.co.uk/news", "bbc.co.uk"),
    ("https://user:pw@example.com:8443/x", "example.com"),
    ("https://news.example.com/a", "news.example.com"),
    ("http://93.184.216.34/path", "93.184.216.34"),
])
def test_the_structure_block_reports_the_host_name_and_no_subdomain_guess(url, domain):
    structure = web_tools.WebAnalysisTool()._analyze_structure(url, {})
    assert structure["domain"] == domain
    assert "has_subdomain" not in structure
