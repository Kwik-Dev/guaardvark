"""The tools router never answers a chat message with its own routing dict.

When the legacy router has nothing to run it returns {"type": "chat",
"requires_llm": True}. /api/tools/route-and-execute hands that turn back to
the client (fallback_to_chat) and saves nothing; any other result with no
answer text is saved as a plain sentence rather than str(result).
/api/tools/route marks the AgentBrain preview execute_via "unified" so the
client sends those turns to unified chat in the first place.

Real blueprint through Flask's test client and an in-memory SQLite database.
The router result and the brain are stubbed: no model, GPU or network.
"""

import sys
import types

import pytest
from flask import Flask

from backend.api.tools_api import _NO_REPLY_TEXT, tools_bp
from backend.models import LLMMessage, LLMSession, db
from backend.services import agent_router
from backend.services.brain_state import BrainState


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
    flask_app.register_blueprint(tools_bp)
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _router_returns(monkeypatch, result):
    monkeypatch.setattr(
        agent_router, "execute_routed_message", lambda message, context=None: result
    )


def _execute(client, message):
    return client.post(
        "/api/tools/route-and-execute",
        json={"message": message, "context": {"session_id": "s1"}},
    )


def _brain_ready(monkeypatch, ready):
    monkeypatch.setattr(
        BrainState, "get_instance", staticmethod(lambda: types.SimpleNamespace(is_ready=ready))
    )


def test_a_chat_result_falls_back_to_chat_and_saves_nothing(client, monkeypatch):
    chat = {"type": "chat", "requires_llm": True, "message": "what is an agent?"}
    _router_returns(monkeypatch, chat)

    response = _execute(client, "what is an agent?")

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["fallback_to_chat"] is True
    assert body["result"] == chat
    assert "display_content" not in body
    assert db.session.query(LLMMessage).count() == 0
    assert db.session.query(LLMSession).count() == 0


def test_a_result_with_no_answer_text_is_saved_as_a_sentence(client, monkeypatch):
    _router_returns(monkeypatch, {
        "type": "tool_result", "tool_name": "media_play", "result": {"success": True},
    })

    body = _execute(client, "play something").get_json()

    assert "fallback_to_chat" not in body
    assert body["display_content"] == _NO_REPLY_TEXT
    saved = db.session.query(LLMMessage).filter_by(role="assistant").one()
    assert saved.content == _NO_REPLY_TEXT


def test_the_agent_brain_preview_is_marked_for_unified_chat(client, monkeypatch):
    _brain_ready(monkeypatch, True)
    stub = types.ModuleType("backend.services.agent_brain")

    class AgentBrain:
        def __init__(self, state=None):
            self.state = state

        def _is_vision_task(self, message, image_data=None):
            return False

    stub.AgentBrain = AgentBrain
    monkeypatch.setitem(sys.modules, "backend.services.agent_brain", stub)

    route = client.post("/api/tools/route", json={"message": "what is an agent?"}).get_json()["route"]

    assert route["route_type"] == "agent_loop"
    assert route["execute_via"] == "unified"


def test_a_legacy_route_carries_no_execute_via(client, monkeypatch):
    _brain_ready(monkeypatch, False)

    route = client.post("/api/tools/route", json={"message": "what is an agent?"}).get_json()["route"]

    assert route["execute_via"] is None
