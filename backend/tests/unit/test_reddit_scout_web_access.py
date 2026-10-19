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
    rules.assert_not_called() and hot.assert_not_called()


def test_reddit_scout_reads_with_web_access_on():
    with patch.object(recon.kill_switch, "is_enabled", return_value=True), \
            patch.object(recon.profile, "load", return_value=type("P", (), {"features": ["local_ai"]})()), \
            patch.object(recon, "web_access_block_reason", return_value=None), \
            patch.object(recon, "fetch_subreddit_rules", return_value=None) as rules:
        report = recon.RecondAgent().scout_reddit("LocalLLaMA")
    rules.assert_called_once_with("LocalLLaMA")
    assert report["reason"] == "rules_unreadable"
