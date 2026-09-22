"""A thumb names one reply, is stored once, and can be withdrawn."""
import json
import os
import sys

import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask
from backend.models import db, LLMMessage, ToolFeedback
from backend.api.agent_control_api import agent_control_bp


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(agent_control_bp)
    with app.app_context(), patch("backend.config.GUAARDVARK_ROOT", str(tmp_path)):
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _turn(session="s1", content="The capital of France is Paris.", request_id="req-1", prov=None):
    u = LLMMessage(session_id=session, role="user", content="capital of france?")
    db.session.add(u)
    db.session.commit()
    a = LLMMessage(session_id=session, role="assistant", content=content,
                   extra_data={"provenance": prov or {"request_id": request_id, "tier": 2, "memory_ids": ["m1"]}})
    db.session.add(a)
    db.session.commit()
    return u, a


def _log_lines(tmp_path):
    p = tmp_path / "data" / "training" / "knowledge" / "feedback.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def test_resolves_by_message_id(client, tmp_path):
    _, a = _turn()
    r = client.post("/api/agent-control/feedback", json={"verdict": "up", "message_id": a.id, "session_id": "s1"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["resolved_by"] == "message_id"
    fb = ToolFeedback.query.one()
    assert fb.message_id == a.id and fb.verdict == "up" and fb.positive is True and fb.kind == "response"
    assert fb.provenance["memory_ids"] == ["m1"] and fb.request_id == "req-1"
    assert db.session.get(LLMMessage, a.id).extra_data["feedback"] == "up"
    assert db.session.get(LLMMessage, a.id).extra_data["feedback_id"] == fb.id
    lines = _log_lines(tmp_path)
    assert len(lines) == 1 and lines[0]["event"] == "set" and lines[0]["message_id"] == a.id


def test_resolves_by_request_id_before_the_row_id_is_known(client):
    _, a = _turn(request_id="req-live")
    r = client.post("/api/agent-control/feedback", json={"verdict": "down", "request_id": "req-live", "session_id": "s1"})
    assert r.get_json()["resolved_by"] == "request_id"
    assert ToolFeedback.query.one().message_id == a.id


def test_resolves_by_prefix_as_a_last_resort_and_none_when_nothing_matches(client):
    _, a = _turn(content="Sure, here is a 100% safe_answer with wildcards")
    r = client.post("/api/agent-control/feedback", json={"positive": True, "task": "Sure, here is a 100% safe_answer", "session_id": "s1"})
    assert r.get_json()["resolved_by"] == "prefix"
    assert ToolFeedback.query.one().message_id == a.id
    r = client.post("/api/agent-control/feedback", json={"positive": False, "task": "never said this", "session_id": "s1"})
    assert r.status_code == 201 and r.get_json()["resolved_by"] == "none"
    assert ToolFeedback.query.count() == 2


def test_a_rethumb_updates_the_same_row(client, tmp_path):
    _, a = _turn()
    client.post("/api/agent-control/feedback", json={"verdict": "up", "message_id": a.id, "session_id": "s1"})
    client.post("/api/agent-control/feedback", json={"verdict": "down", "message_id": a.id, "session_id": "s1",
                                                    "why_text": "too long", "why_tags": ["Too_Long", "verbose"]})
    assert ToolFeedback.query.count() == 1
    fb = ToolFeedback.query.one()
    assert fb.verdict == "down" and fb.positive is False and fb.retracted_at is None
    assert fb.why_text == "too long" and fb.why_tags == ["too_long", "verbose"]
    assert fb.updated_at is not None
    assert db.session.get(LLMMessage, a.id).extra_data["feedback"] == "down"
    assert [l["event"] for l in _log_lines(tmp_path)] == ["set", "set"]


def test_an_unthumb_retracts_and_clears_the_stamp(client, tmp_path):
    _, a = _turn()
    client.post("/api/agent-control/feedback", json={"verdict": "up", "message_id": a.id, "session_id": "s1"})
    r = client.post("/api/agent-control/feedback", json={"verdict": "none", "message_id": a.id, "session_id": "s1"})
    body = r.get_json()
    assert body["taught"][0]["kind"] == "retracted"
    fb = ToolFeedback.query.one()
    assert fb.verdict == "none" and fb.retracted_at is not None
    assert "feedback" not in (db.session.get(LLMMessage, a.id).extra_data or {})
    assert [l["event"] for l in _log_lines(tmp_path)] == ["set", "retract"]


def test_a_tool_card_thumb_stamps_the_assistant_row(client):
    u, a = _turn()
    r = client.post("/api/agent-control/feedback", json={
        "verdict": "up", "kind": "tool:agent_task_execute@0.0", "type": "tool_action",
        "message_id": a.id, "session_id": "s1", "tool_name": "agent_task_execute", "task": "open youtube"})
    assert r.status_code == 201
    fb = ToolFeedback.query.one()
    assert fb.kind == "tool:agent_task_execute@0.0" and fb.tool_name == "agent_task_execute"
    assert (db.session.get(LLMMessage, a.id).extra_data or {}).get("feedback") == "up"
    assert not (db.session.get(LLMMessage, u.id).extra_data or {})


def test_a_reply_and_its_tool_card_are_two_rows(client):
    _, a = _turn()
    client.post("/api/agent-control/feedback", json={"verdict": "up", "message_id": a.id, "session_id": "s1"})
    client.post("/api/agent-control/feedback", json={"verdict": "down", "kind": "tool:web_search@0.0", "message_id": a.id, "session_id": "s1"})
    assert ToolFeedback.query.count() == 2


def test_bad_verdict_is_refused(client):
    r = client.post("/api/agent-control/feedback", json={"verdict": "meh"})
    assert r.status_code == 400
