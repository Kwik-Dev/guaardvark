"""A page fetch is bounded in size and time, reads no redirect body, refuses a
response that is not a page, and says when the page came back in part.

The transport is faked at HTTPAdapter.send; nothing is sent and no name is
looked up (the fetches here are not public-only, which is the only path that
resolves a name before connecting).
"""

import io
import socket
import threading

import pytest
import requests
from requests.adapters import HTTPAdapter

from backend.api import web_search_api
from backend.tools import web_tools
from backend.utils import web_fetch
from backend.utils.hosts import OpenConnections, fetch_session

PAGE = b"<html><head><title>T</title></head><body><main>hello page</main></body></html>"


class _Body(io.BytesIO):
    """A response body that records how much of it was asked for."""

    def __init__(self, data: bytes):
        super().__init__(data)
        self.bytes_read = 0
        self.was_closed = False

    def read(self, amt=-1):
        data = super().read(amt)
        self.bytes_read += len(data)
        return data

    def close(self):
        self.was_closed = True
        super().close()


def _response(request, status=200, body=b"", content_type="text/html", location=None):
    response = requests.Response()
    response.request = request
    response.url = request.url
    response.status_code = status
    if content_type:
        response.headers["Content-Type"] = content_type
    if location:
        response.headers["Location"] = location
    response.raw = _Body(body)
    return response


@pytest.fixture
def transport(monkeypatch):
    """Serve ``routes[url]`` (a function of the request) instead of sending."""
    routes, sent = {}, []

    def fake_send(self, request, **kwargs):
        response = routes[request.url](request)
        sent.append((request.url, kwargs.get("timeout"), response))
        return response

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    return routes, sent


def test_a_page_within_the_limit_is_read_whole(transport):
    routes, _ = transport
    routes["http://site.example/"] = lambda r: _response(r, body=PAGE)
    page = web_fetch.fetch_page("http://site.example/")
    assert page.body == PAGE and page.cut is None
    assert page.media_type == "text/html"


def test_a_page_over_the_limit_is_cut_there_and_says_so(transport):
    routes, sent = transport
    big = b"<html><body><main>" + b"word " * (web_fetch.MAX_PAGE_BYTES // 5 * 4)
    assert len(big) > 3 * web_fetch.MAX_PAGE_BYTES
    routes["http://site.example/big"] = lambda r: _response(r, body=big)
    page = web_fetch.fetch_page("http://site.example/big")
    assert len(page.body) == web_fetch.MAX_PAGE_BYTES
    assert "Only the first 2 MB" in page.cut
    # The rest of the body was never read from the server.
    assert sent[-1][2].raw.bytes_read <= web_fetch.MAX_PAGE_BYTES + web_fetch._CHUNK_BYTES


def test_a_page_of_exactly_the_limit_is_not_reported_as_cut(transport):
    routes, _ = transport
    exact = b"a" * web_fetch.MAX_PAGE_BYTES
    routes["http://site.example/exact"] = lambda r: _response(r, body=exact, content_type="text/plain")
    page = web_fetch.fetch_page("http://site.example/exact")
    assert len(page.body) == web_fetch.MAX_PAGE_BYTES and page.cut is None


def test_a_redirect_body_is_not_read(transport):
    routes, sent = transport
    routes["http://site.example/old"] = lambda r: _response(
        r, status=302, body=b"x" * 500_000, location="/new")
    routes["http://site.example/new"] = lambda r: _response(r, body=PAGE)
    page = web_fetch.fetch_page("http://site.example/old")
    assert page.url == "http://site.example/new" and page.body == PAGE
    redirect = sent[0][2]
    assert redirect.raw.bytes_read == 0 and redirect.raw.was_closed


def test_every_hop_waits_no_longer_than_the_idle_limit(transport):
    routes, sent = transport
    routes["http://site.example/old"] = lambda r: _response(r, status=301, location="http://site.example/new")
    routes["http://site.example/new"] = lambda r: _response(r, body=PAGE)
    web_fetch.fetch_page("http://site.example/old")
    assert [url for url, _, _ in sent] == ["http://site.example/old", "http://site.example/new"]
    assert all(0 < timeout <= web_fetch.IDLE_SECONDS for _, timeout, _ in sent)


class _Clock:
    """time.monotonic() that moves ``step`` seconds every time it is read."""

    def __init__(self, step):
        self.now, self.step = 1000.0, step

    def monotonic(self):
        self.now += self.step
        return self.now


def test_a_body_still_arriving_at_the_deadline_comes_back_in_part(transport, monkeypatch):
    routes, sent = transport
    body = b"<html><body><main>" + b"slow " * 100_000
    routes["http://site.example/slow"] = lambda r: _response(r, body=body)
    monkeypatch.setattr(web_fetch, "time", _Clock(step=web_fetch.DEADLINE_SECONDS / 4))
    page = web_fetch.fetch_page("http://site.example/slow")
    assert 0 < len(page.body) < len(body)
    assert f"still arriving after {web_fetch.DEADLINE_SECONDS} s" in page.cut
    assert sent[-1][2].raw.bytes_read < len(body)


def test_a_redirect_chain_that_uses_up_the_time_fails_with_the_limit_named(transport, monkeypatch):
    routes, sent = transport
    for n in range(10):
        routes[f"http://site.example/hop{n}"] = (
            lambda r, n=n: _response(r, status=302, location=f"/hop{n + 1}"))
    monkeypatch.setattr(web_fetch, "time", _Clock(step=web_fetch.DEADLINE_SECONDS / 3))
    with pytest.raises(web_fetch.FetchFailed, match=f"within {web_fetch.DEADLINE_SECONDS} s"):
        web_fetch.fetch_page("http://site.example/hop0")
    assert len(sent) < 10


def test_a_response_that_is_not_a_page_is_refused_before_its_body_is_read(transport):
    routes, sent = transport
    routes["http://site.example/file.pdf"] = lambda r: _response(
        r, body=b"%PDF-1.4\n" + bytes(range(256)) * 100, content_type="application/pdf")
    with pytest.raises(web_fetch.FetchFailed, match="served as application/pdf"):
        web_fetch.fetch_page("http://site.example/file.pdf")
    assert sent[0][2].raw.bytes_read == 0


def test_binary_data_with_no_content_type_is_refused(transport):
    routes, _ = transport
    routes["http://site.example/blob"] = lambda r: _response(
        r, body=b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4, content_type=None)
    with pytest.raises(web_fetch.FetchFailed, match="binary data"):
        web_fetch.fetch_page("http://site.example/blob")


@pytest.mark.parametrize("media_type,readable", [
    ("text/html", True), ("text/plain", True), ("application/xhtml+xml", True), ("application/json", True),
    ("application/rss+xml", True), ("", True),
    ("application/pdf", False), ("image/png", False), ("application/zip", False),
    ("application/octet-stream", False), ("video/mp4", False),
])
def test_which_response_types_are_read(media_type, readable):
    assert web_fetch.is_page_type(media_type) is readable


def test_an_http_error_is_still_reported_as_one(transport):
    routes, _ = transport
    routes["http://site.example/missing"] = lambda r: _response(r, status=404, body=b"nope")
    result = web_search_api.extract_website_content("http://site.example/missing")
    assert not result["success"] and "404" in result["error"]


def test_the_result_says_when_the_page_was_cut_and_only_then(transport, monkeypatch):
    routes, _ = transport
    big = b"<html><head><title>Big</title></head><body><main>" + b"word " * web_fetch.MAX_PAGE_BYTES
    routes["http://site.example/big"] = lambda r: _response(r, body=big)
    routes["http://site.example/"] = lambda r: _response(r, body=PAGE)
    cut = web_search_api.extract_website_content("http://site.example/big")
    assert cut["success"] and cut["title"] == "Big" and "Only the first 2 MB" in cut["page_cut"]
    whole = web_search_api.extract_website_content("http://site.example/")
    assert whole["success"] and "page_cut" not in whole


def test_the_tools_pass_page_cut_on(monkeypatch):
    note = "Only the first 2 MB of this page were read; it is larger."

    def fake_extract(url, query=None, public_only=False):
        return {"success": True, "url": url, "final_url": url, "title": "t", "description": "",
                "content": "c", "content_length": 1, "page_word_count": 1, "page_cut": note}

    monkeypatch.setattr(web_search_api, "extract_website_content", fake_extract)
    monkeypatch.setattr(web_tools, "_web_access_block_reason", lambda action: None)
    assert web_tools.FetchUrlTool().execute(url="https://site.example/").output["page_cut"] == note
    assert web_tools.WebAnalysisTool().execute(url="https://site.example/").output["page_cut"] == note
    searched = web_tools.WebSearchTool().execute(query="read https://site.example/")
    assert searched.output["page_cut"] == note


def test_the_tool_descriptions_state_the_limits_in_force():
    for tool in (web_tools.FetchUrlTool, web_tools.WebAnalysisTool):
        text = tool.parameters["url"].description
        assert f"{web_fetch.DEADLINE_SECONDS} s" in text and f"{web_fetch.IDLE_SECONDS} s" in text
        assert web_fetch.megabytes(web_fetch.MAX_PAGE_BYTES) in text and "page_cut" in text


def test_cutting_a_session_ends_a_read_that_is_blocked():
    """The overall deadline works by shutting the session's sockets down."""
    ours, theirs = socket.socketpair()
    connections = OpenConnections()
    connections.add(ours)
    got = []
    reader = threading.Thread(target=lambda: got.append(ours.recv(10)), daemon=True)
    reader.start()
    reader.join(0.2)
    assert reader.is_alive()            # blocked: the other end sends nothing
    connections.cut()
    reader.join(5)
    assert not reader.is_alive() and got == [b""]
    assert connections.was_cut
    ours.close()
    theirs.close()


def test_a_socket_connected_after_the_cut_is_shut_down_at_once():
    ours, theirs = socket.socketpair()
    connections = OpenConnections()
    connections.cut()
    connections.add(ours)
    ours.settimeout(5)
    assert ours.recv(10) == b""
    ours.close()
    theirs.close()


def test_the_fetch_session_keeps_the_netrc_and_redirect_rules():
    with fetch_session() as session:
        assert session.connections.was_cut is False
        prepared = session.prepare_request(requests.Request("GET", "https://site.example/"))
        assert "Authorization" not in prepared.headers
        redirect = requests.Response()
        redirect.status_code = 302
        redirect.headers["Location"] = "/next"
        redirect.raw = _Body(b"x" * 1000)
        assert list(session.resolve_redirects(redirect, prepared, yield_requests=True)) == []
        assert redirect.raw.bytes_read == 0
