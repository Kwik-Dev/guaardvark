"""Regression: /record-post must respect the kill switch."""
from unittest.mock import patch

import pytest


def test_record_post_returns_403_when_kill_switch_off(app, client):
    """When kill_switch.is_enabled() returns False, /record-post must
    refuse to flip status='posted' and must return 403."""
    from backend.api import social_outreach_api
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    
    with patch("backend.api.social_outreach_api.kill_switch.is_enabled", return_value=False):
        resp = client.post(
            "/api/social-outreach/record-post",
            json={"audit_id": 1, "platform": "reddit", "url": "https://reddit.com/x", "text": "hi"},
        )
    assert resp.status_code == 403
    body = resp.get_json()
    assert "kill" in body.get("error", "").lower() or "disabled" in body.get("error", "").lower()


def test_approved_draft_dispatcher_skips_when_kill_switch_off(app, db_session, monkeypatch):
    """Approved rows must not be claimed or servo-posted while Outreach is disabled."""
    from backend.models import Setting, SocialOutreachLog
    from backend.tasks import social_outreach_tasks

    db_session.add(Setting(key="social_outreach_enabled", value="false"))
    row = SocialOutreachLog(
        platform="reddit",
        action="comment",
        target_url="https://reddit.com/r/test/comments/abc/example",
        target_thread_id="abc",
        draft_text="Helpful, non-spammy draft.",
        status="approved",
    )
    db_session.add(row)
    db_session.commit()

    bootstrap_called = {"n": 0}

    def _fail_bootstrap(*_args, **_kwargs):
        bootstrap_called["n"] += 1
        raise AssertionError("must not bootstrap Flask when kill switch is off")

    monkeypatch.setattr(social_outreach_tasks, "_with_app_context", _fail_bootstrap)

    with app.app_context():
        result = social_outreach_tasks.tick_process_approved_drafts.run()

    db_session.refresh(row)
    assert bootstrap_called["n"] == 0
    assert result == {"processed": 0, "reason": "kill_switch_off"}
    assert row.status == "approved"


def test_beat_ticks_skip_without_flask_bootstrap(app, db_session, monkeypatch):
    """Beat ticks must read the kill switch before importing backend.app."""
    from backend.models import Setting
    from backend.tasks import social_outreach_tasks

    db_session.add(Setting(key="social_outreach_enabled", value="false"))
    db_session.commit()

    bootstrap_called = {"n": 0}

    def _fail_bootstrap(*_args, **_kwargs):
        bootstrap_called["n"] += 1
        raise AssertionError("must not bootstrap Flask when kill switch is off")

    monkeypatch.setattr(social_outreach_tasks, "_with_app_context", _fail_bootstrap)

    ticks = (
        social_outreach_tasks.tick_reddit_outreach,
        social_outreach_tasks.tick_self_share,
        social_outreach_tasks.tick_recon_youtube_replies,
    )
    with app.app_context():
        for tick in ticks:
            result = tick.run()
            assert result.get("reason") == "kill_switch_off"
    assert bootstrap_called["n"] == 0


def test_kill_endpoint_drains_pending_outreach(monkeypatch):
    """POST /kill must flip off and drain queued outreach broker work."""
    from backend.api import social_outreach_api

    calls = []

    def fake_apply_kill_switch():
        calls.append("apply")
        return {
            "enabled": False,
            "purged": 3,
            "revoked": 1,
            "cancelled_tasks": 2,
            "revoked_tasks": 1,
            "errors": [],
        }

    monkeypatch.setattr(
        social_outreach_api.kill_switch,
        "apply_kill_switch",
        fake_apply_kill_switch,
    )

    app = __import__("flask").Flask(__name__)
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    client = app.test_client()
    resp = client.post("/api/social-outreach/kill")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["enabled"] is False
    assert body["purged"] == 3
    assert body["cancelled_tasks"] == 2
    assert calls == ["apply"]


def test_run_pass_refuses_to_queue_when_kill_switch_off(app, client, db_session):
    """The GUI/API launch path must not create Task rows while disabled."""
    from backend.api import social_outreach_api
    from backend.models import Setting, Task

    app.register_blueprint(social_outreach_api.social_outreach_bp)
    db_session.add(Setting(key="social_outreach_enabled", value="false"))
    db_session.commit()

    resp = client.post("/api/social-outreach/run-pass", json={"platform": "recon"})

    assert resp.status_code == 400
    assert "disabled" in resp.get_json().get("error", "").lower()
    assert Task.query.count() == 0


def test_run_pass_creates_task_queue_row_when_enabled(app, client, db_session, monkeypatch):
    """Enabled Outreach launches should be Task-backed for TaskPage/Activity/progress."""
    from backend.api import social_outreach_api
    from backend.models import Setting, Task
    from backend.tasks.unified_task_executor import execute_unified_task

    class DummyAsyncResult:
        id = "celery-test-id"

    app.register_blueprint(social_outreach_api.social_outreach_bp)
    db_session.add(Setting(key="social_outreach_enabled", value="true"))
    db_session.commit()
    monkeypatch.setattr(
        execute_unified_task,
        "apply_async",
        lambda *args, **kwargs: DummyAsyncResult(),
    )

    resp = client.post("/api/social-outreach/run-pass", json={"platform": "recon"})

    assert resp.status_code == 202
    body = resp.get_json()
    row = db_session.get(Task, body["task_id"])
    assert row is not None
    assert row.type == "social_outreach_recon"
    assert row.status == "queued"
    assert row.job_id == f"task_{row.id}"
    assert body["job_id"] == row.job_id
    assert body["celery_task_id"] == "celery-test-id"
