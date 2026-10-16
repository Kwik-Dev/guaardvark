"""A browser post happens only as one watched post a person started.

Reddit, YouTube and Facebook drafts post through the agent browser, so each
one needs the person's approval, then a "Post now, I'm watching" click with
the Agent Screen open; one click starts one post. X and LinkedIn are refused
everywhere a draft could enter or leave the queue.

The real routes and the real watched_post run against the in-memory
database. Redis is a FakeRedis, the Agent Screen's presence is a stand-in
(``watching``), posters are recorders and the Celery dispatch is captured.
No browser, broker or network.
"""

from __future__ import annotations

import json
import time

import pytest

from backend.api import social_outreach_api
from backend.models import SocialOutreachLog, db
from backend.services.social_outreach import kill_switch, platforms, transitions, watched_post
from backend.tasks import social_outreach_tasks as tasks
from backend.utils import agent_screen_presence

REDDIT_THREAD = "https://www.reddit.com/r/x/comments/abc/t/"


@pytest.fixture(autouse=True)
def _posting_needs_web_access(web_access_on):
    """Posting is gated on web access being on."""


@pytest.fixture
def outreach(app, client, monkeypatch, fake_redis, watching):
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    monkeypatch.setattr(kill_switch, "is_enabled", lambda: True)
    monkeypatch.setattr(kill_switch, "cadence_allows_post", lambda platform: (True, None))
    dispatched: list[tuple] = []
    published: list[str] = []
    monkeypatch.setattr(watched_post, "_dispatch", lambda event_id, clicked_at: dispatched.append((event_id, clicked_at)))

    def with_ctx(fn, *args, **kwargs):
        with app.app_context():
            return fn(*args, **kwargs)

    monkeypatch.setattr(tasks, "_with_app_context", with_ctx)

    def poster(*args, before_submit=None, **kwargs):
        if before_submit is not None and not before_submit():
            return False, transitions.WITHDRAWN_BEFORE_SUBMIT
        published.append(args[0])
        return True, "ok"

    monkeypatch.setattr("backend.services.social_outreach.reddit_outreach.post_comment_via_servo", poster)
    monkeypatch.setattr("backend.services.social_outreach.reddit_outreach.record_post_via_backend",
                        lambda audit_id, *a, **k: transitions.mark_posted(audit_id, "hello"))

    class Outreach:
        def add(self, status="approved", platform="reddit", action="comment", url=REDDIT_THREAD):
            row = SocialOutreachLog(platform=platform, action=action, status=status, draft_text="hello",
                                    target_url=url, target_thread_id="abc")
            db.session.add(row)
            db.session.commit()
            return row.id

        def row(self, row_id):
            db.session.expire_all()
            return db.session.get(SocialOutreachLog, row_id)

    helper = Outreach()
    helper.client = client
    helper.redis = fake_redis
    helper.watching = watching
    helper.dispatched = dispatched
    helper.published = published
    return helper


# ---- starting one post ---------------------------------------------------------------

def test_post_now_starts_exactly_that_one_post(outreach):
    first = outreach.add()
    second = outreach.add()

    resp = outreach.client.post(f"/api/social-outreach/post-now/{first}")

    assert resp.status_code == 202
    assert "Agent Screen" in resp.get_json()["message"]
    assert [event_id for event_id, _ in outreach.dispatched] == [first]
    assert outreach.row(first).status == "approved"  # the worker claims it
    assert outreach.row(second).status == "approved"


def test_a_second_post_is_refused_while_one_runs(outreach):
    first, second = outreach.add(), outreach.add()
    assert outreach.client.post(f"/api/social-outreach/post-now/{first}").status_code == 202

    resp = outreach.client.post(f"/api/social-outreach/post-now/{second}")

    assert resp.status_code == 409
    assert f"draft #{first}" in resp.get_json()["error"]
    assert len(outreach.dispatched) == 1


def test_nothing_starts_without_the_agent_screen_open(outreach):
    row_id = outreach.add()
    outreach.watching["watching"] = False

    resp = outreach.client.post(f"/api/social-outreach/post-now/{row_id}")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == agent_screen_presence.NOT_WATCHING
    assert outreach.dispatched == []
    assert outreach.redis.get(watched_post.RUNNING_KEY) is None


@pytest.mark.parametrize("status", ["drafted", "posted", "rejected"])
def test_only_an_approved_draft_can_be_started(outreach, status):
    row_id = outreach.add(status=status)

    resp = outreach.client.post(f"/api/social-outreach/post-now/{row_id}")

    assert resp.status_code == 409
    assert f"'{status}'" in resp.get_json()["error"]
    assert outreach.dispatched == []


@pytest.mark.parametrize("platform, needle", [
    ("discord", "sent by the Discord bot"),
    ("x", "does not post to X"),
    ("linkedin", "does not post to LinkedIn"),
    ("internal", "Nothing here posts to 'internal'"),
])
def test_post_now_is_only_for_watched_platforms(outreach, platform, needle):
    row_id = outreach.add(platform=platform)

    resp = outreach.client.post(f"/api/social-outreach/post-now/{row_id}")

    assert resp.status_code == 409
    assert needle in resp.get_json()["error"]
    assert outreach.dispatched == []


def test_post_now_for_a_missing_draft_is_404(outreach):
    assert outreach.client.post("/api/social-outreach/post-now/999").status_code == 404


def test_a_dispatch_that_fails_frees_the_slot(outreach, monkeypatch):
    def broker_down(event_id, clicked_at):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(watched_post, "_dispatch", broker_down)
    row_id = outreach.add()

    resp = outreach.client.post(f"/api/social-outreach/post-now/{row_id}")

    assert resp.status_code == 409
    assert "could not be queued" in resp.get_json()["error"]
    assert outreach.redis.get(watched_post.RUNNING_KEY) is None


def test_nothing_starts_without_redis(outreach, monkeypatch):
    monkeypatch.setattr(kill_switch, "_get_redis", lambda: None)
    row_id = outreach.add()

    resp = outreach.client.post(f"/api/social-outreach/post-now/{row_id}")

    assert resp.status_code == 409
    assert "Redis" in resp.get_json()["error"]


def test_the_status_route_lists_what_waits_for_a_person(outreach):
    waiting = outreach.add()
    outreach.add(platform="discord")
    outreach.add(platform="x")
    outreach.add(action="reply")
    outreach.add(status="drafted")

    body = outreach.client.get("/api/social-outreach/post-now").get_json()

    assert [row["id"] for row in body["waiting"]] == [waiting]
    assert body["watching"] is True
    assert body["running"] is None


# ---- the worker side -----------------------------------------------------------------

def test_the_started_post_runs_and_frees_the_slot(outreach):
    row_id = outreach.add()
    assert outreach.client.post(f"/api/social-outreach/post-now/{row_id}").status_code == 202
    event_id, clicked_at = outreach.dispatched[0]

    result = tasks.post_watched_draft.run(event_id, clicked_at)

    assert result["posted"] is True
    assert outreach.published == [REDDIT_THREAD]
    assert outreach.row(row_id).status == "posted"
    assert outreach.redis.get(watched_post.RUNNING_KEY) is None
    last = outreach.client.get("/api/social-outreach/post-now").get_json()["last"]
    assert (last["event_id"], last["posted"]) == (row_id, True)


def test_a_post_that_starts_too_late_is_not_started(outreach):
    row_id = outreach.add()
    assert outreach.client.post(f"/api/social-outreach/post-now/{row_id}").status_code == 202

    result = tasks.post_watched_draft.run(row_id, time.time() - watched_post.START_WINDOW_S - 1)

    assert result["posted"] is False
    assert "did not start within" in result["message"]
    assert outreach.published == []
    assert outreach.row(row_id).status == "approved"
    assert outreach.redis.get(watched_post.RUNNING_KEY) is None


def test_closing_the_agent_screen_before_submit_posts_nothing(outreach, monkeypatch):
    row_id = outreach.add()

    def closes_the_screen_then_posts(*args, before_submit=None, **kwargs):
        outreach.watching["watching"] = False
        if before_submit is not None and not before_submit():
            return False, transitions.WITHDRAWN_BEFORE_SUBMIT
        outreach.published.append(args[0])
        return True, "ok"

    monkeypatch.setattr("backend.services.social_outreach.reddit_outreach.post_comment_via_servo",
                        closes_the_screen_then_posts)

    result = tasks.post_watched_draft.run(row_id, time.time())

    assert result["posted"] is False
    assert "Agent Screen was closed" in result["message"]
    assert outreach.published == []
    row = outreach.row(row_id)
    assert (row.status, row.abort_reason) == ("approved", None)  # waits for another click


def test_the_worker_rechecks_the_screen_before_driving_the_browser(outreach, monkeypatch):
    opened = []
    monkeypatch.setattr("backend.utils.agent_browser_control.agent_browser_control",
                        lambda: opened.append(1))
    row_id = outreach.add()
    outreach.watching["watching"] = False

    result = tasks.post_watched_draft.run(row_id, time.time())

    assert result["posted"] is False
    assert opened == []
    assert outreach.row(row_id).status == "approved"


def test_the_slot_is_freed_even_when_the_task_cannot_start(outreach, monkeypatch):
    row_id = outreach.add()
    assert outreach.client.post(f"/api/social-outreach/post-now/{row_id}").status_code == 202

    def no_app(*args, **kwargs):
        raise RuntimeError("app import failed")

    monkeypatch.setattr(tasks, "_with_app_context", no_app)
    with pytest.raises(RuntimeError):
        tasks.post_watched_draft.run(row_id, time.time())

    assert outreach.redis.get(watched_post.RUNNING_KEY) is None


# ---- nobody but the Discord bot claims from outside -----------------------------------

@pytest.mark.parametrize("platform, needle", [
    ("reddit", "Post now"), ("youtube", "Post now"), ("facebook", "Post now"),
    ("x", "does not post to X"),
])
def test_claim_and_submit_routes_refuse_everything_but_discord(outreach, platform, needle):
    approved = outreach.add(platform=platform)
    claimed = outreach.add(status="processing", platform=platform)

    for path in (f"/api/social-outreach/claim/{approved}", f"/api/social-outreach/submit/{claimed}"):
        resp = outreach.client.post(path)
        assert resp.status_code == 409
        assert needle in resp.get_json()["error"]
    assert outreach.row(approved).status == "approved"
    assert outreach.row(claimed).status == "processing"


def test_the_discord_bot_still_claims_its_drafts(outreach):
    row_id = outreach.add(platform="discord")

    resp = outreach.client.post(f"/api/social-outreach/claim/{row_id}")

    assert resp.status_code == 200 and resp.get_json()["status"] == "processing"


# ---- X and LinkedIn never enter or leave the queue -----------------------------------

@pytest.mark.parametrize("platform", ["x", "twitter", "Twitter", "linkedin"])
def test_approving_an_x_or_linkedin_draft_refuses_it_with_the_reason(outreach, platform):
    row_id = outreach.add(status="drafted", platform=platform)

    resp = outreach.client.post(f"/api/social-outreach/approve/{row_id}")

    assert resp.status_code == 409
    assert resp.get_json() == {"error": platforms.refusal_for(platform), "status": "unsupported"}
    row = outreach.row(row_id)
    assert (row.status, row.abort_reason) == ("unsupported", platforms.refusal_for(platform))
    assert row.draft_text == "hello"


def test_an_approved_reddit_draft_says_what_happens_next(outreach):
    row_id = outreach.add(status="drafted")

    resp = outreach.client.post(f"/api/social-outreach/approve/{row_id}")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "approved"
    assert "Post now" in resp.get_json()["next"]
    assert outreach.dispatched == []  # approving starts nothing


def test_the_approve_tool_refuses_an_x_draft_with_the_reason(outreach):
    from backend.tools import outreach_tools

    row_id = outreach.add(status="drafted", platform="twitter")

    result = outreach_tools.OutreachApproveDraftTool().execute(id=row_id)

    assert not result.success
    assert result.error == platforms.refusal_for("twitter")


@pytest.mark.parametrize("platform", ["x", "twitter", "linkedin"])
def test_a_draft_for_x_or_linkedin_cannot_be_created_or_moved_there(outreach, platform):
    created = outreach.client.post("/api/social-outreach/drafts",
                                   json={"platform": platform, "draft_text": "hi"})
    assert created.status_code == 400
    assert created.get_json()["error"] == platforms.refusal_for(platform)

    row_id = outreach.add(status="drafted")
    moved = outreach.client.patch(f"/api/social-outreach/drafts/{row_id}", json={"platform": platform})
    assert moved.status_code == 400
    assert outreach.row(row_id).platform == "reddit"


@pytest.mark.parametrize("url", [
    "https://x.com/someone/status/1",
    "https://twitter.com/someone/status/1",
    "https://www.linkedin.com/feed/update/urn:li:activity:1",
])
def test_scouting_an_x_or_linkedin_url_is_refused_before_any_fetch(outreach, monkeypatch, url):
    def no_fetch(*args, **kwargs):
        raise AssertionError("nothing may be fetched for a refused platform")

    monkeypatch.setattr("requests.get", no_fetch)
    monkeypatch.setattr("backend.services.dom_metadata_extractor.DOMMetadataExtractor.get_instance", no_fetch)

    resp = outreach.client.post("/api/social-outreach/scout-url", json={"url": url})

    assert resp.status_code == 400
    assert "does not post to" in resp.get_json()["error"]


def test_the_supervised_route_is_gone(outreach):
    resp = outreach.client.post("/api/social-outreach/supervised", json={"on": False})

    assert resp.status_code in (404, 405)


# ---- Agent Screen presence -----------------------------------------------------------

class _PresenceRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, ex=None):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)


def test_a_frame_just_served_counts_as_watching(monkeypatch):
    r = _PresenceRedis()
    monkeypatch.setattr(agent_screen_presence, "_redis", lambda: r)

    assert agent_screen_presence.watching() == (False, agent_screen_presence.NOT_WATCHING)
    agent_screen_presence.note_frame()
    assert agent_screen_presence.watching() == (True, None)


def test_a_stale_frame_does_not(monkeypatch):
    r = _PresenceRedis()
    r.values[agent_screen_presence.FRAME_KEY] = repr(time.time() - agent_screen_presence.WATCH_WINDOW_S - 1)
    monkeypatch.setattr(agent_screen_presence, "_redis", lambda: r)

    assert agent_screen_presence.watching()[0] is False


def test_presence_fails_closed_without_redis(monkeypatch):
    def down():
        raise ConnectionError("refused")

    monkeypatch.setattr(agent_screen_presence, "_redis", down)

    assert agent_screen_presence.watching() == (False, agent_screen_presence.UNKNOWN)
    agent_screen_presence.note_frame()  # a frame never fails on this


def test_the_last_outcome_is_kept_as_json(outreach):
    watched_post._remember(7, False, "no")

    assert json.loads(outreach.redis.get(watched_post.LAST_KEY))["event_id"] == 7
