"""extract_website_content keeps the page head by default and returns the passage a query is about when one is given."""

import pytest

try:
    from backend.api import web_search_api
except Exception:  # pragma: no cover - environment guard
    pytest.skip("Backend modules not available", allow_module_level=True)

MENU = " ".join(f"Menu item {i} Products Support Downloads" for i in range(120))
SPEC = "The model K20 bracket kit fits frames up to 48 inches and needs four M6 bolts."
HTML = f"<html><head><title>K20 bracket kit</title></head><body><div>{MENU}</div><p>{SPEC}</p></body></html>"


class _Page:
    content = HTML.encode("utf-8")

    def raise_for_status(self):
        return None


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(web_search_api.requests, "get", lambda *args, **kwargs: _Page())


def test_without_a_query_the_content_is_the_head_of_the_page(page):
    result = web_search_api.extract_website_content("https://example.com/k20")
    assert result["success"] and result["title"] == "K20 bracket kit"
    assert result["content"].startswith("Menu item 0") and len(result["content"]) == 2000
    assert SPEC not in result["content"]


def test_with_a_query_the_content_is_the_passage_about_it(page):
    result = web_search_api.extract_website_content("https://example.com/k20", query="Which bolts does the K20 bracket kit need?")
    assert SPEC in result["content"] and result["content_length"] == len(result["content"]) <= 2000
