"""Deterministic chat auto-capture: intents, scope, rejects, dedupe, engine wrap."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.db]

try:
    from flask import Flask

    from backend.api.memory_api import get_memories_for_context, pop_last_selected_ids
    from backend.models import AgentMemory, db
    from backend.services.memory_capture import capture_from_message
except Exception:  # pragma: no cover - environment without backend deps
    pytest.skip("Flask or backend modules not available", allow_module_level=True)

ENGINE_PATH = (
    Path(__file__).resolve().parents[2] / "services" / "unified_chat_engine.py"
)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.mark.parametrize(
    "message, expected",
    [
        ("remember that the sky is blue", "the sky is blue"),
        ("Remember that the sky is blue", "the sky is blue"),
        ("remember: the sky is blue", "the sky is blue"),
        ("Remember: the sky is blue", "the sky is blue"),
        ("from now on use dark mode", "use dark mode"),
        ("From now on, always use dark mode", "always use dark mode"),
        ("for future reference the API lives in settings", "the API lives in settings"),
        ("For future reference, the API lives in settings", "the API lives in settings"),
    ],
)
def test_remember_forms_store_a_fact_for_every_chat(app, message, expected):
    mem_id = capture_from_message(message, session_id="s1", user_id="u1")
    assert mem_id is not None
    row = db.session.get(AgentMemory, mem_id)
    assert row is not None
    assert row.content == expected
    assert row.type == "fact"
    assert row.source == "chat"
    assert abs((row.importance or 0) - 0.7) < 1e-6
    assert row.session_id is None
    assert row.project_id is None
    assert row.user_id == "u1"


@pytest.mark.parametrize(
    "message, expected",
    [
        ("note that we prefer pytest", "we prefer pytest"),
        ("Note that we prefer pytest", "we prefer pytest"),
        ("my name is Alice", "my name is Alice"),
        ("our timezone is Pacific", "our timezone is Pacific"),
    ],
)
def test_other_intent_forms_store_a_fact_for_this_chat(app, message, expected):
    mem_id = capture_from_message(message, session_id="s1", user_id="u1")
    assert mem_id is not None
    row = db.session.get(AgentMemory, mem_id)
    assert row is not None
    assert row.content == expected
    assert row.type == "fact"
    assert row.source == "chat"
    assert abs((row.importance or 0) - 0.7) < 1e-6
    assert row.session_id == "s1"
    assert row.user_id == "u1"


@pytest.mark.parametrize(
    "message",
    [
        "remember that the sky is blue?",
        "note that we prefer pytest?",
        "is my name Alice?",
        "what is our timezone?",
    ],
)
def test_questions_are_not_captured(app, message):
    assert capture_from_message(message) is None
    assert AgentMemory.query.count() == 0


@pytest.mark.parametrize(
    "message",
    [
        "hi",
        "remember that x",
        "note that hi",
        "my name is",
        "ok thanks",
    ],
)
def test_short_messages_are_not_captured(app, message):
    assert capture_from_message(message) is None
    assert AgentMemory.query.count() == 0


def test_plain_chat_without_intent_is_ignored(app):
    assert capture_from_message("please index the docs folder now") is None
    assert AgentMemory.query.count() == 0


def test_dedupe_returns_existing_id(app):
    first = capture_from_message("remember that the sky is blue")
    second = capture_from_message("Remember that the sky is blue.")
    assert first is not None
    assert second == first
    assert AgentMemory.query.filter_by(type="fact").count() == 1


def test_remember_in_a_project_chat_stays_in_that_project(app):
    mem_id = capture_from_message(
        "remember that the build uses ninja", session_id="s1", project_id=7
    )
    row = db.session.get(AgentMemory, mem_id)
    assert row.session_id is None
    assert row.project_id == 7


def test_other_intent_in_a_project_chat_keeps_chat_and_project(app):
    mem_id = capture_from_message(
        "note that the build uses ninja", session_id="s1", project_id=7
    )
    row = db.session.get(AgentMemory, mem_id)
    assert row.session_id == "s1"
    assert row.project_id == 7


def test_remembered_fact_is_recalled_in_a_new_chat(app):
    mem_id = capture_from_message(
        "remember that my favourite colour is teal", session_id="chat-one"
    )
    assert mem_id is not None
    pop_last_selected_ids()

    block = get_memories_for_context(
        query="what is my favourite colour?", session_id="chat-two"
    )

    assert "my favourite colour is teal" in block
    assert mem_id in pop_last_selected_ids()


def test_chat_scoped_capture_is_not_recalled_in_a_new_chat(app):
    capture_from_message("note that the deploy target is staging", session_id="chat-one")

    elsewhere = get_memories_for_context(query="deploy target", session_id="chat-two")
    same_chat = get_memories_for_context(query="deploy target", session_id="chat-one")

    assert "deploy target is staging" not in elsewhere
    assert "deploy target is staging" in same_chat


def test_project_remember_is_not_recalled_in_another_project(app):
    capture_from_message(
        "remember that the build uses ninja", session_id="chat-one", project_id=7
    )

    other_project = get_memories_for_context(
        query="which build tool?", session_id="chat-two", project_id=9
    )
    same_project = get_memories_for_context(
        query="which build tool?", session_id="chat-two", project_id=7
    )

    assert "build uses ninja" not in other_project
    assert "build uses ninja" in same_project


def test_remember_does_not_reuse_a_copy_scoped_to_another_chat(app):
    db.session.add(AgentMemory(
        id="old-chat-copy",
        content="my favourite colour is teal",
        source="chat",
        type="fact",
        importance=0.7,
        status="active",
        session_id="chat-zero",
    ))
    db.session.commit()

    mem_id = capture_from_message(
        "remember that my favourite colour is teal", session_id="chat-one"
    )

    assert mem_id not in (None, "old-chat-copy")
    assert db.session.get(AgentMemory, mem_id).session_id is None
    assert db.session.get(AgentMemory, "old-chat-copy").session_id == "chat-zero"


def test_remember_reuses_a_copy_every_chat_already_recalls(app):
    db.session.add_all([
        AgentMemory(
            id="global-copy", content="the sky is blue", source="chat",
            type="fact", importance=0.7, status="active",
        ),
        AgentMemory(
            id="project-five-copy", content="the build uses ninja", source="chat",
            type="fact", importance=0.7, status="active", project_id=5,
        ),
    ])
    db.session.commit()

    assert capture_from_message("remember that the sky is blue", session_id="s1") == "global-copy"
    assert capture_from_message(
        "remember that the sky is blue", session_id="s1", project_id=7
    ) == "global-copy"

    in_project_seven = capture_from_message(
        "remember that the build uses ninja", session_id="s1", project_id=7
    )
    assert in_project_seven != "project-five-copy"
    assert db.session.get(AgentMemory, in_project_seven).project_id == 7


def test_engine_call_site_is_exception_safe(monkeypatch):
    """Capture sits in a try/except Exception immediately after the user save."""
    text = ENGINE_PATH.read_text()
    marker = "# 5. Save user message to DB"
    idx = text.find(marker)
    assert idx != -1
    window = text[idx : idx + 900]
    assert "self._save_message" in window
    save_at = window.find("self._save_message")
    capture_at = window.find("capture_from_message")
    except_at = window.find("except Exception")
    assert capture_at != -1
    assert except_at != -1
    assert save_at < capture_at < except_at

    from backend.services import memory_capture as mc

    def boom(*_a, **_k):
        raise RuntimeError("storage down")

    monkeypatch.setattr(mc, "capture_from_message", boom)

    try:
        from backend.services.memory_capture import capture_from_message as _capture

        _capture("remember that the sky is blue")
        raised = False
    except Exception:
        raised = True
    # The raw helper may raise; the engine wrap must not. Reproduce that wrap.
    assert raised is True

    logged = {"hit": False}

    def engine_call_site(message):
        try:
            from backend.services.memory_capture import capture_from_message

            capture_from_message(message)
        except Exception:
            logged["hit"] = True

    engine_call_site("remember that the sky is blue")
    assert logged["hit"] is True
