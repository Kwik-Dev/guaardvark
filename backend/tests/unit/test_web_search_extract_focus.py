"""extract_website_content keeps the page head by default and returns the passage a query is about when one is given."""

import pytest

try:
    from backend.api import web_search_api
except Exception:  # pragma: no cover - environment guard
    pytest.skip("Backend modules not available", allow_module_level=True)

MENU = " ".join(f"Menu item {i} Products Support Downloads" for i in range(120))
SPEC = "The model K20 bracket kit fits frames up to 48 inches and needs four M6 bolts."
HTML = f"<html><head><title>K20 bracket kit</title></head><body><div>{MENU}</div><p>{SPEC}</p></body></html>"


def _page():
    response = web_search_api.requests.Response()
    response.status_code = 200
    response.url = "https://example.com/k20"
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response._content = HTML.encode("utf-8")
    response._content_consumed = True
    return response


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(web_search_api.requests.Session, "get", lambda *args, **kwargs: _page())


def test_without_a_query_the_content_is_the_head_of_the_page(page):
    result = web_search_api.extract_website_content("https://example.com/k20")
    assert result["success"] and result["title"] == "K20 bracket kit"
    assert result["content"].startswith("Menu item 0") and len(result["content"]) == 2000
    assert SPEC not in result["content"]


def test_with_a_query_the_content_is_the_passage_about_it(page):
    result = web_search_api.extract_website_content("https://example.com/k20", query="Which bolts does the K20 bracket kit need?")
    assert SPEC in result["content"] and result["content_length"] == len(result["content"]) <= 2000


def test_fetch_url_and_analyze_website_pass_the_query_through(monkeypatch):
    """The chat tools carry the person's question to the extractor; no query, no focus."""
    from backend.tools import web_tools

    seen = []

    def fake_extract(url, query=None, public_only=False):
        seen.append((url, query, public_only))
        return {"success": True, "url": url, "title": "t", "description": "", "content": "c", "content_length": 1}

    monkeypatch.setattr(web_search_api, "extract_website_content", fake_extract)
    monkeypatch.setattr(web_tools, "_web_access_block_reason", lambda action: None)
    web_tools.FetchUrlTool().execute(url="example.com", query="  which bolts  ")
    web_tools.FetchUrlTool().execute(url="example.com")
    web_tools.WebAnalysisTool().execute(url="example.com", query="opening hours")
    # Both tools also ask the extractor to refuse private and local addresses.
    assert seen[0] == ("example.com", "which bolts", True)
    assert seen[1] == ("example.com", None, True)
    assert seen[2] == ("example.com", "opening hours", True)
