"""Which poster a watched post picks, and what the approved-drafts tick does now.

The real watched-post task and the real tick run against the in-memory
database. Posters are stand-ins that record the call; no browser, Redis or
network. The Agent Screen counts as open (``watching``).
"""

from __future__ import annotations

import json
import time

import pytest

from backend.api import social_outreach_api
from backend.models import SocialOutreachLog, db
from backend.services.social_outreach import kill_switch, platforms, transitions
from backend.tasks import social_outreach_tasks as tasks


@pytest.fixture(autouse=True)
def _posting_needs_web_access(web_access_on):
    """These tests post; posting is gated on web access being on."""


@pytest.fixture
def post(app, monkeypatch, fake_redis, watching):
    """Run one watched post on the test app with every poster replaced.
    Returns (run, calls): run(row_id) posts that draft, calls lists
    (poster, args, kwargs)."""
    calls: list[tuple] = []

    def stand_in(name):
        def poster(*args, **kwargs):
            calls.append((name, args, kwargs))
            before_submit = kwargs.get("before_submit")
            if before_submit is not None and not before_submit():
                return False, transitions.WITHDRAWN_BEFORE_SUBMIT
            return True, "ok"
        return poster

    def with_ctx(fn, *args, **kwargs):
        with app.app_context():
            return fn(*args, **kwargs)

    monkeypatch.setattr(tasks, "_with_app_context", with_ctx)
    monkeypatch.setattr(kill_switch, "is_enabled", lambda: True)
    monkeypatch.setattr(kill_switch, "cadence_allows_post", lambda platform: (True, None))
    for target, name in (
        ("backend.services.social_outreach.reddit_outreach.post_comment_via_servo", "reddit_comment"),
        ("backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo", "youtube_comment"),
        ("backend.services.social_outreach.youtube_outreach.post_youtube_reply_via_servo", "youtube_reply"),
        ("backend.services.social_outreach.facebook_outreach.post_comment_via_bidi", "facebook_comment"),
        ("backend.services.social_outreach.general_poster.post_via_agent_loop", "agent_loop"),
        ("backend.services.social_outreach.self_share._submit_post_via_servo", "reddit_share"),
        ("backend.services.social_outreach.reddit_outreach.record_post_via_backend", "record_post"),
    ):
        monkeypatch.setattr(target, stand_in(name))
    return (lambda row_id: tasks.post_watched_draft.run(row_id, time.time())), calls


@pytest.fixture
def tick(app, monkeypatch):
    def with_ctx(fn, *args, **kwargs):
        with app.app_context():
            return fn(*args, **kwargs)

    monkeypatch.setattr(tasks, "_with_app_context", with_ctx)
    monkeypatch.setattr(kill_switch, "is_enabled", lambda: True)
    return lambda: tasks.tick_process_approved_drafts.run()


def _approved(platform, action, draft_text="hello", posted_text=None,
              url="https://www.reddit.com/r/x/comments/abc/t/"):
    row = SocialOutreachLog(
        platform=platform, action=action, status="approved", draft_text=draft_text,
        posted_text=posted_text, target_url=url, target_thread_id="abc",
    )
    db.session.add(row)
    db.session.commit()
    return row.id


def _row(row_id):
    db.session.expire_all()
    return db.session.get(SocialOutreachLog, row_id)


# ---- the tick posts nothing ------------------------------------------------------------

def test_the_tick_never_posts_an_approved_browser_draft(app, tick, monkeypatch):
    def poster(*args, **kwargs):
        raise AssertionError("the tick must not post")

    for target in (
        "backend.services.social_outreach.reddit_outreach.post_comment_via_servo",
        "backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo",
        "backend.services.social_outreach.facebook_outreach.post_comment_via_bidi",
        "backend.services.social_outreach.general_poster.post_via_agent_loop",
    ):
        monkeypatch.setattr(target, poster)
    ids = [
        _approved("reddit", "comment"),
        _approved("youtube", "comment", url="https://www.youtube.com/watch?v=abcdefghijk"),
        _approved("facebook", "comment", url="https://www.facebook.com/p/1"),
    ]

    result = tick()

    assert result == {"unsupported": 0, "waiting_for_watched_post": 3}
    assert [_row(i).status for i in ids] == ["approved", "approved", "approved"]


@pytest.mark.parametrize("platform", ["x", "twitter", "X", "linkedin"])
def test_the_tick_closes_an_approved_draft_for_a_refused_platform(app, tick, platform):
    row_id = _approved(platform, "comment", url="https://x.com/someone/status/1")

    result = tick()

    row = _row(row_id)
    assert row.status == "unsupported"
    assert row.abort_reason == platforms.refusal_for(platform)
    assert result["unsupported"] == 1


@pytest.mark.parametrize("platform, action", [
    ("reddit", "reply"), ("facebook", "reply"), ("facebook", "share"), ("youtube", "share"),
    ("reddit", "abort"),
])
def test_every_action_without_a_poster_on_its_platform_is_unsupported(app, tick, platform, action):
    row_id = _approved(platform, action)

    tick()

    row = _row(row_id)
    assert row.status == "unsupported"
    assert row.abort_reason == f"unsupported: no poster for action '{action}' on platform '{platform}'"
    assert row.draft_text == "hello"


def test_a_platform_the_tick_does_not_post_is_left_alone(app, tick):
    """Discord rows are claimed by the Discord cog, not this tick."""
    row_id = _approved("discord", "reply")

    tick()

    assert _row(row_id).status == "approved"


# ---- one watched post picks the poster for its platform and action -------------------

def test_a_capitalised_reddit_platform_takes_the_reddit_comment_poster(app, post):
    run, calls = post
    row_id = _approved("Reddit", "comment")

    result = run(row_id)

    assert result["posted"] is True
    assert [name for name, _, _ in calls] == ["reddit_comment", "record_post"]
    assert calls[1][2]["platform"] == "reddit"
    assert _row(row_id).status == "submitting"


def test_a_capitalised_youtube_reply_takes_the_youtube_reply_poster(app, post):
    run, calls = post
    envelope = json.dumps({"draft": "Yes, 12GB is enough.", "anchor": "Here is how I run it."})
    row_id = _approved(
        "YouTube", "reply", draft_text=envelope, posted_text="Yes, 12GB is enough.",
        url="https://www.youtube.com/watch?v=abcdefghijk",
    )

    result = run(row_id)

    assert result["posted"] is True
    assert [name for name, _, _ in calls] == ["youtube_reply", "record_post"]
    assert calls[0][1][1] == "Here is how I run it."
    assert calls[1][2]["platform"] == "youtube"


def test_a_facebook_comment_takes_the_facebook_poster(app, post):
    run, calls = post
    row_id = _approved("facebook", "comment", url="https://www.facebook.com/p/1")

    assert run(row_id)["posted"] is True
    assert [name for name, _, _ in calls] == ["facebook_comment", "record_post"]


@pytest.mark.parametrize("platform", ["x", "twitter", "linkedin"])
def test_a_watched_post_never_reaches_a_poster_for_a_refused_platform(app, post, platform):
    run, calls = post
    row_id = _approved(platform, "comment", url="https://x.com/someone/status/1")

    result = run(row_id)

    assert result["posted"] is False
    assert result["message"] == platforms.refusal_for(platform)
    assert calls == []
    assert _row(row_id).status == "approved"


def test_a_reply_without_its_anchor_is_aborted_without_a_poster(app, post):
    run, calls = post
    row_id = _approved("youtube", "reply", draft_text="plain", posted_text="Yes.",
                       url="https://www.youtube.com/watch?v=abcdefghijk")

    result = run(row_id)

    assert result["posted"] is False
    assert calls == []
    row = _row(row_id)
    assert (row.status, row.abort_reason) == ("aborted", "reply_missing_anchor_or_text")


def test_an_unsupported_row_cannot_be_rejected_and_says_why(app, client):
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    row_id = _approved("reddit", "reply")
    assert transitions.mark_unsupported(row_id, "unsupported: test")

    resp = client.post(f"/api/social-outreach/reject/{row_id}")

    assert resp.status_code == 409
    assert resp.get_json()["status"] == "unsupported"
    assert "nothing here can post that action on that platform" in resp.get_json()["error"]
