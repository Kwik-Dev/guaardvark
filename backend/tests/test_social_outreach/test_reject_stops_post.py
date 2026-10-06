"""A rejected draft is never published, and a reject says when it came too late.

The real posting tick and the real /api/social-outreach routes run against the
in-memory database. Posters are stand-ins that never touch a browser: they
record "publish" where a real poster publishes. No Redis, no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.api import social_outreach_api
from backend.models import SocialOutreachLog, db
from backend.services.social_outreach import kill_switch, transitions
from backend.tasks import social_outreach_tasks as tasks
from backend.tools import outreach_tools
from backend.utils.backend_http import BackendError, BackendResponse


@pytest.fixture(autouse=True)
def _posting_needs_web_access(web_access_on):
    """These tests post; posting is gated on web access being on."""


REDDIT_THREAD = "https://www.reddit.com/r/x/comments/abc/t/"


@pytest.fixture
def outreach(app, client, monkeypatch):
    """The tick and the routes on the test app, with every outside call replaced."""
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    events: list[str] = []

    def with_ctx(fn, *args, **kwargs):
        with app.app_context():
            return fn(*args, **kwargs)

    def record_post(audit_id, permalink, thread_id, posted_text, task_id, platform="reddit"):
        resp = client.post("/api/social-outreach/record-post", json={
            "audit_id": audit_id, "platform": platform, "posted_text": posted_text,
        })
        events.append(f"record-post {resp.status_code}")

    monkeypatch.setattr(tasks, "_with_app_context", with_ctx)
    monkeypatch.setattr(kill_switch, "is_enabled", lambda: True)
    monkeypatch.setattr(kill_switch, "cadence_allows_post", lambda platform: (True, None))
    monkeypatch.setattr(kill_switch, "record_post", lambda platform: None)
    monkeypatch.setattr(
        "backend.services.social_outreach.reddit_outreach.record_post_via_backend", record_post)

    class Outreach:
        def add(self, status, platform="reddit", url=REDDIT_THREAD, abort_reason=None):
            row = SocialOutreachLog(
                platform=platform, action="comment", status=status, draft_text="hello",
                target_url=url, target_thread_id="abc", abort_reason=abort_reason,
            )
            db.session.add(row)
            db.session.commit()
            return row.id

        def row(self, row_id):
            db.session.expire_all()
            return db.session.get(SocialOutreachLog, row_id)

        def reject(self, row_id):
            return client.post(f"/api/social-outreach/reject/{row_id}")

        def poster(self, name, before_gate=None, after_gate=None):
            """Stand-in for a poster: asks the gate, then publishes."""
            def post(*args, before_submit=None, **kwargs):
                if before_gate:
                    before_gate()
                if before_submit is not None and not before_submit():
                    return False, transitions.WITHDRAWN_BEFORE_SUBMIT
                if after_gate:
                    after_gate()
                events.append(f"publish {name}")
                return True, "ok"
            return post

        def use_posters(self, reddit=None, youtube=None):
            monkeypatch.setattr(
                "backend.services.social_outreach.reddit_outreach.post_comment_via_servo",
                reddit or self.poster("reddit"))
            monkeypatch.setattr(
                "backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo",
                youtube or self.poster("youtube"))

        def tick(self):
            return tasks.tick_process_approved_drafts.run()

    helper = Outreach()
    helper.events = events
    helper.client = client
    return helper


def test_reject_while_the_poster_is_working_stops_the_post(outreach):
    row_id = outreach.add("approved")
    answers = []
    outreach.use_posters(reddit=outreach.poster(
        "reddit", before_gate=lambda: answers.append(outreach.reject(row_id))))

    result = outreach.tick()

    assert answers[0].status_code == 200
    assert answers[0].get_json()["rejected_from"] == "processing"
    assert outreach.events == []  # nothing published, nothing recorded
    assert result["processed"] == 0 and result["withdrawn"] == 1
    assert outreach.row(row_id).status == "rejected"


def test_reject_after_publishing_began_is_refused(outreach):
    row_id = outreach.add("approved")
    answers = []
    outreach.use_posters(reddit=outreach.poster(
        "reddit", after_gate=lambda: answers.append(outreach.reject(row_id))))

    outreach.tick()

    assert answers[0].status_code == 409
    assert answers[0].get_json()["status"] == "submitting"
    assert "can no longer be stopped" in answers[0].get_json()["error"]
    assert outreach.events == ["publish reddit", "record-post 200"]
    assert outreach.row(row_id).status == "posted"


def test_a_row_rejected_while_an_earlier_row_posts_is_not_claimed(outreach):
    first = outreach.add("approved")
    second = outreach.add("approved", platform="youtube", url="https://www.youtube.com/watch?v=abcdefghijk")
    outreach.use_posters(reddit=outreach.poster("reddit", before_gate=lambda: outreach.reject(second)))

    result = outreach.tick()

    assert outreach.events == ["publish reddit", "record-post 200"]
    assert result["skipped_not_approved"] == 1
    assert outreach.row(first).status == "posted"
    assert outreach.row(second).status == "rejected"


def test_an_approved_row_still_posts(outreach):
    row_id = outreach.add("approved")
    outreach.use_posters()

    result = outreach.tick()

    assert result["processed"] == 1
    assert outreach.events == ["publish reddit", "record-post 200"]
    assert outreach.row(row_id).status == "posted"


def test_a_failed_post_does_not_turn_a_rejection_into_an_abort(outreach):
    row_id = outreach.add("approved")

    def fails_after_reject(*args, before_submit=None, **kwargs):
        outreach.reject(row_id)
        return False, "navigate_failed"

    outreach.use_posters(reddit=fails_after_reject)
    outreach.tick()

    assert outreach.row(row_id).status == "rejected"


def test_record_post_does_not_overwrite_a_rejection(outreach):
    row_id = outreach.add("rejected")

    resp = outreach.client.post("/api/social-outreach/record-post", json={
        "audit_id": row_id, "platform": "discord", "posted_text": "hello",
    })

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "rejected"
    row = outreach.row(row_id)
    assert row.status == "rejected"
    assert row.posted_text == "hello"
    assert row.abort_reason == "posted_despite_rejection"


def test_submit_route_is_the_gate_for_posters_outside_the_backend(outreach):
    claimed = outreach.add("processing")
    rejected = outreach.add("rejected")

    ok = outreach.client.post(f"/api/social-outreach/submit/{claimed}")
    refused = outreach.client.post(f"/api/social-outreach/submit/{rejected}")
    again = outreach.client.post(f"/api/social-outreach/submit/{claimed}")

    assert ok.status_code == 200 and ok.get_json()["status"] == "submitting"
    assert refused.status_code == 409 and refused.get_json()["status"] == "rejected"
    assert again.status_code == 409  # a second poster cannot take the same row
    assert outreach.client.post("/api/social-outreach/submit/999999").status_code == 404


def test_approve_after_a_reject_is_refused(outreach):
    row_id = outreach.add("drafted")
    assert outreach.reject(row_id).status_code == 200

    resp = outreach.client.post(f"/api/social-outreach/approve/{row_id}")

    assert resp.status_code == 409
    assert outreach.row(row_id).status == "rejected"


def test_reject_rereads_when_the_poster_moves_the_row(outreach, monkeypatch):
    row_id = outreach.add("approved")
    real_current = transitions.current_status
    reads = []

    def current_then_poster_submits(audit_id):
        status = real_current(audit_id)
        reads.append(status)
        if len(reads) == 1:
            assert transitions.claim(audit_id)
            assert transitions.begin_submit(audit_id)
        return status

    monkeypatch.setattr(transitions, "current_status", current_then_poster_submits)

    outcome = transitions.reject(row_id)

    assert outcome == transitions.RejectOutcome(False, "submitting")
    assert outreach.row(row_id).status == "submitting"


@pytest.mark.parametrize("status, rejected", [
    ("candidate", True), ("drafted", True), ("approved", True), ("processing", True),
    ("submitting", False), ("posted", False), ("rejected", False), ("aborted", False),
])
def test_reject_tool_over_mcp_says_what_happened(outreach, monkeypatch, status, rejected):
    row_id = outreach.add(status)

    def backend(method, path, payload=None, **kwargs):
        resp = outreach.client.open(path, method=method, json=payload)
        body = resp.get_json()
        if resp.status_code >= 400:
            raise BackendError("http", body.get("error"), status=resp.status_code, body=body)
        return BackendResponse(status=resp.status_code, body=body, data=body)

    monkeypatch.setattr(outreach_tools, "request_json", backend)
    tool = outreach_tools.OutreachRejectDraftTool()
    tool.set_context({"transport": "mcp"})

    result = tool.execute(id=row_id)

    assert result.success is rejected
    if rejected:
        assert result.output["status"] == "rejected"
        assert result.output["rejected_from"] == status
        assert ("note" in result.output) == (status == "processing")
    else:
        assert "was not rejected" in result.error
        assert result.metadata["status"] == status
        assert outreach.row(row_id).status == status


def test_reaper_closes_rows_stuck_submitting(outreach):
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    stuck = outreach.add("submitting", abort_reason=f"submitting_since:{old}")
    live = outreach.add("submitting", abort_reason=f"submitting_since:{fresh}")

    tasks.tick_reap_stuck_processing.run()

    assert outreach.row(stuck).status == "aborted"
    assert "may have gone out" in outreach.row(stuck).abort_reason
    assert outreach.row(live).status == "submitting"
