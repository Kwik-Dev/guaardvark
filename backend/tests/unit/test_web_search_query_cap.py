"""The model's own web_search call is held to the query cap enhanced chat applies
to the person's message: a query over MAX_SEARCH_QUERY_CHARS is refused and
nothing is sent. The search itself is a stub."""

import pytest

from backend.api import web_search_api
from backend.api.enhanced_chat_api import EnhancedChatManager
from backend.tools import web_tools
from backend.utils.web_search_sources import MAX_SEARCH_QUERY_CHARS


@pytest.fixture
def sent(monkeypatch):
    queries = []

    def search(query, max_results=5):
        queries.append(query)
        return {"success": True, "strategy_used": "stub",
                "data": {"results": [], "snippet": "", "source": "stub"}}

    monkeypatch.setattr(web_search_api, "enhanced_web_search", search)
    monkeypatch.setattr(web_tools, "_web_access_block_reason", lambda action: None)
    return queries


def test_enhanced_chat_and_the_tool_share_the_cap():
    assert EnhancedChatManager._WEB_SEARCH_MAX_CHARS == MAX_SEARCH_QUERY_CHARS == 300


def test_a_query_at_the_cap_is_sent(sent):
    result = web_tools.WebSearchTool().execute(query="a" * MAX_SEARCH_QUERY_CHARS)
    assert result.success
    assert len(sent) == 1


def test_a_longer_query_is_refused_unsent(sent):
    result = web_tools.WebSearchTool().execute(query="a" * (MAX_SEARCH_QUERY_CHARS + 1))
    assert not result.success
    assert sent == []
    assert f"longer than {MAX_SEARCH_QUERY_CHARS} characters" in result.error
    assert "send all of it to the search engine" in result.error


def test_web_access_off_still_refuses_first(monkeypatch, sent):
    monkeypatch.setattr(web_tools, "_web_access_block_reason", lambda action: "Web access is disabled.")
    result = web_tools.WebSearchTool().execute(query="a" * 400)
    assert result.error == "Web access is disabled."
    assert sent == []
