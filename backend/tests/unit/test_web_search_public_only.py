"""A URL in a web search is fetched only from a public address, on every path
that runs one: the web_search tool from chat and over MCP, the web-search
routes and /websearch. A refused URL ends the call without a search and says
why. The addresses are numeric and requests' transport is replaced, so no name
is looked up and nothing is sent."""

import io

import pytest
import requests
from flask import Flask
from requests.adapters import HTTPAdapter

from backend.api import web_search_api
from backend.routes import command_api
from backend.tools import web_tools
from backend.utils.web_fetch import PUBLIC_ONLY_NOTE

PRIVATE = "http://127.0.0.1:5000/api/health"
PUBLIC = "http://93.184.216.34/start"


@pytest.fixture
def searched(monkeypatch):
    asked = []

    def search(query, max_results=5):
        asked.append(query)
        return {"success": True, "results": [{"title": "t", "url": "https://example.org/", "snippet": "s"}],
                "snippet": "s", "total_results": 1}

    monkeypatch.setattr(web_search_api, "perform_duckduckgo_search", search)
    return asked


@pytest.fixture
def sent(monkeypatch):
    """Answer every request with a redirect to this machine instead of sending it."""
    urls = []

    def fake_send(self, request, **kwargs):
        urls.append(request.url)
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 302
        response.headers["Location"] = PRIVATE
        response.raw = io.BytesIO(b"")
        return response

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    return urls


def test_a_private_url_in_a_query_is_refused_and_not_searched(searched, sent):
    result = web_search_api.enhanced_web_search(f"what is on {PRIVATE}?")
    assert not result["success"]
    assert result["error"].startswith(f"Refused to fetch {PRIVATE}: 127.0.0.1 resolves to a private")
    assert result["error"].endswith(PUBLIC_ONLY_NOTE) and "process_file" in result["error"]
    assert searched == [] and sent == []


def test_a_redirect_to_this_machine_is_refused(searched, sent):
    result = web_search_api.enhanced_web_search(f"read {PUBLIC}")
    assert not result["success"] and result["error"].startswith(f"Refused to fetch {PRIVATE}")
    assert sent == [PUBLIC] and searched == []


@pytest.mark.parametrize("transport", [None, "mcp"])
def test_the_tool_refuses_the_same_way_from_chat_and_over_mcp(transport, searched, sent, monkeypatch):
    monkeypatch.setattr(web_tools, "_web_access_block_reason", lambda action: None)
    tool = web_tools.WebSearchTool()
    if transport:
        tool.set_context({"transport": transport})
    result = tool.execute(query=f"read {PRIVATE}")
    assert not result.success and result.error.startswith(f"Refused to fetch {PRIVATE}")
    assert searched == [] and sent == []


@pytest.mark.parametrize("path", ["/api/web-search/quick-search", "/api/web-search/search",
                                  "/api/command/websearch"])
def test_the_routes_refuse_a_private_url(path, searched, sent, monkeypatch):
    monkeypatch.setattr(web_search_api, "get_web_access", lambda: True)
    monkeypatch.setattr(command_api, "get_web_access", lambda: True)
    app = Flask(__name__)
    app.register_blueprint(web_search_api.web_search_bp)
    app.register_blueprint(command_api.command_bp)

    response = app.test_client().post(path, json={"query": PRIVATE})
    assert f"Refused to fetch {PRIVATE}" in response.get_data(as_text=True)
    assert searched == [] and sent == []


def test_a_public_url_is_still_read(searched, monkeypatch):
    page = b"<html><head><title>Public page</title></head><body><main>hello</main></body></html>"

    def fake_send(self, request, **kwargs):
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        response.headers["Content-Type"] = "text/html"
        response.raw = io.BytesIO(page)
        return response

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    result = web_search_api.enhanced_web_search(f"read {PUBLIC}")
    assert result["success"] and result["data"]["title"] == "Public page"
    assert searched == []


def test_a_file_url_points_at_process_file():
    result = web_search_api.extract_website_content("file:///etc/passwd")
    assert not result["success"] and "process_file" in result["error"]
