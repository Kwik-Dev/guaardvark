"""The stop on all public posting holds outreach drafts instead of dropping them.

Runs against the in-memory database. No Redis, no Celery, no browser: the
broker drain is replaced, and posting is exercised only up to the gates a
poster must pass before it publishes.
"""

from __future__ import annotations

import pytest

from backend.api import social_outreach_api
from backend.models import Setting, SocialOutreachLog, db
from backend.services.social_outreach import kill_switch, transitions


@pytest.fixture
def no_broker(monkeypatch):
    monkeypatch.setattr(
        kill_switch,
        "drain_pending_outreach_tasks",
        lambda: {"purged": 0, "revoked": 0, "errors": []},
    )


def _setting(key, value):
    db.session.add(Setting(key=key, value=value))
    db.session.commit()


def _row(status, abort_reason=None):
    row = SocialOutreachLog(
        platform="reddit", action="comment", status=status, draft_text="hello",
        target_url="https://www.reddit.com/r/x/comments/abc/t/", target_thread_id="abc",
        abort_reason=abort_reason,
    )
    db.session.add(row)
    db.session.commit()
    return row.id


def _status(row_id):
    db.session.expire_all()
    return db.session.get(SocialOutreachLog, row_id)


def test_claim_is_refused_and_the_draft_stays_approved(app):
    _setting(kill_switch.POSTING_STOP_KEY, "true")
    row_id = _row("approved")

    assert transitions.claim(row_id) is False
    assert _status(row_id).status == "approved"


def test_a_claimed_draft_is_handed_back_at_the_last_gate(app):
    """A poster mid-flight when the stop lands must not publish, and the draft waits."""
    row_id = _row("processing", abort_reason="processing_since:2026-10-06T00:00:00+00:00")
    _setting(kill_switch.POSTING_STOP_KEY, "true")

    assert transitions.begin_submit(row_id) is False
    row = _status(row_id)
    assert (row.status, row.abort_reason) == ("approved", None)
    # The poster's give-up after a refused submit finds nothing in flight.
    assert transitions.abort_in_flight(row_id, "servo: withdrawn_before_submit") is False
    assert _status(row_id).status == "approved"


def test_posting_goes_ahead_when_the_stop_is_off(app):
    row_id = _row("approved")
    assert transitions.claim(row_id) is True
    assert transitions.begin_submit(row_id) is True
    assert _status(row_id).status == "submitting"


def test_the_posting_tick_skips_while_stopped(app, monkeypatch):
    from backend.tasks import social_outreach_tasks

    _setting("social_outreach_enabled", "true")
    _setting(kill_switch.POSTING_STOP_KEY, "true")
    row_id = _row("approved")

    def _fail_bootstrap(*_args, **_kwargs):
        raise AssertionError("must not bootstrap Flask while posting is stopped")

    monkeypatch.setattr(social_outreach_tasks, "_with_app_context", _fail_bootstrap)

    result = social_outreach_tasks.tick_process_approved_drafts.run()

    assert result == {"processed": 0, "reason": "kill_switch_off"}
    assert _status(row_id).status == "approved"


def test_claim_route_names_the_stop(app, client):
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    _setting(kill_switch.POSTING_STOP_KEY, "true")
    row_id = _row("approved")

    resp = client.post(f"/api/social-outreach/claim/{row_id}")

    assert resp.status_code == 409
    assert resp.get_json() == {"error": kill_switch.POSTING_STOPPED_REASON, "status": "approved"}


def test_stop_and_resume_routes(app, client, no_broker, monkeypatch):
    monkeypatch.setattr(kill_switch, "cadence_status", lambda: {})
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    _setting("social_outreach_enabled", "true")

    stopped = client.post("/api/social-outreach/stop-posting")
    assert stopped.status_code == 200
    assert stopped.get_json()["posting_stopped"] is True

    status = client.get("/api/social-outreach/status").get_json()
    # The outreach switch keeps its own value; the stop is reported beside it.
    assert (status["enabled"], status["posting_stopped"]) == (True, True)
    assert kill_switch.is_enabled() is False

    resumed = client.post("/api/social-outreach/resume-posting")
    assert resumed.status_code == 200
    assert resumed.get_json() == {"posting_stopped": False}
    assert kill_switch.is_enabled() is True


def test_a_stop_that_does_not_save_is_reported(app, client, no_broker, monkeypatch):
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    monkeypatch.setattr(kill_switch, "_write_setting", lambda key, value: None)

    resp = client.post("/api/social-outreach/stop-posting")

    assert resp.status_code == 500
    assert resp.get_json()["posting_stopped"] is False
