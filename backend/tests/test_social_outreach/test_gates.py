"""The independent-check gate shared by content_agent and /draft-comment.

An unsupervised draft may post only when the second-opinion grader actually
ran and passed. The setting outreach_require_independent_check (default on)
is read from the in-memory settings table, or stubbed where the table must be
unreadable.
"""

from __future__ import annotations

import pytest

from backend.models import Setting, db
from backend.services.social_outreach import gates, kill_switch

PASSED = {"grade": 0.75, "checked": True, "skipped": False}
FAILED = {"grade": 0.25, "checked": True, "skipped": False}
UNCHECKED = {"grade": 0.0, "checked": False, "skipped": True, "reason": "no_grader_model_loaded"}


def _set(key, value):
    db.session.add(Setting(key=key, value=value))
    db.session.commit()


@pytest.mark.parametrize("supervised", [True, False])
def test_a_check_that_ran_decides_on_its_grade(app, supervised):
    assert gates.independent_ok(PASSED, supervised=supervised) == (True, "passed")
    assert gates.independent_ok(FAILED, supervised=supervised) == (False, "failed")


def test_the_threshold_is_inclusive_and_unchanged(app):
    assert gates.MIN_EXTERNAL_GRADE == 0.5
    at_threshold = {"grade": 0.5, "checked": True}
    assert gates.independent_ok(at_threshold, supervised=False) == (True, "passed")


def test_unchecked_and_unsupervised_is_held(app):
    assert gates.independent_ok(UNCHECKED, supervised=False) == (False, "no_independent_check")


def test_unchecked_and_supervised_goes_to_a_person(app):
    assert gates.independent_ok(UNCHECKED, supervised=True) == (True, "human_review")


def test_unchecked_and_unsupervised_passes_when_the_check_is_switched_off(app):
    _set("outreach_require_independent_check", "false")

    assert gates.independent_ok(UNCHECKED, supervised=False) == (True, "check_not_required")


def test_switching_the_check_off_does_not_pass_a_failed_check(app):
    _set("outreach_require_independent_check", "false")

    assert gates.independent_ok(FAILED, supervised=False) == (False, "failed")


@pytest.mark.parametrize("ext", [
    {"grade": 0.9, "skipped": False},   # no checked key
    {"grade": 0.9, "checked": None},
    {},
    None,
])
def test_anything_short_of_checked_true_is_unchecked(app, ext):
    assert gates.independent_check_label(ext) == "unavailable"
    assert gates.independent_ok(ext, supervised=False) == (False, "no_independent_check")


@pytest.mark.parametrize("ext, label", [(PASSED, "passed"), (FAILED, "failed"), (UNCHECKED, "unavailable")])
def test_label(ext, label):
    assert gates.independent_check_label(ext) == label


# ---- self-shares ---------------------------------------------------------------------

@pytest.mark.parametrize("ext", [PASSED, FAILED, UNCHECKED])
def test_an_unsupervised_share_always_waits_for_a_person(app, ext):
    assert gates.independent_ok(ext, supervised=False, action="share") == (False, "share_needs_person")


def test_a_share_waits_even_when_the_check_is_switched_off(app):
    _set("outreach_require_independent_check", "false")

    assert gates.independent_ok(UNCHECKED, supervised=False, action="share") == (False, "share_needs_person")


def test_a_supervised_share_goes_to_a_person(app):
    assert gates.independent_ok(UNCHECKED, supervised=True, action="share") == (True, "human_review")


# ---- the setting ---------------------------------------------------------------------

def test_required_by_default(app):
    assert kill_switch.requires_independent_check() is True


@pytest.mark.parametrize("value, required", [
    ("false", False), ("0", False), ("off", False),
    ("true", True), ("on", True),
])
def test_required_follows_the_stored_value(app, value, required):
    _set("outreach_require_independent_check", value)

    assert kill_switch.requires_independent_check() is required


def test_required_when_the_settings_table_cannot_be_read(monkeypatch):
    def unreadable(key):
        raise RuntimeError("settings table unreachable")

    monkeypatch.setattr(kill_switch, "_lookup_setting", unreadable)

    assert kill_switch.requires_independent_check() is True
