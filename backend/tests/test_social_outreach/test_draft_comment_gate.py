"""POST /draft-comment runs the independent check and gates would_post on it.

The real route runs against the in-memory database. The persona (drafting LLM)
and the external grader are stand-ins, outreach is on and cadence is open, and
the audit jsonl goes to a temporary directory. No Ollama, Redis or network.
"""

from __future__ import annotations

import json

import pytest

from backend.api import social_outreach_api
from backend.models import Setting, SocialOutreachLog, db
from backend.services.social_outreach import audit, external_grader, kill_switch, persona

PASSED = {"grade": 0.75, "passed": True, "checked": True, "skipped": False, "model": "g", "reason": "fine"}
FAILED = {"grade": 0.75, "passed": False, "checked": True, "skipped": False, "model": "g", "reason": "generic"}
UNCHECKED = {"grade": 0.0, "checked": False, "skipped": True, "model": None,
             "reason": "no_grader_model_loaded"}

THREAD = "TITLE: Running local models\n\nOP BODY:\nWhich GPU do I need?"


@pytest.fixture
def route(app, client, monkeypatch, tmp_path):
    """Call /draft-comment with a chosen grader result and supervised mode."""
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    monkeypatch.setattr(audit, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(kill_switch, "is_enabled", lambda: True)
    monkeypatch.setattr(kill_switch, "cadence_allows_post", lambda platform: (True, None))
    state = {"draft": "A 12GB card runs the 8B models fine.", "grade": 0.9,
             "ext": UNCHECKED, "supervised": False, "graded": []}

    monkeypatch.setattr(kill_switch, "is_supervised", lambda: state["supervised"])
    monkeypatch.setattr(
        persona, "draft_outreach_text",
        lambda **kwargs: {"draft": state["draft"], "grade": state["grade"], "reason": "r"},
    )

    def grade(draft_text, thread_context):
        state["graded"].append((draft_text, thread_context))
        return state["ext"]

    monkeypatch.setattr(external_grader, "grade_draft_externally", grade)

    def call(**body):
        body = {"platform": "reddit", "thread_context": THREAD,
                "target_url": "https://www.reddit.com/r/x/comments/abc/t/", **body}
        resp = client.post("/api/social-outreach/draft-comment", json=body)
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()

    def audit_extra():
        lines = (tmp_path / "audit.jsonl").read_text().splitlines()
        return json.loads(lines[-1])["extra"]

    call.state = state
    call.audit_extra = audit_extra
    return call


def _status(audit_id):
    db.session.expire_all()
    return SocialOutreachLog.query.get(audit_id).status


def test_the_grader_sees_the_draft_and_the_thread(route):
    route()

    assert route.state["graded"] == [("A 12GB card runs the 8B models fine.", THREAD)]


def test_unsupervised_passed_check_posts(route):
    route.state["ext"] = PASSED

    body = route()

    assert body["would_post"] is True
    assert body["gates"]["independent_check"] == "passed"
    assert _status(body["audit_id"]) == "approved"
    assert route.audit_extra()["hold_reason"] is None


def test_unsupervised_unchecked_draft_is_held(route):
    body = route()

    assert body["would_post"] is False
    assert body["gates"]["independent_check"] == "unavailable"
    assert body["gates"]["independent_reason"] == "no_independent_check"
    assert _status(body["audit_id"]) == "drafted"
    assert route.audit_extra()["hold_reason"] == "no_independent_check"


def test_unsupervised_failed_check_is_held(route):
    route.state["ext"] = FAILED

    body = route()

    assert body["would_post"] is False
    assert body["gates"]["independent_check"] == "failed"
    assert _status(body["audit_id"]) == "drafted"
    assert route.audit_extra()["hold_reason"] == "failed"


def test_unchecked_draft_posts_when_the_check_is_switched_off(route):
    db.session.add(Setting(key="outreach_require_independent_check", value="false"))
    db.session.commit()

    body = route()

    assert body["would_post"] is True
    assert body["gates"]["independent_check"] == "unavailable"
    assert body["gates"]["independent_reason"] == "check_not_required"
    assert _status(body["audit_id"]) == "approved"


@pytest.mark.parametrize("ext, label", [(PASSED, "passed"), (UNCHECKED, "unavailable")])
def test_supervised_never_posts_and_reports_the_check(route, ext, label):
    route.state["supervised"] = True
    route.state["ext"] = ext

    body = route()

    assert body["would_post"] is False
    assert body["gates"]["independent_check"] == label
    assert _status(body["audit_id"]) == "drafted"


def test_an_empty_draft_is_not_sent_to_the_grader(route):
    route.state["draft"] = "   "

    body = route()

    assert route.state["graded"] == []
    assert body["would_post"] is False
    assert body["gates"]["independent_check"] == "unavailable"


def test_a_passed_draft_on_a_thread_the_judge_did_not_see_is_held(route):
    route.state["ext"] = PASSED

    body = route(relevance_unchecked=True)

    assert body["would_post"] is False
    assert body["gates"]["independent_check"] == "passed"
    assert body["gates"]["independent_reason"] == "relevance_unchecked"
    assert _status(body["audit_id"]) == "drafted"
    assert route.audit_extra()["relevance_unchecked"] is True
    assert route.audit_extra()["hold_reason"] == "relevance_unchecked"


def _share(route):
    return route(mode="share", share_target="r/SideProject", share_link="https://example.com/x",
                 thread_context=None)


def test_an_unsupervised_share_is_not_graded_and_waits_for_a_person(route):
    route.state["ext"] = PASSED

    body = _share(route)

    assert route.state["graded"] == []
    assert body["would_post"] is False
    assert body["gates"]["independent_check"] == "unavailable"
    assert body["gates"]["independent_reason"] == "share_needs_person"
    assert _status(body["audit_id"]) == "drafted"
    assert route.audit_extra()["hold_reason"] == "share_needs_person"
    assert route.audit_extra()["external_reason"] == "share_not_graded"


def test_a_share_waits_even_when_the_check_is_switched_off(route):
    db.session.add(Setting(key="outreach_require_independent_check", value="false"))
    db.session.commit()

    body = _share(route)

    assert body["would_post"] is False
    assert _status(body["audit_id"]) == "drafted"
    assert route.audit_extra()["hold_reason"] == "share_needs_person"


def test_a_supervised_share_waits_as_before_without_a_grader_call(route):
    route.state["supervised"] = True

    body = _share(route)

    assert route.state["graded"] == []
    assert body["would_post"] is False
    assert body["gates"]["independent_reason"] == "human_review"
    assert _status(body["audit_id"]) == "drafted"
    assert route.audit_extra()["hold_reason"] is None
