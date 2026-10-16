"""The independent check reports on a draft; it never lets one post.

Every draft waits for a person's approval. These cover what the check's
answer says to that person, the per-platform posting table, and that the old
settings that could switch the wait off are no longer read.
"""

from __future__ import annotations

import pytest

from backend.models import Setting, db
from backend.services.social_outreach import gates, kill_switch, platforms

PASSED = {"grade": 0.75, "passed": True, "checked": True, "skipped": False}
FAILED = {"grade": 0.25, "passed": False, "checked": True, "skipped": False}
UNCHECKED = {"grade": 0.0, "checked": False, "skipped": True, "reason": "no_grader_model_loaded"}


def _set(key, value):
    db.session.add(Setting(key=key, value=value))
    db.session.commit()


def test_a_check_that_ran_reports_its_answer(app):
    assert gates.independent_ok(PASSED) == (True, "passed")
    assert gates.independent_ok(FAILED) == (False, "failed")


@pytest.mark.parametrize("ext", [
    {"grade": 0.75, "passed": False, "checked": True},   # the grade does not pass a draft
    {"grade": 1.0, "checked": True},                      # no passed answer is not a pass
    {"grade": 1.0, "passed": "yes", "checked": True},
])
def test_only_passed_true_passes(app, ext):
    assert gates.independent_check_label(ext) == "failed"
    assert gates.independent_ok(ext) == (False, "failed")


def test_passed_decides_whatever_the_grade(app):
    assert gates.independent_check_label({"grade": 0.0, "passed": True, "checked": True}) == "passed"


@pytest.mark.parametrize("ext", [
    UNCHECKED,
    {"grade": 0.9, "skipped": False},   # no checked key
    {"grade": 0.9, "checked": None},
    {},
    None,
])
def test_anything_short_of_checked_true_is_unchecked(app, ext):
    assert gates.independent_check_label(ext) == "unavailable"
    assert gates.independent_ok(ext) == (True, "unchecked")


@pytest.mark.parametrize("ext, label", [(PASSED, "passed"), (FAILED, "failed"), (UNCHECKED, "unavailable")])
def test_label(ext, label):
    assert gates.independent_check_label(ext) == label


def test_an_unjudged_thread_is_noted(app):
    assert gates.independent_ok(PASSED, relevance_unchecked=True) == (True, "relevance_unchecked")


def test_an_unjudged_thread_does_not_rescue_a_failed_draft(app):
    assert gates.independent_ok(FAILED, relevance_unchecked=True) == (False, "failed")


@pytest.mark.parametrize("ext", [PASSED, FAILED, UNCHECKED])
def test_a_share_is_never_graded(app, ext):
    assert gates.independent_ok(ext, action="share") == (True, "share_not_graded")


@pytest.mark.parametrize("key", ["social_outreach_supervised", "outreach_require_independent_check"])
def test_the_old_switches_are_not_read(app, key):
    """A setting left by an older install cannot switch the wait off."""
    _set(key, "false")

    assert gates.independent_ok(UNCHECKED) == (True, "unchecked")
    assert kill_switch.status_snapshot()["supervised"] is True
    assert not hasattr(kill_switch, "is_supervised")
    assert not hasattr(kill_switch, "set_supervised")
    assert not hasattr(kill_switch, "requires_independent_check")


# ---- the per-platform posting table ----------------------------------------------------

def test_browser_platforms_are_watched_only():
    assert set(platforms.platforms_in(platforms.WATCHED)) == {"reddit", "youtube", "facebook"}


def test_discord_is_the_only_bot_platform():
    assert platforms.platforms_in(platforms.BOT) == ("discord",)


@pytest.mark.parametrize("platform", ["x", "twitter", "X", "linkedin"])
def test_x_and_linkedin_are_refused_with_a_reason(platform):
    policy = platforms.policy_for(platform)
    assert policy.mode == platforms.REFUSED
    assert policy.actions == ()
    assert platforms.refusal_for(platform) == policy.refusal and policy.refusal


@pytest.mark.parametrize("url, refused", [
    ("https://x.com/someone/status/1", True),
    ("https://mobile.twitter.com/someone/status/1", True),
    ("https://www.linkedin.com/feed/update/1", True),
    ("https://www.reddit.com/r/x/comments/abc/t/", False),
    ("https://notx.com/a", False),
    ("", False),
])
def test_urls_on_refused_platforms(url, refused):
    assert bool(platforms.refusal_for_url(url)) is refused


@pytest.mark.parametrize("platform, action, posted", [
    ("reddit", "comment", True), ("Reddit", "share", True), ("reddit", "reply", False),
    ("youtube", "reply", True), ("youtube", "share", False), ("facebook", "comment", True),
    ("discord", "comment", True), ("x", "comment", False), ("internal", "comment", False),
])
def test_posts(platform, action, posted):
    assert platforms.posts(platform, action) is posted


def test_status_reports_the_table_and_no_refused_cadence(app, monkeypatch):
    monkeypatch.setattr(kill_switch, "_get_redis", lambda: None)

    snapshot = kill_switch.status_snapshot()

    assert snapshot["posting_modes"]["reddit"]["mode"] == platforms.WATCHED
    assert snapshot["posting_modes"]["x"]["mode"] == platforms.REFUSED
    assert set(snapshot["cadence"]) == {"reddit", "youtube", "facebook", "discord"}
