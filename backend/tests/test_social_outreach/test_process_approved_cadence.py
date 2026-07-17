"""Cadence + status guards for process-approved and approve/reject APIs."""
from unittest.mock import patch


def _use_test_app_context(monkeypatch, app):
    """Celery ticks call backend.app — bind them to the pytest app/db instead."""
    from backend.tasks import social_outreach_tasks

    def _ctx(fn, *args, **kwargs):
        with app.app_context():
            return fn(*args, **kwargs)

    monkeypatch.setattr(social_outreach_tasks, "_with_app_context", _ctx)


def test_tick_process_approved_respects_cadence(app, monkeypatch):
    from backend.models import SocialOutreachLog, db
    from backend.tasks.social_outreach_tasks import tick_process_approved_drafts

    _use_test_app_context(monkeypatch, app)

    with app.app_context(), \
         patch("backend.services.social_outreach.kill_switch.is_enabled", return_value=True), \
         patch(
             "backend.services.social_outreach.kill_switch.cadence_allows_post",
             return_value=(False, "daily cap hit"),
         ), \
         patch(
             "backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo"
         ) as mock_post:

        row = SocialOutreachLog(
            platform="youtube",
            action="comment",
            status="approved",
            draft_text="hello",
            target_url="https://www.youtube.com/watch?v=test123",
            target_thread_id="test123",
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id

        result = tick_process_approved_drafts.run()
        assert result.get("processed") == 0
        assert result.get("skipped_cadence", 0) >= 1
        assert mock_post.call_count == 0
        db.session.expire_all()
        assert SocialOutreachLog.query.get(rid).status == "approved"


def test_tick_process_approved_one_post_per_platform(app, monkeypatch):
    from backend.models import SocialOutreachLog, db
    from backend.tasks.social_outreach_tasks import tick_process_approved_drafts

    _use_test_app_context(monkeypatch, app)

    with app.app_context(), \
         patch("backend.services.social_outreach.kill_switch.is_enabled", return_value=True), \
         patch(
             "backend.services.social_outreach.kill_switch.cadence_allows_post",
             return_value=(True, None),
         ), \
         patch(
             "backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo",
             return_value=(True, "ok"),
         ), \
         patch(
             "backend.services.social_outreach.reddit_outreach.record_post_via_backend"
         ) as mock_record:

        for i in range(3):
            db.session.add(SocialOutreachLog(
                platform="youtube",
                action="comment",
                status="approved",
                draft_text=f"draft {i}",
                target_url=f"https://www.youtube.com/watch?v=vid{i}",
                target_thread_id=f"vid{i}",
            ))
        db.session.commit()

        result = tick_process_approved_drafts.run()
        assert result.get("processed") == 1
        assert mock_record.call_count == 1
        remaining = SocialOutreachLog.query.filter_by(
            platform="youtube", status="approved"
        ).count()
        assert remaining == 2


def test_approve_only_from_drafted(app, client):
    from backend.api import social_outreach_api
    from backend.models import SocialOutreachLog, db

    app.register_blueprint(social_outreach_api.social_outreach_bp)

    with app.app_context():
        posted = SocialOutreachLog(
            platform="reddit",
            action="comment",
            status="posted",
            draft_text="already out",
        )
        drafted = SocialOutreachLog(
            platform="reddit",
            action="comment",
            status="drafted",
            draft_text="pending",
        )
        db.session.add_all([posted, drafted])
        db.session.commit()
        posted_id, drafted_id = posted.id, drafted.id

    r = client.post(f"/api/social-outreach/approve/{posted_id}")
    assert r.status_code == 409

    r = client.post(f"/api/social-outreach/approve/{drafted_id}")
    assert r.status_code == 200
    assert r.get_json()["status"] == "approved"


def test_reject_illegal_status_409(app, client):
    from backend.api import social_outreach_api
    from backend.models import SocialOutreachLog, db

    app.register_blueprint(social_outreach_api.social_outreach_bp)

    with app.app_context():
        row = SocialOutreachLog(
            platform="reddit",
            action="comment",
            status="posted",
            draft_text="done",
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id

    r = client.post(f"/api/social-outreach/reject/{rid}")
    assert r.status_code == 409


def test_claim_approved_to_processing(app, client):
    from backend.api import social_outreach_api
    from backend.models import SocialOutreachLog, db

    app.register_blueprint(social_outreach_api.social_outreach_bp)

    with app.app_context():
        row = SocialOutreachLog(
            platform="discord",
            action="comment",
            status="approved",
            draft_text="hi",
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id

    r = client.post(f"/api/social-outreach/claim/{rid}")
    assert r.status_code == 200
    assert r.get_json()["status"] == "processing"

    r2 = client.post(f"/api/social-outreach/claim/{rid}")
    assert r2.status_code == 409
