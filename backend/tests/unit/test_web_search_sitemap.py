"""The /websearch sitemap: form reads one sitemap through the public-only page
fetch and reports on it. The fetch is replaced here; nothing is sent and no
name is looked up."""

import pytest
from flask import Flask

from backend.api import web_search_api
from backend.utils.web_fetch import FetchFailed, FetchRefused, Page

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'

URLSET = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset {NS} xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url><loc>https://site.example/</loc><priority>1.0</priority></url>
  <url><loc>https://site.example/pricing</loc><lastmod>2026-09-01</lastmod>
       <changefreq>weekly</changefreq><priority>0.8</priority>
       <image:image><image:loc>https://site.example/hero.png</image:loc></image:image></url>
  <url><loc>https://site.example/about</loc><priority>0.3</priority></url>
  <url><loc>https://site.example/blog/post-1</loc></url>
</urlset>""".encode()

INDEX = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex {NS}>
  <sitemap><loc>https://site.example/sitemap-pages.xml</loc><lastmod>2026-09-02</lastmod></sitemap>
  <sitemap><loc>https://site.example/sitemap-posts.xml</loc></sitemap>
</sitemapindex>""".encode()


@pytest.fixture
def fetched(monkeypatch):
    """Serve ``pages[url]`` (a body, a Page or an exception) for the one fetch."""
    pages, calls = {}, []

    def fake_fetch_page(url, headers=None, public_only=False):
        calls.append((url, public_only))
        served = pages[url]
        if isinstance(served, Exception):
            raise served
        if isinstance(served, Page):
            return served
        return Page(url=url, body=served, media_type="application/xml", charset=None)

    monkeypatch.setattr(web_search_api, "fetch_page", fake_fetch_page)
    return pages, calls


def test_a_list_of_pages_is_counted_by_depth_with_its_landing_pages(fetched):
    pages, calls = fetched
    pages["https://site.example/sitemap.xml"] = URLSET
    report = web_search_api.read_sitemap("site.example/sitemap.xml")
    assert calls == [("https://site.example/sitemap.xml", True)]
    assert report["success"] and report["type"] == "urlset" and report["total"] == 4
    assert report["entries"][1] == {"loc": "https://site.example/pricing", "lastmod": "2026-09-01",
                                    "changefreq": "weekly", "priority": "0.8"}
    assert report["by_depth"] == {0: 1, 1: 2, 2: 1}
    assert [page["loc"] for page in report["landing_pages"]] == ["https://site.example/pricing"]
    assert "page_cut" not in report and "incomplete" not in report


def test_an_index_lists_its_sitemaps_without_fetching_them(fetched):
    pages, calls = fetched
    pages["https://site.example/sitemap_index.xml"] = INDEX
    report = web_search_api.read_sitemap("https://site.example/sitemap_index.xml")
    assert report["success"] and report["type"] == "sitemapindex" and report["total"] == 2
    assert [entry["loc"] for entry in report["entries"]] == [
        "https://site.example/sitemap-pages.xml", "https://site.example/sitemap-posts.xml"]
    assert "by_depth" not in report and len(calls) == 1


def test_the_listing_is_capped_but_the_count_is_not(fetched):
    pages, _ = fetched
    rows = "".join(f"<url><loc>https://site.example/p{i}</loc></url>" for i in range(50))
    pages["https://site.example/big.xml"] = f"<urlset {NS}>{rows}</urlset>".encode()
    report = web_search_api.read_sitemap("https://site.example/big.xml")
    assert report["total"] == 50
    assert len(report["entries"]) == web_search_api.SITEMAP_LISTED_ENTRIES


def test_a_sitemap_cut_at_the_size_limit_is_read_up_to_the_cut(fetched):
    pages, _ = fetched
    rows = "".join(f"<url><loc>https://site.example/p{i}</loc></url>" for i in range(10))
    body = f"<urlset {NS}>{rows}".encode()[:-20]      # the last entry is cut off
    note = "Only the first 2 MB of this page were read; it is larger."
    pages["https://site.example/cut.xml"] = Page(
        url="https://site.example/cut.xml", body=body, media_type="text/xml", charset=None, cut=note)
    report = web_search_api.read_sitemap("https://site.example/cut.xml")
    assert report["success"] and report["total"] == 9
    assert report["page_cut"] == note


def test_a_sitemap_that_breaks_off_says_so(fetched):
    pages, _ = fetched
    pages["https://site.example/broken.xml"] = (
        f"<urlset {NS}><url><loc>https://site.example/a</loc></url><url><loc>x</wrong>").encode()
    report = web_search_api.read_sitemap("https://site.example/broken.xml")
    assert report["success"] and report["total"] == 1
    assert report["incomplete"].startswith("The sitemap stops being well-formed XML after 1 entry (")


@pytest.mark.parametrize("body", [
    b"<!DOCTYPE html><html><head><title>Not found</title></head><body>404</body></html>",
    b"<rss version='2.0'><channel><title>feed</title></channel></rss>",
    b"just some text",
])
def test_a_page_that_is_not_a_sitemap_is_refused(fetched, body):
    pages, _ = fetched
    pages["https://site.example/sitemap.xml"] = body
    report = web_search_api.read_sitemap("https://site.example/sitemap.xml")
    assert not report["success"] and report["error"].startswith("Not a sitemap")


def test_fetch_refusals_and_failures_are_passed_on(fetched):
    pages, _ = fetched
    pages["https://inside.example/sitemap.xml"] = FetchRefused(
        "Refused to fetch https://inside.example/sitemap.xml: inside.example resolves to a private "
        "or local address (10.0.0.5)")
    pages["https://site.example/sitemap.xml.gz"] = FetchFailed(
        "Not a web page: https://site.example/sitemap.xml.gz is served as application/gzip.")
    refused = web_search_api.read_sitemap("https://inside.example/sitemap.xml")
    assert not refused["success"] and refused["error"].startswith("Refused to fetch")
    failed = web_search_api.read_sitemap("https://site.example/sitemap.xml.gz")
    assert not failed["success"] and failed["error"].startswith("Not a web page")


def test_only_http_and_https_are_fetched(fetched):
    _, calls = fetched
    report = web_search_api.read_sitemap("file:///etc/passwd")
    assert not report["success"] and report["error"].startswith("Refused")
    assert calls == []


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(web_search_api.web_search_bp)
    return app.test_client()


def test_the_route_reads_nothing_with_web_access_off(client, fetched, monkeypatch):
    _, calls = fetched
    monkeypatch.setattr(web_search_api, "get_web_access", lambda: False)
    response = client.post("/api/web-search/sitemap", json={"url": "https://site.example/sitemap.xml"})
    assert response.status_code == 403 and calls == []


def test_the_route_reports_the_sitemap_or_why_not(client, fetched, monkeypatch):
    pages, _ = fetched
    monkeypatch.setattr(web_search_api, "get_web_access", lambda: True)
    assert client.post("/api/web-search/sitemap", json={}).status_code == 400

    pages["https://site.example/sitemap.xml"] = URLSET
    ok = client.post("/api/web-search/sitemap", json={"url": "https://site.example/sitemap.xml"})
    assert ok.status_code == 200 and ok.get_json()["data"]["total"] == 4

    pages["https://site.example/page"] = b"<html><body>hello</body></html>"
    bad = client.post("/api/web-search/sitemap", json={"url": "https://site.example/page"})
    assert bad.status_code == 422 and bad.get_json()["message"].startswith("Not a sitemap")
