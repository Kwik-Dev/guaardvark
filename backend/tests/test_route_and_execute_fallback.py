"""The tools router never answers a chat message with its own routing dict.

When the legacy router has nothing to run it returns {"type": "chat",
"requires_llm": True}. /api/tools/route-and-execute hands that turn back to
the client (fallback_to_chat) and saves nothing; any other result with no
answer text is saved as a plain sentence rather than str(result).

Real blueprint through Flask's test client and an in-memory SQLite database.
The router result is stubbed: no model, GPU or network.
"""

import pytest
from flask import Flask

from backend.api.tools_api import _NO_REPLY_TEXT, tools_bp
from backend.models import LLMMessage, LLMSession, db
from backend.services import agent_router


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

