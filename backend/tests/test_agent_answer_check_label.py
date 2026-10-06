"""The agent executor's facts check (AgentResult.verified) reaches the chat.

Tier 3 puts it on chat:complete, on the saved assistant row and on its return
value; the legacy agent loop puts it on the agent_result that
/api/tools/route-and-execute returns and saves. Only a boolean is carried:
None (no tool ran, nothing to check) leaves every payload as it was.

The executor is stubbed at the class the code constructs; the database is an
in-memory SQLite app. No model, GPU or network.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import LLMMessage, db
from backend.services.agent_executor import AgentResult


@pytest.fixture
def app(tmp_path):
    flask_app = Flask(__name__)
    flask_app.config.update(
        OUTPUT_DIR=str(tmp_path / "outputs"),
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(flask_app)
    from backend.api.tools_api import tools_bp
    flask_app.register_blueprint(tools_bp)
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


def _executor_returning(result):
    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def set_tool_context(self, **kwargs):
            pass

        def execute(self, *args, **kwargs):
            return result

    return FakeExecutor


def _deliberate(app, result):
    from backend.services.agent_brain import AgentBrain

    brain = AgentBrain.__new__(AgentBrain)
    brain.state = SimpleNamespace(
        health=SimpleNamespace(llm_available=True, tools_available=True),
        tool_registry=object(),
        llm=SimpleNamespace(model="test-model"),
        max_agent_iterations=5,
    )
    acs = MagicMock()
    acs.drain_thinking_steps.return_value = []
    acs.drain_recipe_usage.return_value = {}
    events = []
    with patch("backend.services.agent_executor.AgentExecutor", _executor_returning(result)), \
            patch("backend.services.agent_control_service.get_agent_control_service", return_value=acs):
        out = brain._deliberate(
            "s1", "what is in my notes?", {}, lambda name, payload: events.append((name, payload)),
            app=app, request_id="r1",
        )
    complete = [payload for name, payload in events if name == "chat:complete"]
    assert len(complete) == 1
    return out, complete[0]


@pytest.mark.parametrize("verified", [False, True])
def test_tier3_carries_the_facts_check(app, verified):
    out, complete = _deliberate(app, AgentResult(final_answer="It is on Tuesday.", verified=verified))

    assert out["verified"] is verified
    assert complete["verified"] is verified
    saved = db.session.query(LLMMessage).filter_by(role="assistant").one()
    assert saved.extra_data["verified"] is verified


def test_tier3_with_nothing_checked_adds_no_label(app):
    out, complete = _deliberate(app, AgentResult(final_answer="Hello there.", verified=None))

    assert out["verified"] is None
    assert "verified" not in complete
    saved = db.session.query(LLMMessage).filter_by(role="assistant").one()
    assert "verified" not in saved.extra_data


def test_the_legacy_agent_loop_result_carries_the_facts_check():
    from backend.services import agent_router as ar

    router = ar.AgentRouter()
    router._tool_registry = object()
    router._llm = object()
    decision = ar.RouteDecision(route_type=ar.RouteType.AGENT_LOOP)
    unchecked = AgentResult(final_answer="Your notes are in Dropbox.", verified=False)
    with patch("backend.services.agent_executor.AgentExecutor", _executor_returning(unchecked)):
        out = router._execute_generic_agent_loop(decision, "where are my notes?", {})

    assert out["type"] == "agent_result"
    assert out["verified"] is False


@pytest.mark.parametrize("verified", [False, True])
def test_route_and_execute_returns_and_saves_the_facts_check(app, monkeypatch, verified):
    from backend.services import agent_router as ar

    monkeypatch.setattr(ar, "execute_routed_message", lambda message, context=None: {
        "type": "agent_result", "final_answer": "Your notes are in Dropbox.",
        "error": None, "steps": [], "iterations": 2, "success": True, "verified": verified,
    })

    body = app.test_client().post(
        "/api/tools/route-and-execute",
        json={"message": "/agent where are my notes?", "context": {"session_id": "s1"}},
    ).get_json()

    assert body["result"]["verified"] is verified
    saved = db.session.query(LLMMessage).filter_by(role="assistant").one()
    assert saved.extra_data == {"verified": verified}


def test_route_and_execute_saves_no_label_for_other_results(app, monkeypatch):
    from backend.services import agent_router as ar

    monkeypatch.setattr(ar, "execute_routed_message", lambda message, context=None: {
        "type": "tool_result", "tool_name": "media_play", "result": {"success": True},
        "final_answer": "Playing.",
    })

    app.test_client().post(
        "/api/tools/route-and-execute",
        json={"message": "play something", "context": {"session_id": "s1"}},
    )

    saved = db.session.query(LLMMessage).filter_by(role="assistant").one()
    assert saved.extra_data is None
