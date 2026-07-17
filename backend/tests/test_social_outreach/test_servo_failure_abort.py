"""Legacy Reddit loop is draft-only — servo failure threshold no longer applies."""
from unittest.mock import patch
from backend.services.social_outreach import reddit_outreach


def test_loop_drafts_without_calling_servo():
    """would_post drafts are queued; post_comment_via_servo is never invoked."""

    fake_threads = [
        reddit_outreach.RedditThread(
            id=f"thread{i}",
            url=f"https://reddit.com/r/test/comments/{i}",
            permalink=f"/r/test/comments/{i}",
            subreddit="test",
            title=f"Test {i}",
            selftext="body",
            score=100,
            num_comments=10,
            created_utc=1234567890.0,
        )
        for i in range(5)
    ]

    with patch("backend.services.social_outreach.reddit_outreach.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.reddit_outreach.fetch_subreddit_rules", return_value=[]), \
         patch("backend.services.social_outreach.reddit_outreach.fetch_hot_threads", return_value=fake_threads), \
         patch("backend.services.social_outreach.reddit_outreach.audit.recent_thread_ids", return_value=set()), \
         patch("backend.services.social_outreach.reddit_outreach.fetch_thread_comments", return_value=["ollama comment"]), \
         patch("backend.services.social_outreach.reddit_outreach.thread_is_relevant", return_value="hint"), \
         patch("backend.services.social_outreach.reddit_outreach.draft_via_backend") as mock_draft, \
         patch("backend.services.social_outreach.reddit_outreach.post_comment_via_servo") as mock_post:

        mock_draft.return_value = {
            "would_post": True,
            "draft": "test draft",
            "audit_id": 1,
        }

        loop = reddit_outreach.RedditOutreachLoop()
        report = loop.run_one_pass("test")

        assert report["drafted"] >= 1
        assert report.get("queued_for_post", 0) >= 1
        assert report["posted"] == 0
        assert mock_post.call_count == 0
