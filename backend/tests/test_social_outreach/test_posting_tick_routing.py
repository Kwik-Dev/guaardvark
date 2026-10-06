"""Which poster the approved-drafts tick picks, and what it does with a row none handles.

The real tick runs against the in-memory database. Posters are stand-ins that
record the call; no browser, Redis or network.
"""

from __future__ import annotations

import json

import pytest

from backend.api import social_outreach_api
from backend.models import SocialOutreachLog, db
from backend.services.social_outreach import kill_switch, transitions
from backend.tasks import social_outreach_tasks as tasks


@pytest.fixture(autouse=True)
def _posting_needs_web_access(web_access_on):
    """These tests post; posting is gated on web access being on."""


@pytest.fixture
def tick(app, monkeypatch):
    """Run the tick on the test app with every poster replaced. Returns
    (run, calls): run() runs one tick, calls lists (poster, args, kwargs)."""
    calls: list[tuple] = []

    def stand_in(name):
        def post(*args, **kwargs):
            calls.append((name, args, kwargs))
            before_submit = kwargs.get("before_submit")
            if before_submit is not None and not before_submit():
                return False, transitions.WITHDRAWN_BEFORE_SUBMIT
            return True, "ok"
        return post

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
        ("backend.services.social_outreach.general_poster.post_via_agent_loop", "agent_loop"),
        ("backend.services.social_outreach.self_share._submit_post_via_servo", "reddit_share"),
        ("backend.services.social_outreach.reddit_outreach.record_post_via_backend", "record_post"),
    ):
        monkeypatch.setattr(target, stand_in(name))
    return (lambda: tasks.tick_process_approved_drafts.run()), calls


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


def test_a_reddit_reply_is_unsupported_at_once_and_never_claimed(app, tick, monkeypatch):
    run, calls = tick
    claimed = []
    real_claim = transitions.claim
    monkeypatch.setattr(transitions, "claim", lambda row_id: claimed.append(row_id) or real_claim(row_id))
    row_id = _approved("reddit", "reply")

    result = run()

    row = _row(row_id)
    assert row.status == "unsupported"
    assert row.abort_reason == "unsupported: no poster for action 'reply' on platform 'reddit'"
    assert row.draft_text == "hello"
    assert result["unsupported"] == 1
    assert result["processed"] == 0
    assert claimed == []
    assert calls == []


@pytest.mark.parametrize("platform, action", [
    ("x", "reply"), ("facebook", "reply"), ("facebook", "share"), ("youtube", "share"),
    ("reddit", "abort"),
])
def test_every_action_without_a_poster_on_its_platform_is_unsupported(app, tick, platform, action):
    run, calls = tick
    row_id = _approved(platform, action)

    run()

    assert _row(row_id).status == "unsupported"
    assert calls == []


def test_a_capitalised_reddit_platform_takes_the_reddit_comment_poster(app, tick):
    run, calls = tick
    row_id = _approved("Reddit", "comment")

    result = run()

    assert result["processed"] == 1
    assert [name for name, _, _ in calls] == ["reddit_comment", "record_post"]
    assert calls[1][2]["platform"] == "reddit"
    assert _row(row_id).status == "submitting"


def test_a_capitalised_youtube_reply_takes_the_youtube_reply_poster(app, tick):
    run, calls = tick
    envelope = json.dumps({"draft": "Yes, 12GB is enough.", "anchor": "Here is how I run it."})
    row_id = _approved(
        "YouTube", "reply", draft_text=envelope, posted_text="Yes, 12GB is enough.",
        url="https://www.youtube.com/watch?v=abcdefghijk",
    )

    result = run()

    assert result["processed"] == 1
    assert [name for name, _, _ in calls] == ["youtube_reply", "record_post"]
    assert calls[0][1][1] == "Here is how I run it."
    assert calls[1][2]["platform"] == "youtube"
    assert _row(row_id).status == "submitting"


def test_a_platform_the_tick_does_not_post_is_left_alone(app, tick):
    """Discord rows are claimed by the Discord cog, not this tick."""
    run, calls = tick
    row_id = _approved("discord", "reply")

    run()

    assert _row(row_id).status == "approved"
    assert calls == []


def test_an_unsupported_row_cannot_be_rejected_and_says_why(app, client):
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    row_id = _approved("reddit", "reply")
    assert transitions.mark_unsupported(row_id, "unsupported: test")

    resp = client.post(f"/api/social-outreach/reject/{row_id}")

    assert resp.status_code == 409
    assert resp.get_json()["status"] == "unsupported"
    assert "nothing here can post that action on that platform" in resp.get_json()["error"]
