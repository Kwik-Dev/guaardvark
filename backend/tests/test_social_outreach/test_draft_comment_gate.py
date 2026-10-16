"""POST /draft-comment runs the independent check and queues every draft for a person.

The real route runs against the in-memory database. The persona (drafting LLM)
and the external grader are stand-ins, outreach is on and cadence is open, and
the audit jsonl goes to a temporary directory. No Ollama, Redis or network.
"""

from __future__ import annotations

import json

import pytest

from backend.api import social_outreach_api
from backend.models import Setting, SocialOutreachLog, db
from backend.services.social_outreach import audit, external_grader, kill_switch, persona, platforms

PASSED = {"grade": 0.75, "passed": True, "checked": True, "skipped": False, "model": "g", "reason": "fine"}
FAILED = {"grade": 0.75, "passed": False, "checked": True, "skipped": False, "model": "g", "reason": "generic"}
UNCHECKED = {"grade": 0.0, "checked": False, "skipped": True, "model": None,
             "reason": "no_grader_model_loaded"}

THREAD = "TITLE: Running local models\n\nOP BODY:\nWhich GPU do I need?"


@pytest.fixture
def route(app, client, monkeypatch, tmp_path):
    """Call /draft-comment with a chosen grader result."""
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    monkeypatch.setattr(audit, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(kill_switch, "is_enabled", lambda: True)
    monkeypatch.setattr(kill_switch, "cadence_allows_post", lambda platform: (True, None))
    state = {"draft": "A 12GB card runs the 8B models fine.", "grade": 0.9,
             "ext": UNCHECKED, "graded": [], "drafted": 0}

    def draft(**kwargs):
        state["drafted"] += 1
        return {"draft": state["draft"], "grade": state["grade"], "reason": "r"}

    monkeypatch.setattr(persona, "draft_outreach_text", draft)

    def grade(draft_text, thread_context):
        state["graded"].append((draft_text, thread_context))
        return state["ext"]

    monkeypatch.setattr(external_grader, "grade_draft_externally", grade)

    def post(**body):
        body = {"platform": "reddit", "thread_context": THREAD,
                "target_url": "https://www.reddit.com/r/x/comments/abc/t/", **body}
        return client.post("/api/social-outreach/draft-comment", json=body)

    def call(**body):
        resp = post(**body)
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()

    def audit_extra():
        lines = (tmp_path / "audit.jsonl").read_text().splitlines()
        return json.loads(lines[-1])["extra"]

    call.state = state
    call.post = post
    call.audit_extra = audit_extra
    return call


def _status(audit_id):
    db.session.expire_all()
    return SocialOutreachLog.query.get(audit_id).status


def test_the_grader_sees_the_draft_and_the_thread(route):
    route()

    assert route.state["graded"] == [("A 12GB card runs the 8B models fine.", THREAD)]


def test_a_feature_hint_in_the_body_is_the_persons_requested_feature(route, monkeypatch):
    """feature_hint reaches the persona as requested_feature, the argument
    that steers the draft; a body without one leaves the angle to the persona."""
    calls = []

    def draft(**kwargs):
        calls.append(kwargs)
        return {"draft": "A 12GB card runs the 8B models fine.", "grade": 0.9, "reason": "r"}

    monkeypatch.setattr(persona, "draft_outreach_text", draft)

    route(feature_hint="rag")
    route()

    assert [call.get("requested_feature") for call in calls] == ["rag", None]
    assert all("feature_hint" not in call for call in calls)


@pytest.mark.parametrize("ext, label, reason, hold", [
    (PASSED, "passed", "passed", None),
    (UNCHECKED, "unavailable", "unchecked", None),
    (FAILED, "failed", "failed", "failed"),
])
def test_every_draft_waits_for_a_person_and_reports_the_check(route, ext, label, reason, hold):
    route.state["ext"] = ext

    body = route()

    assert body["would_post"] is False
    assert body["gates"]["supervised"] is True
    assert body["gates"]["independent_check"] == label
    assert body["gates"]["independent_reason"] == reason
    assert _status(body["audit_id"]) == "drafted"
    assert route.audit_extra()["hold_reason"] == hold


@pytest.mark.parametrize("key", ["social_outreach_supervised", "outreach_require_independent_check"])
def test_settings_from_an_older_install_do_not_approve_a_draft(route, key):
    db.session.add(Setting(key=key, value="false"))
    db.session.commit()
    route.state["ext"] = PASSED

    body = route()

    assert body["would_post"] is False
    assert _status(body["audit_id"]) == "drafted"


def test_an_empty_draft_is_not_sent_to_the_grader(route):
    route.state["draft"] = "   "

    body = route()

    assert route.state["graded"] == []
    assert body["would_post"] is False
    assert body["gates"]["independent_check"] == "unavailable"


def test_a_passed_draft_on_a_thread_the_judge_did_not_see_is_noted(route):
    route.state["ext"] = PASSED

    body = route(relevance_unchecked=True)

    assert body["gates"]["independent_check"] == "passed"
    assert body["gates"]["independent_reason"] == "relevance_unchecked"
    assert _status(body["audit_id"]) == "drafted"
    assert route.audit_extra()["relevance_unchecked"] is True


def test_a_share_is_not_graded_and_waits_for_a_person(route):
    route.state["ext"] = PASSED

    body = route(mode="share", share_target="r/SideProject", share_link="https://example.com/x",
                 thread_context=None)

    assert route.state["graded"] == []
    assert body["would_post"] is False
    assert body["gates"]["independent_reason"] == "share_not_graded"
    assert _status(body["audit_id"]) == "drafted"
    assert route.audit_extra()["external_reason"] == "share_not_graded"


@pytest.mark.parametrize("platform", ["x", "twitter", "linkedin"])
def test_a_draft_for_x_or_linkedin_is_refused_before_drafting(route, platform):
    resp = route.post(platform=platform)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == platforms.refusal_for(platform)
    assert route.state["drafted"] == 0
    assert SocialOutreachLog.query.count() == 0
