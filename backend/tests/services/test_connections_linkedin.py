"""LinkedIn provider: OAuth, token expiry, Posts API requests and deletes.

The provider's one HTTP function is replaced, so nothing here reaches LinkedIn.
"""

import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from backend.services.connections import base
from backend.services.connections.providers import linkedin

REDIRECT = "http://localhost:5173/connections"


class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


@pytest.fixture
def http(monkeypatch):
    """Answers queued per URL suffix; every call recorded."""
    calls, answers = [], {}

    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs))
        for suffix, queue in answers.items():
            if url.endswith(suffix) or suffix in url:
                if not queue:
                    pytest.fail(f"no answer left for {url}")
                answer = queue.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return answer
        pytest.fail(f"unexpected request {method} {url}")

    monkeypatch.setattr(linkedin, "_request", fake)

    def answer(suffix, *responses):
        answers.setdefault(suffix, []).extend(responses)

    return type("Http", (), {"calls": calls, "answer": staticmethod(answer)})


def _ctx(secrets=None, config=None):
    saved, statuses = {}, []
    ctx = base.ConnCtx(
        connection={},
        config=dict(config or {}),
        secrets=dict(secrets or {}),
        save_secrets=saved.update,
        set_status=lambda status, error: statuses.append((status, error)),
    )
    return ctx, saved, statuses


def _authorized(**extra):
    secrets = {
        "client_id": "cid",
        "client_secret": "csecret",
        "access_token": "tok",
        "access_token_expires_at": str(int(time.time()) + 30 * 86400),
    }
    secrets.update(extra)
    return secrets


def _request(**kwargs):
    return base.PublishRequest(record_id=1, connection_id=1, **kwargs)


# --- OAuth ---------------------------------------------------------------------------
def test_authorize_url_asks_for_the_member_post_scopes():
    ctx, _, _ = _ctx({"client_id": "cid"})
    url = urlparse(linkedin.authorize_url(ctx, REDIRECT, "st"))
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == linkedin.AUTH_URI
    assert query["scope"] == ["openid profile w_member_social"]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == [REDIRECT] and query["state"] == ["st"]


def test_authorize_url_needs_a_client_id():
    with pytest.raises(ValueError):
        linkedin.authorize_url(_ctx({})[0], REDIRECT, "st")


def test_exchange_sends_the_form_and_keeps_the_expiry(http):
    http.answer("/oauth/v2/accessToken", _Resp(200, {"access_token": "new", "expires_in": 5184000}))
    ctx, _, _ = _ctx({"client_id": "cid", "client_secret": "csecret"})

    values = linkedin.exchange_code(ctx, " the-code ", REDIRECT)

    method, url, kwargs = http.calls[0]
    assert (method, url) == ("POST", linkedin.TOKEN_URI)
    assert kwargs["data"] == {
        "grant_type": "authorization_code", "code": "the-code", "client_id": "cid",
        "client_secret": "csecret", "redirect_uri": REDIRECT,
    }
    assert values["access_token"] == "new"
    assert abs(int(values["access_token_expires_at"]) - (time.time() + 5184000)) < 5
    assert "refresh_token" not in values


def test_exchange_keeps_a_refresh_token_when_linkedin_issues_one(http):
    http.answer("/oauth/v2/accessToken", _Resp(200, {
        "access_token": "new", "expires_in": 60, "refresh_token": "r", "refresh_token_expires_in": 3600}))
    values = linkedin.exchange_code(_ctx({"client_id": "cid"})[0], "c", REDIRECT)
    assert values["refresh_token"] == "r" and "refresh_token_expires_at" in values


def test_a_refused_code_says_what_to_check(http):
    http.answer("/oauth/v2/accessToken", _Resp(400, {"error": "invalid_request", "error_description": "bad redirect"}))
    with pytest.raises(RuntimeError, match="redirect URL"):
        linkedin.exchange_code(_ctx({"client_id": "cid"})[0], "c", REDIRECT)


# --- tokens and Test -----------------------------------------------------------------
def test_test_reads_the_member_and_the_expiry(http):
    http.answer("/v2/userinfo", _Resp(200, {"sub": "abc", "name": "Sam Example"}))
    ctx, _, _ = _ctx(_authorized())

    ok, message, discovered = linkedin.test(ctx)

    assert ok and "Sam Example" in message
    assert discovered["config"] == {"member_urn": "urn:li:person:abc"}
    assert isinstance(discovered["token_expires_at"], datetime)
    assert http.calls[0][2]["headers"] == {"Authorization": "Bearer tok"}


def test_test_without_authorization_makes_no_call(http):
    ok, message, discovered = linkedin.test(_ctx({"client_id": "cid"})[0])
    assert not ok and "Authorize with LinkedIn" in message
    assert http.calls == [] and discovered == {}


def test_an_expired_token_without_refresh_reports_expired(http):
    ctx, _, _ = _ctx(_authorized(access_token_expires_at=str(int(time.time()) - 10)))
    ok, message, discovered = linkedin.test(ctx)
    assert not ok and discovered == {"expired": True}
    assert "60 days" in message and http.calls == []


def test_an_expired_token_is_refreshed_where_linkedin_allows_it(http):
    http.answer("/oauth/v2/accessToken", _Resp(200, {"access_token": "fresh", "expires_in": 5184000}))
    http.answer("/v2/userinfo", _Resp(200, {"sub": "abc", "name": "Sam"}))
    ctx, saved, _ = _ctx(_authorized(access_token_expires_at="1", refresh_token="r"))

    ok, _, _ = linkedin.test(ctx)

    assert ok
    assert http.calls[0][2]["data"]["grant_type"] == "refresh_token"
    assert saved["access_token"] == "fresh"
    assert http.calls[1][2]["headers"]["Authorization"] == "Bearer fresh"


def test_a_revoked_token_reads_as_expired(http):
    http.answer("/v2/userinfo", _Resp(401, {"message": "revoked"}))
    ok, _, discovered = linkedin.test(_ctx(_authorized())[0])
    assert not ok and discovered == {"expired": True}


def test_an_unreachable_linkedin_is_reported(http):
    http.answer("/v2/userinfo", requests.ConnectionError("down"))
    ok, message, _ = linkedin.test(_ctx(_authorized())[0])
    assert not ok and message.startswith("Could not reach LinkedIn")


# --- publish ---------------------------------------------------------------------------
def test_publish_sends_the_versioned_posts_request(http):
    http.answer("/rest/posts", _Resp(201, None, {"x-restli-id": "urn:li:share:1"}))
    ctx, _, _ = _ctx(_authorized(), {"member_urn": "urn:li:person:abc"})

    result = linkedin.publish(ctx, _request(body="Hello", link_url="https://example.com", visibility="connections"),
                              lambda *a: None)

    assert result.ok and result.remote_id == "urn:li:share:1"
    assert result.remote_url == "https://www.linkedin.com/feed/update/urn:li:share:1/"
    method, url, kwargs = http.calls[0]
    assert (method, url) == ("POST", linkedin.POSTS_URL)
    headers = kwargs["headers"]
    assert headers["LinkedIn-Version"] == linkedin.API_VERSION
    assert re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", linkedin.API_VERSION)
    assert headers["X-Restli-Protocol-Version"] == "2.0.0"
    assert headers["Authorization"] == "Bearer tok"
    body = kwargs["json"]
    assert body["author"] == "urn:li:person:abc"
    assert body["commentary"] == "Hello\n\nhttps://example.com"
    assert body["visibility"] == "CONNECTIONS"
    assert body["lifecycleState"] == "PUBLISHED"
    assert body["distribution"]["feedDistribution"] == "MAIN_FEED"


def test_publish_looks_up_the_member_when_test_has_not(http):
    http.answer("/v2/userinfo", _Resp(200, {"sub": "xyz"}))
    http.answer("/rest/posts", _Resp(201, None, {"x-restli-id": "urn:li:share:2"}))

    result = linkedin.publish(_ctx(_authorized())[0], _request(body="Hi", visibility="public"), lambda *a: None)

    assert result.ok
    assert http.calls[1][2]["json"]["author"] == "urn:li:person:xyz"
    assert http.calls[1][2]["json"]["visibility"] == "PUBLIC"


def test_publish_marks_an_expired_token_and_stops(http):
    ctx, _, statuses = _ctx(_authorized(access_token_expires_at="1"), {"member_urn": "urn:li:person:a"})
    result = linkedin.publish(ctx, _request(body="Hi"), lambda *a: None)
    assert not result.ok and "authorize" in result.message.lower()
    assert statuses and statuses[0][0] == base.STATUS_EXPIRED
    assert http.calls == []


def test_publish_marks_a_401_as_expired(http):
    http.answer("/rest/posts", _Resp(401, {"message": "Expired"}))
    ctx, _, statuses = _ctx(_authorized(), {"member_urn": "urn:li:person:a"})
    result = linkedin.publish(ctx, _request(body="Hi"), lambda *a: None)
    assert not result.ok and statuses[0][0] == base.STATUS_EXPIRED


@pytest.mark.parametrize("status, needle", [
    (403, "Share on LinkedIn"),
    (429, "midnight UTC"),
    (422, "422"),
])
def test_publish_explains_a_refusal(http, status, needle):
    http.answer("/rest/posts", _Resp(status, {"message": "nope"}))
    ctx, _, _ = _ctx(_authorized(), {"member_urn": "urn:li:person:a"})
    result = linkedin.publish(ctx, _request(body="Hi"), lambda *a: None)
    assert not result.ok and needle in result.message and "nope" in result.message


def test_publish_refuses_media():
    item = base.MediaItem(path="/x/a.png", mime="image/png", bytes=1)
    result = linkedin.publish(_ctx(_authorized())[0], _request(body="Hi", media=[item]), lambda *a: None)
    assert not result.ok


@pytest.mark.parametrize("text, expected", [
    ("Plain words.", "Plain words."),
    ("Launch (v3) is out", "Launch \\(v3\\) is out"),
    ("snake_case *bold* ~x~ a|b {c} [d] <e> @f \\g", "snake\\_case \\*bold\\* \\~x\\~ a\\|b \\{c\\} \\[d\\] \\<e\\> \\@f \\\\g"),
    ("#launch day, issue #1 and C#", "#launch day, issue \\#1 and C\\#"),
    ("see https://example.com/a_b#top", "see https://example.com/a\\_b\\#top"),
    ("(#tag)", "\\(#tag\\)"),
])
def test_commentary_escapes_little_text(text, expected):
    assert linkedin.commentary(text) == expected


# --- delete ----------------------------------------------------------------------------
def test_delete_sends_the_encoded_urn(http):
    http.answer("/rest/posts/", _Resp(204))
    result = linkedin.delete(_ctx(_authorized())[0], base.DeleteRequest(record_id=1, remote_id="urn:li:share:1"))
    method, url, kwargs = http.calls[0]
    assert result.ok and method == "DELETE"
    assert url == f"{linkedin.POSTS_URL}/urn%3Ali%3Ashare%3A1"
    assert kwargs["headers"]["X-RestLi-Method"] == "DELETE"
    assert kwargs["headers"]["LinkedIn-Version"] == linkedin.API_VERSION


def test_delete_of_a_post_already_gone_succeeds(http):
    http.answer("/rest/posts/", _Resp(404))
    assert linkedin.delete(_ctx(_authorized())[0], base.DeleteRequest(record_id=1, remote_id="urn:li:share:1")).ok


def test_delete_refuses_something_that_is_not_a_post(http):
    result = linkedin.delete(_ctx(_authorized())[0], base.DeleteRequest(record_id=1, remote_id="12345"))
    assert not result.ok and http.calls == []


# --- declarations ---------------------------------------------------------------------
def test_linkedin_declares_its_rules():
    caps = linkedin.SPEC.capabilities
    assert caps.native_scheduling is None and caps.schedule_refusal
    assert caps.deletable and not caps.images and caps.max_text_chars == 3000
    assert caps.ai_disclosure.required_when == base.DISCLOSE_NEVER
    assert caps.bot_label.kind == base.BOT_LABEL_NONE
    assert not linkedin.SPEC.posts_to_owned_channel
    assert any("{redirect_uri}" in step for step in linkedin.SPEC.setup_steps)
