"""Saving a memory whose text is already saved where it would be recalled
returns the saved row instead of writing a second one: add_memory, the HTTP
route the Memory page and MCP use, and the save_memory tool."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

try:
    from flask import Flask
    from backend.models import db, AgentMemory, AgentMemoryAudit
    from backend.api.memory_api import add_memory, find_saved_copy, memory_bp
    from backend.services.memory_contract import normalize_memory_text
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(memory_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.mark.parametrize(
    "a, b",
    [
        ("Prefers metric units", "prefers metric units"),
        ("Prefers  metric\nunits.", "prefers metric units"),
        ("  The build uses ninja!  ", "the build uses ninja"),
        ("the sky is blue .", "The sky is blue"),
        ("Lives on Hauptstraße", "lives on HAUPTSTRASSE"),
    ],
)
def test_copies_normalise_to_the_same_text(a, b):
    assert normalize_memory_text(a) == normalize_memory_text(b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("Prefers metric units", "Prefers imperial units"),
        ("Is the build on ninja?", "Is the build on ninja"),
        ("use tabs", "use tabs, not spaces"),
    ],
)
def test_different_text_stays_different(a, b):
    assert normalize_memory_text(a) != normalize_memory_text(b)


def test_same_text_in_the_same_scope_returns_the_saved_row_untouched(app):
    first = add_memory(content="Prefers metric units", memory_type="preference")
    assert first.already_saved is False
    first_id = first.id
    stamps = (first.created_at, first.updated_at)

    again = add_memory(content="  prefers METRIC units. ", memory_type="fact", tags=["units"], importance=1.0)

    assert again.id == first_id
    assert again.already_saved is True
    assert AgentMemory.query.count() == 1
    row = db.session.get(AgentMemory, first_id)
    assert row.type == "preference"
    assert row.tags is None
    assert (row.created_at, row.updated_at) == stamps
    assert AgentMemoryAudit.query.filter_by(memory_id=first_id).count() == 1


def test_a_copy_kept_for_the_whole_app_answers_a_save_in_one_chat(app):
    saved_id = add_memory(content="The build uses ninja").id

    again = add_memory(
        content="the build uses ninja", session_id="chat-1", project_id=7,
        user_id="u1", workspace_root="repo-a",
    )

    assert again.id == saved_id and again.already_saved is True
    assert AgentMemory.query.count() == 1


def test_a_copy_kept_for_another_chat_or_project_does_not(app):
    in_chat = add_memory(content="The build uses ninja", session_id="chat-1")
    in_chat_id = in_chat.id
    in_project = add_memory(content="The build uses ninja", project_id=5)
    in_project_id = in_project.id
    everywhere = add_memory(content="The build uses ninja")
    everywhere_id = everywhere.id

    assert len({in_chat_id, in_project_id, everywhere_id}) == 3
    assert AgentMemory.query.count() == 3

    # Each of them now answers a repeat where it is recalled.
    repeat = add_memory(content="the build uses ninja.", session_id="chat-1")
    assert repeat.already_saved is True
    assert repeat.id in (in_chat_id, everywhere_id)
    assert AgentMemory.query.count() == 3


def test_another_user_or_workspace_is_another_scope(app):
    mine_id = add_memory(content="Prefers dark mode", user_id="u1").id
    theirs = add_memory(content="Prefers dark mode", user_id="u2")
    assert theirs.id != mine_id and theirs.already_saved is False

    repo_a_id = add_memory(content="Tests run with pytest", workspace_root="repo-a").id
    repo_b = add_memory(content="Tests run with pytest", workspace_root="repo-b")
    assert repo_b.id != repo_a_id and repo_b.already_saved is False


@pytest.mark.parametrize(
    "memory_type, source",
    [("belief_update", "agent"), ("snippet", "candidate_recipe")],
)
def test_event_rows_are_written_every_time(app, memory_type, source):
    """The lesson reconciler counts belief_update rows as sessions, and each
    candidate_recipe row logs one induction, so neither is collapsed."""
    content = '"Firefox icon" was not visible during this session'
    for _ in range(3):
        mem = add_memory(content=content, memory_type=memory_type, source=source)
        assert mem is not None and mem.already_saved is False
    assert AgentMemory.query.count() == 3


def test_an_event_row_is_not_a_saved_copy_of_a_statement(app):
    add_memory(content="Firefox icon was not visible", memory_type="belief_update", source="agent")
    note = add_memory(content="Firefox icon was not visible", memory_type="note")
    assert note.already_saved is False
    assert AgentMemory.query.count() == 2


def test_rows_no_longer_active_do_not_count(app):
    old = add_memory(content="Uses Python 3.12")
    old_id = old.id
    old.status = "archived"
    db.session.commit()

    new = add_memory(content="Uses Python 3.12")

    assert new.id != old_id and new.already_saved is False
    assert find_saved_copy("uses python 3.12") is new


def test_a_row_saved_inactive_is_written_as_asked(app):
    first_id = add_memory(content="Uses Python 3.12").id
    archived = add_memory(content="Uses Python 3.12", status="archived")
    assert archived.id != first_id
    assert archived.status == "archived" and archived.already_saved is False


def test_post_answers_201_then_200_already_saved(app, client):
    first = client.post("/api/memory", json={"content": "Prefers metric units", "type": "preference"})
    assert first.status_code == 201
    assert first.get_json()["already_saved"] is False

    again = client.post("/api/memory", json={"content": "prefers metric units.", "type": "preference"})
    assert again.status_code == 200
    body = again.get_json()
    assert body["success"] is True
    assert body["already_saved"] is True
    assert body["memory"]["id"] == first.get_json()["memory"]["id"]
    assert AgentMemory.query.count() == 1


def test_save_memory_tool_says_the_text_was_already_saved(app):
    from backend.tools.memory_tools import SaveMemoryTool

    first = SaveMemoryTool().execute(content="Prefers metric units", type="preference")
    assert first.success is True
    assert first.metadata["already_saved"] is False
    assert first.output.startswith("Successfully saved to long-term memory")

    again = SaveMemoryTool().execute(content="prefers metric units", type="fact")
    assert again.success is True
    assert again.metadata["already_saved"] is True
    assert again.metadata["id"] == first.metadata["id"]
    assert again.output == (
        f"Already saved in long-term memory (ID: {first.metadata['id']}, preference); "
        "nothing new was stored."
    )
    assert AgentMemory.query.count() == 1


def test_save_memory_over_mcp_reports_the_backends_answer(monkeypatch):
    from backend.tools import memory_tools

    class _Reply:
        body = {
            "success": True,
            "already_saved": True,
            "memory": {"id": "abc123", "content": "Prefers metric units", "type": "preference"},
        }

    calls = []

    def fake_request_json(method, path, payload=None):
        calls.append((method, path, payload))
        return _Reply()

    monkeypatch.setattr(memory_tools, "request_json", fake_request_json)
    tool = memory_tools.SaveMemoryTool()
    tool.set_context({"transport": "mcp"})

    result = tool.execute(content="prefers metric units")

    assert calls and calls[0][:2] == ("POST", "/api/memory")
    assert result.success is True
    assert result.metadata["already_saved"] is True
    assert result.output.startswith("Already saved in long-term memory (ID: abc123, preference)")
