"""A Tier 3 turn is saved like any other: the question, then the answer, under a
session row that exists even when the chat is new.

The database is an in-memory SQLite app with foreign keys enforced, as Postgres
enforces them; the executor is stubbed at the class the code constructs.
"""
import os
import sys

import pytest
from flask import Flask
from sqlalchemy import event

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.models import LLMMessage, LLMSession, Setting, db
from backend.services.agent_executor import AgentResult
from backend.tests.test_agent_answer_check_label import _deliberate


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
    with flask_app.app_context():
        event.listen(db.engine, "connect", lambda conn, _rec: conn.execute("PRAGMA foreign_keys=ON"))
        db.engine.dispose()
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


def test_a_new_chat_answered_by_tier3_keeps_question_and_answer(app):
    _deliberate(app, AgentResult(final_answer="They are in the Projects folder.", verified=None))

    assert db.session.get(LLMSession, "s1") is not None
    rows = db.session.query(LLMMessage).filter_by(session_id="s1").order_by(LLMMessage.id).all()
    assert [(r.role, r.content) for r in rows] == [
        ("user", "what is in my notes?"),
        ("assistant", "They are in the Projects folder."),
    ]


def test_persist_turn_creates_the_session_row(app):
    from backend.services.agent_brain import _persist_turn
    assert _persist_turn(app, "brand-new", "assistant", "hi", None)
    assert db.session.get(LLMSession, "brand-new") is not None


def test_llm_debug_read_in_a_request_holds_for_worker_threads(app, monkeypatch):
    import backend.utils.settings_utils as settings_utils
    monkeypatch.delenv("GUAARDVARK_LLM_DEBUG", raising=False)
    monkeypatch.setattr(settings_utils, "_llm_debug_seen", None)
    db.session.add(Setting(key="llm_debug", value="true"))
    db.session.commit()
    assert settings_utils.get_llm_debug() is True

    from flask import has_app_context
    # Leave every pushed context, as a worker thread has none.
    import flask.globals
    saved = []
    while has_app_context():
        top = flask.globals._cv_app.get()
        saved.append(top)
        top.pop()
    try:
        assert settings_utils.get_llm_debug() is True
    finally:
        for top in reversed(saved):
            top.push()
