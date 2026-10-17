"""/api/enhanced-chat/<id>/history returns every saved message field, so a
thumb, the thinking, the verified mark and the web-search offer survive a page
refresh, and the row id comes back as the number a thumb names. In-memory
SQLite, no live database."""

import pytest

SAVED = {
    "feedback": "up",
    "thinking": "The user asks when the war ended.",
    "verified": True,
    "web_search_offer": {"action": "search", "query": "when did the war end?"},
    "provenance": {"request_id": "req-7", "tier": 2},
    "steps": [{"iteration": 1, "tool_calls": [{"tool_name": "web_search", "params": {"query": "war"}}]}],
}


@pytest.fixture
def history():
    from flask import Flask

    from backend.api import enhanced_chat_api as eca
    from backend.models import LLMMessage, LLMSession, db

    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    app.register_blueprint(eca.enhanced_chat_bp)
    with app.app_context():
        db.create_all()
        db.session.add(LLMSession(id="s1", user="default"))
        db.session.flush()
        row = LLMMessage(session_id="s1", role="assistant", content="It ended in 1945.", extra_data=SAVED)
        db.session.add(row)
        db.session.commit()
        message = app.test_client().get("/api/enhanced-chat/s1/history").get_json()["messages"][0]
        yield message, row.id
        db.session.remove()
        db.drop_all()


def test_the_row_id_comes_back_as_a_number(history):
    message, row_id = history
    assert message["message_id"] == row_id
    assert isinstance(message["message_id"], int)
    assert message["id"] == str(row_id)


def test_every_other_saved_field_comes_back_in_extra_data(history):
    message, _row_id = history
    extra = message["extra_data"]
    assert extra["feedback"] == "up"
    assert extra["thinking"] == SAVED["thinking"]
    assert extra["verified"] is True
    assert extra["provenance"]["request_id"] == "req-7"


def test_flattened_fields_and_steps_are_not_sent_twice(history):
    message, _row_id = history
    assert message["web_search_offer"] == SAVED["web_search_offer"]
    assert message["toolCalls"][0]["tool_calls"][0]["tool_name"] == "web_search"
    assert "steps" not in message["extra_data"]
    assert "web_search_offer" not in message["extra_data"]
