"""Servo failures must preserve the draft text in the audit row.

Background: under the supervised-mode marketing flow, the user clicks
'approve' in the UI to copy a grade-0.9 draft and post manually when
vision-driven autoposting fails. That requires the audit row to keep
its draft_text after a servo abort, not get wiped or replaced by a
text-less abort row.
"""
from unittest.mock import patch
import pytest

def test_mark_draft_aborted_preserves_draft_text(app):
    """A drafted row updated to 'aborted' must keep its draft_text."""
    from backend.services.social_outreach import audit
    from backend.models import SocialOutreachLog, db
    
    with app.app_context():
        row = SocialOutreachLog(
            platform="reddit",
            action="comment",
            status="drafted",
            draft_text="If you're running local LLMs, watch your VRAM allocation early.",
            target_url="https://reddit.com/r/x/comments/abc/y",
            target_thread_id="abc",
            grade_score=0.9,
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id
        
        # This function doesn't exist yet, so this test should fail to import or call
        ok = audit.mark_draft_aborted(rid, "servo: click_save_failed: timeout")
        assert ok is True
        
        db.session.expire_all()
        updated = SocialOutreachLog.query.get(rid)
        assert updated.status == "aborted"
        assert updated.abort_reason == "servo: click_save_failed: timeout"
        # CRITICAL: draft_text preserved so the user can copy-paste it.
        assert updated.draft_text == "If you're running local LLMs, watch your VRAM allocation early."
        assert updated.grade_score == 0.9


def test_mark_draft_aborted_returns_false_for_missing_id(app):
    from backend.services.social_outreach import audit
    with app.app_context():
        # This function doesn't exist yet
        try:
            assert audit.mark_draft_aborted(999_999, "servo: x") is False
        except AttributeError:
            pytest.fail("mark_draft_aborted not implemented")


def test_run_one_pass_is_draft_only_never_servo_posts(app):
    """Legacy Reddit loop drafts only — posting is via process-approved."""
    from unittest.mock import MagicMock, patch
    from backend.services.social_outreach.reddit_outreach import RedditOutreachLoop
    from backend.models import SocialOutreachLog, db

    with app.app_context(), \
         patch("backend.services.social_outreach.reddit_outreach.fetch_subreddit_rules", return_value=[]), \
         patch("backend.services.social_outreach.reddit_outreach.fetch_hot_threads") as mock_hot, \
         patch("backend.services.social_outreach.reddit_outreach.fetch_thread_comments", return_value=[]), \
         patch("backend.services.social_outreach.reddit_outreach.thread_is_relevant", return_value="test_hint"), \
         patch("backend.services.social_outreach.reddit_outreach.draft_via_backend") as mock_draft, \
         patch("backend.services.social_outreach.reddit_outreach.post_comment_via_servo") as mock_post, \
         patch("backend.services.social_outreach.reddit_outreach.kill_switch.is_enabled", return_value=True):

        mock_thread = MagicMock()
        mock_thread.id = "thread123"
        mock_thread.permalink = "https://reddit.com/r/test/comments/thread123"
        mock_hot.return_value = [mock_thread]

        row = SocialOutreachLog(
            platform="reddit",
            action="comment",
            status="drafted",
            draft_text="Test draft text",
            target_url=mock_thread.permalink,
            target_thread_id=mock_thread.id,
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id

        mock_draft.return_value = {
            "audit_id": rid,
            "would_post": True,
            "draft": "Test draft text",
        }

        loop = RedditOutreachLoop()
        report = loop.run_one_pass("test_subreddit")

        assert report["drafted"] == 1
        assert report.get("queued_for_post") == 1
        assert mock_post.call_count == 0
        db.session.expire_all()
        assert SocialOutreachLog.query.get(rid).status == "drafted"
        assert SocialOutreachLog.query.get(rid).draft_text == "Test draft text"


def test_self_share_loop_is_draft_only_never_servo_posts(app):
    """Self-share loop drafts only — never calls servo submit."""
    from unittest.mock import patch
    from backend.services.social_outreach.self_share import SelfShareLoop
    from backend.models import SocialOutreachLog, db
    import json

    with app.app_context(), \
         patch("backend.services.social_outreach.self_share.fetch_subreddit_rules", return_value=[]), \
         patch("backend.services.social_outreach.self_share._draft_share") as mock_draft, \
         patch("backend.services.social_outreach.self_share._submit_post_via_servo") as mock_submit, \
         patch("backend.services.social_outreach.self_share.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.self_share.kill_switch.cadence_allows_post", return_value=(True, None)):

        draft_content = json.dumps({"title": "Test Title", "body": "Test Body"})
        row = SocialOutreachLog(
            platform="reddit",
            action="share",
            status="drafted",
            draft_text=draft_content,
            target_url="https://reddit.com/r/test",
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id

        mock_draft.return_value = {
            "audit_id": rid,
            "would_post": True,
            "draft": draft_content,
        }

        loop = SelfShareLoop()
        report = loop.run_one_pass("test_subreddit", "https://guaardvark.com")

        assert report["drafted"] == 1
        assert report.get("queued_for_post") == 1
        assert mock_submit.call_count == 0
        db.session.expire_all()
        updated = SocialOutreachLog.query.get(rid)
        assert updated.status == "drafted"
        assert updated.draft_text == draft_content
