"""The Reddit scout is an outside request: with web access off (Settings,
off by default) it reads nothing, like the YouTube scout and posting."""
from unittest.mock import patch

from backend.services.social_outreach import recon


def test_reddit_scout_reads_nothing_with_web_access_off():
    with patch.object(recon.kill_switch, "is_enabled", return_value=True), \
            patch.object(recon.profile, "load", return_value=type("P", (), {"features": ["local_ai"]})()), \
            patch.object(recon, "web_access_block_reason",
                         return_value="Web access is disabled. Enable it in Settings to scout Reddit for outreach.") as gate, \
            patch.object(recon, "fetch_subreddit_rules") as rules, \
            patch.object(recon, "fetch_hot_threads") as hot:
        report = recon.RecondAgent().scout_reddit("LocalLLaMA")
    assert report["reason"] == "web_access_off" and report["candidates"] == 0
    gate.assert_called_once_with("scout Reddit for outreach")
    rules.assert_not_called()
    hot.assert_not_called()


def test_reddit_scout_reads_with_web_access_on():
    with patch.object(recon.kill_switch, "is_enabled", return_value=True), \
            patch.object(recon.profile, "load", return_value=type("P", (), {"features": ["local_ai"]})()), \
            patch.object(recon, "web_access_block_reason", return_value=None), \
            patch.object(recon, "fetch_subreddit_rules", return_value=None) as rules:
        report = recon.RecondAgent().scout_reddit("LocalLLaMA")
    rules.assert_called_once_with("LocalLLaMA")
    assert report["reason"] == "rules_unreadable"


def test_the_beat_driven_loops_read_nothing_with_web_access_off():
    """tick_reddit_outreach and tick_self_share (celery beat) run these passes."""
    from backend.services.social_outreach import reddit_outreach, self_share

    off = "Web access is disabled. Enable it in Settings to scout Reddit for outreach."
    with patch.object(reddit_outreach.kill_switch, "is_enabled", return_value=True), \
            patch.object(reddit_outreach, "web_access_block_reason", return_value=off), \
            patch.object(reddit_outreach.audit, "log_outreach_event") as logged, \
            patch.object(reddit_outreach, "fetch_subreddit_rules") as rules, \
            patch.object(self_share, "fetch_subreddit_rules") as share_rules:
        loop = reddit_outreach.RedditOutreachLoop().run_one_pass("LocalLLaMA")
        share = self_share.SelfShareLoop().run_one_pass("LocalLLaMA", "https://example.org")
    assert loop["reason"] == share["reason"] == "web_access_off"
    rules.assert_not_called()
    share_rules.assert_not_called()
    assert [c.kwargs["abort_reason"] for c in logged.call_args_list] == ["web_access_off"] * 2
