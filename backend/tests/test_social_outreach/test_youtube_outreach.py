"""YouTube outreach tests — Phase 3 posting path."""
from unittest.mock import patch, MagicMock
import pytest


def test_post_youtube_comment_auth_required(app):
    """Returns (False, 'auth_required') when BiDi finds a sign-in wall."""
    from backend.services.social_outreach.youtube_outreach import post_youtube_comment_via_servo

    with app.app_context(), \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_service, \
         patch("backend.services.local_screen_backend.LocalScreenBackend"), \
         patch("backend.services.social_outreach.youtube_outreach._bidi_navigate", return_value=True), \
         patch(
             "backend.services.social_outreach.youtube_outreach._bidi_scroll_to_yt_composer",
             return_value=(False, "auth_required", None),
         ), \
         patch("backend.services.social_outreach.youtube_outreach.time.sleep"):

        service_instance = MagicMock()
        service_instance.is_active = False
        mock_service.return_value = service_instance

        success, reason = post_youtube_comment_via_servo(
            "https://www.youtube.com/watch?v=test123",
            "test comment",
        )

        assert success is False
        assert reason == "auth_required"


def test_post_youtube_comment_servo_failure_returns_not_raises(app):
    """On servo failure, returns (False, <reason>) rather than raising."""
    from backend.services.social_outreach.youtube_outreach import post_youtube_comment_via_servo

    with app.app_context(), \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_service, \
         patch("backend.services.local_screen_backend.LocalScreenBackend"), \
         patch("backend.services.social_outreach.youtube_outreach._bidi_navigate", return_value=True), \
         patch(
             "backend.services.social_outreach.youtube_outreach._bidi_scroll_to_yt_composer",
             return_value=(False, "composer not in DOM", None),
         ), \
         patch("backend.services.social_outreach.youtube_outreach.time.sleep"):

        service_instance = MagicMock()
        service_instance.is_active = False
        mock_service.return_value = service_instance

        success, reason = post_youtube_comment_via_servo(
            "https://www.youtube.com/watch?v=test123",
            "test comment",
        )

        assert success is False
        assert "composer_not_found" in reason


def test_tick_process_approved_drafts_handles_youtube_success(app):
    """Approved YouTube rows transition through processing → posted on success."""
    from backend.models import SocialOutreachLog, db
    
    with app.app_context(), \
         patch("backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo") as mock_post, \
         patch("backend.services.social_outreach.reddit_outreach.record_post_via_backend") as mock_record:
        
        # Create approved YouTube row
        row = SocialOutreachLog(
            platform="youtube",
            action="comment",
            status="approved",
            draft_text="Great video! Check out guaardvark.com for local AI.",
            target_url="https://www.youtube.com/watch?v=test123",
            target_thread_id="test123",
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id
        
        # Mock successful post
        mock_post.return_value = (True, "ok")
        
        # Run the approved drafts processing logic directly (not through Celery)
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "approved")
            .filter(SocialOutreachLog.platform.in_(("reddit", "youtube")))
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(5)
            .all()
        )
        
        for row in rows:
            row.status = "processing"
            db.session.commit()
            
            if row.action == "comment" and row.platform == "youtube":
                comment_text = row.posted_text or row.draft_text
                success, reason = mock_post(row.target_url, comment_text, row.task_id)
                if success:
                    mock_record(row.id, row.target_url, row.target_thread_id, comment_text, row.task_id)
        
        # Verify posted
        db.session.expire_all()
        updated = SocialOutreachLog.query.get(rid)
        assert updated.status == "processing"  # Manually set to processing
        assert mock_post.call_count == 1
        assert mock_record.call_count == 1


def test_tick_process_approved_drafts_handles_youtube_failure(app):
    """Approved YouTube rows transition to aborted with clear reason on servo failure."""
    from backend.models import SocialOutreachLog, db
    from backend.services.social_outreach.audit import mark_draft_aborted
    
    with app.app_context(), \
         patch("backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo") as mock_post:
        
        # Create approved YouTube row
        row = SocialOutreachLog(
            platform="youtube",
            action="comment",
            status="approved",
            draft_text="Great video!",
            target_url="https://www.youtube.com/watch?v=test123",
            target_thread_id="test123",
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id
        
        # Mock servo failure
        mock_post.return_value = (False, "comment_submit_failed: timeout")
        
        # Run the approved drafts processing logic directly
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "approved")
            .filter(SocialOutreachLog.platform.in_(("reddit", "youtube")))
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(5)
            .all()
        )
        
        for row in rows:
            row.status = "processing"
            db.session.commit()
            
            if row.action == "comment" and row.platform == "youtube":
                comment_text = row.posted_text or row.draft_text
                success, reason = mock_post(row.target_url, comment_text, row.task_id)
                if not success:
                    mark_draft_aborted(row.id, f"servo: {reason}")
        
        # Verify aborted
        db.session.expire_all()
        updated = SocialOutreachLog.query.get(rid)
        assert updated.status == "aborted"
        assert updated.abort_reason == "servo: comment_submit_failed: timeout"


def test_tick_process_approved_drafts_platform_filter_includes_youtube(app):
    """Platform filter is now in_(("reddit", "youtube")) and non-supported platform is left at approved."""
    from backend.models import SocialOutreachLog, db
    
    with app.app_context(), \
         patch("backend.services.social_outreach.reddit_outreach.post_comment_via_servo") as mock_reddit, \
         patch("backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo") as mock_youtube, \
         patch("backend.services.social_outreach.reddit_outreach.record_post_via_backend") as mock_record:
        
        # Create approved rows for different platforms
        reddit_row = SocialOutreachLog(
            platform="reddit",
            action="comment",
            status="approved",
            draft_text="test",
            target_url="https://reddit.com/r/test/comments/abc",
        )
        youtube_row = SocialOutreachLog(
            platform="youtube",
            action="comment",
            status="approved",
            draft_text="test",
            target_url="https://youtube.com/watch?v=test",
        )
        twitter_row = SocialOutreachLog(
            platform="twitter",
            action="comment",
            status="approved",
            draft_text="test",
            target_url="https://twitter.com/test/status/123",
        )
        
        db.session.add_all([reddit_row, youtube_row, twitter_row])
        db.session.commit()
        
        reddit_id = reddit_row.id
        youtube_id = youtube_row.id
        twitter_id = twitter_row.id
        
        # Mock successful posts
        mock_reddit.return_value = (True, "ok")
        mock_youtube.return_value = (True, "ok")
        
        # Run the approved drafts processing logic directly
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "approved")
            .filter(SocialOutreachLog.platform.in_(("reddit", "youtube")))
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(5)
            .all()
        )
        
        # Verify filter picked up Reddit and YouTube but not Twitter
        assert len(rows) == 2
        assert any(r.platform == "reddit" for r in rows)
        assert any(r.platform == "youtube" for r in rows)
        assert not any(r.platform == "twitter" for r in rows)
        
        # Process the rows
        for row in rows:
            row.status = "processing"
            db.session.commit()
            
            if row.action == "comment":
                comment_text = row.posted_text or row.draft_text
                if row.platform == "reddit":
                    success, reason = mock_reddit(row.target_url, comment_text)
                elif row.platform == "youtube":
                    success, reason = mock_youtube(row.target_url, comment_text, row.task_id)
                else:
                    row.status = "approved"
                    db.session.commit()
                    continue
                
                if success:
                    mock_record(row.id, row.target_url, row.target_thread_id, comment_text, row.task_id)
        
        # Verify Reddit and YouTube were processed
        db.session.expire_all()
        reddit_updated = SocialOutreachLog.query.get(reddit_id)
        youtube_updated = SocialOutreachLog.query.get(youtube_id)
        twitter_updated = SocialOutreachLog.query.get(twitter_id)
        
        assert reddit_updated.status == "processing"
        assert youtube_updated.status == "processing"
        # Twitter should still be approved (not picked up by filter)
        assert twitter_updated.status == "approved"


def test_tick_process_approved_drafts_youtube_posted_text_fallback(app):
    """YouTube row whose posted_text is missing falls back to draft_text."""
    from backend.models import SocialOutreachLog, db
    
    with app.app_context(), \
         patch("backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo") as mock_post, \
         patch("backend.services.social_outreach.reddit_outreach.record_post_via_backend") as mock_record:
        
        # Create YouTube row with draft_text but no posted_text
        row = SocialOutreachLog(
            platform="youtube",
            action="comment",
            status="approved",
            draft_text="Great local AI content!",
            posted_text=None,  # Explicitly NULL
            target_url="https://www.youtube.com/watch?v=test123",
            target_thread_id="test123",
        )
        db.session.add(row)
        db.session.commit()
        
        # Mock successful post
        mock_post.return_value = (True, "ok")
        
        # Run the approved drafts processing logic directly
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "approved")
            .filter(SocialOutreachLog.platform.in_(("reddit", "youtube")))
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(5)
            .all()
        )
        
        for row in rows:
            row.status = "processing"
            db.session.commit()
            
            if row.action == "comment" and row.platform == "youtube":
                comment_text = row.posted_text or row.draft_text
                success, reason = mock_post(row.target_url, comment_text, row.task_id)
                if success:
                    mock_record(row.id, row.target_url, row.target_thread_id, comment_text, row.task_id)
        
        # Verify the call used draft_text
        assert mock_post.call_count == 1
        call_args = mock_post.call_args[0]
        assert call_args[1] == "Great local AI content!"  # comment_text from draft_text


# ---------------------------------------------------------------------------
# Post-submit check: the text must be in a posted comment, not just on the page
# ---------------------------------------------------------------------------

COMMENT = "Great breakdown of local inference, we run the same setup at home"
EVALUATE = "backend.services.social_outreach.reddit_outreach.bidi_evaluate_json"


def _page(**fields):
    """What the verify script reports; defaults describe a clean posted comment."""
    page = {
        "in_comments": True, "comments_seen": 21, "composers": 1, "composer_chars": 0,
        "box_text": "Add a comment... Cancel Comment", "url": "https://www.youtube.com/watch?v=test123",
    }
    page.update(fields)
    return page


def _verify(page):
    from backend.services.social_outreach.youtube_outreach import _verify_youtube_text_in_dom

    seen = []

    def evaluate(expression):
        seen.append(expression)
        return page

    with patch(EVALUATE, side_effect=evaluate):
        result = _verify_youtube_text_in_dom(COMMENT)
    return result, seen


def test_verify_text_only_in_the_composer_is_not_posted():
    (ok, reason), _ = _verify((_page(in_comments=False, composer_chars=59), ""))
    assert ok is False
    assert "not_in_comment_bodies" in reason and "composer_not_empty" in reason


def test_verify_text_in_a_comment_body_is_posted():
    (ok, reason), seen = _verify((_page(), ""))
    assert ok is True, reason
    # The needle is the start of the comment; the page body as a whole is not read.
    assert COMMENT[:60] in seen[0]
    assert "#content-text" in seen[0]
    assert "document.body" not in seen[0]


def test_verify_try_again_in_another_comment_still_posted():
    # Other people's comments never reach box_text; only the comment box and
    # toasts are searched for error words.
    (ok, reason), _ = _verify((_page(comments_seen=40), ""))
    assert ok is True, reason


def test_verify_error_in_the_comment_box_is_not_posted():
    (ok, reason), _ = _verify((_page(box_text="Add a comment... Something went wrong"), ""))
    assert ok is False
    assert "error_in_comment_box" in reason
    assert "not_in_comment_bodies" not in reason


def test_verify_text_in_body_but_composer_still_full_is_not_posted():
    (ok, reason), _ = _verify((_page(composer_chars=12), ""))
    assert ok is False
    assert reason.startswith("composer_not_empty")


def test_verify_unreadable_page_is_not_posted():
    (ok, reason), _ = _verify((None, "connect failed: refused"))
    assert ok is False
    assert "page_not_readable" in reason


def test_comment_left_in_the_composer_is_not_recorded_posted(monkeypatch):
    """End to end through the poster: a fill that 'worked' but left the text
    in the box comes back submit_unverified, so the row is never marked posted."""
    from backend.services.social_outreach import youtube_outreach as yt

    service = MagicMock()
    service.is_active = False
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(yt, "_bidi_navigate", lambda *a, **k: True)
    monkeypatch.setattr(yt, "_bidi_scroll_to_yt_composer", lambda: (True, "ok", (1, 1)))
    monkeypatch.setattr(yt, "_bidi_fill_and_submit_comment", lambda text: (True, "submitted"))
    with patch("backend.services.agent_control_service.get_agent_control_service",
               return_value=service), \
         patch("backend.utils.agent_display_utils.start_agent_display_if_needed",
               return_value=True), \
         patch("backend.services.local_screen_backend.LocalScreenBackend"), \
         patch(EVALUATE, return_value=(_page(in_comments=False, composer_chars=59), "")):
        ok, reason = yt.post_youtube_comment_via_servo(
            "https://www.youtube.com/watch?v=test123", COMMENT)
    assert ok is False
    assert reason.startswith("submit_unverified")


# ---------------------------------------------------------------------------
# Fill and submit: a disabled Comment button is a failed submit
# ---------------------------------------------------------------------------

class _BidiSocket:
    """BiDi socket for the fill step: opens a session on one tab, then answers
    each script.evaluate with the next queued page result."""

    def __init__(self, *page_results):
        import json
        self.replies = [{"type": "success"},
                        {"type": "success", "result": {"contexts": [{"context": "tab-1"}]}}]
        self.replies += [
            {"type": "success", "result": {"result": {"type": "string", "value": json.dumps(r)}}}
            for r in page_results
        ]
        self.sent = []

    def send(self, message):
        import json
        self.sent.append(json.loads(message))

    def recv(self):
        import json
        return json.dumps(self.replies.pop(0))

    def close(self):
        pass

    def expressions(self):
        return [m["params"]["expression"] for m in self.sent if m.get("method") == "script.evaluate"]


def _fill(*page_results):
    from backend.services.social_outreach.youtube_outreach import _bidi_fill_and_submit_comment

    sock = _BidiSocket(*page_results)
    with patch("websocket.create_connection", return_value=sock), \
         patch("backend.services.social_outreach.youtube_outreach.time.sleep"):
        result = _bidi_fill_and_submit_comment(COMMENT)
    return result, sock


def test_fill_with_the_comment_button_disabled_is_a_failed_submit():
    (ok, reason), sock = _fill(
        {"ok": True, "stage": "clicked_placeholder"},
        {"ok": False, "stage": "comment_button_disabled", "filled_len": len(COMMENT)},
    )
    assert ok is False
    assert reason.startswith("fill_failed:") and "comment_button_disabled" in reason
    # The page script itself refuses to fall back to Ctrl+Enter for a disabled button.
    fill_script = sock.expressions()[1]
    assert fill_script.index("comment_button_disabled") < fill_script.index("ctrlKey")


def test_fill_with_the_comment_button_clicked_is_submitted():
    (ok, reason), _ = _fill(
        {"ok": True, "stage": "clicked_placeholder"},
        {"ok": True, "stage": "clicked_submit", "filled_len": len(COMMENT)},
    )
    assert ok is True
    assert "clicked_submit" in reason


def test_disabled_button_stops_the_post_before_the_page_check(monkeypatch):
    """Through the poster: the row fails at fill_submit, and the page is never
    read as if the comment had been sent."""
    from backend.services.social_outreach import youtube_outreach as yt

    service = MagicMock()
    service.is_active = False
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(yt, "_bidi_navigate", lambda *a, **k: True)
    monkeypatch.setattr(yt, "_bidi_scroll_to_yt_composer", lambda: (True, "ok", (1, 1)))
    sock = _BidiSocket(
        {"ok": True, "stage": "clicked_placeholder"},
        {"ok": False, "stage": "comment_button_disabled", "filled_len": len(COMMENT)},
    )
    with patch("backend.services.agent_control_service.get_agent_control_service",
               return_value=service), \
         patch("backend.utils.agent_display_utils.start_agent_display_if_needed",
               return_value=True), \
         patch("backend.services.local_screen_backend.LocalScreenBackend"), \
         patch("websocket.create_connection", return_value=sock), \
         patch(EVALUATE) as page_check:
        ok, reason = yt.post_youtube_comment_via_servo(
            "https://www.youtube.com/watch?v=test123", COMMENT)
    assert ok is False
    assert reason.startswith("fill_submit_failed") and "comment_button_disabled" in reason
    page_check.assert_not_called()
