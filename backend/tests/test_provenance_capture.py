"""A reply knows what produced it, and the client learns which row it became.

Feedback names one message and flows back to the sources in its provenance,
so every save path must write the block and announce the id.
"""
import os
import sys

import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask
from backend.models import db, LLMMessage, AgentMemory


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


def _engine(app):
    from backend.services.unified_chat_engine import UnifiedChatEngine
    eng = UnifiedChatEngine.__new__(UnifiedChatEngine)
    eng.app = app
    eng._project_id = None
    eng._request_id = "req-1"
    eng._emit_fn = MagicMock()
    eng._prov = {"request_id": "req-1", "tier": 2, "model": "gemma4:e4b",
                 "memory_ids": ["m1"], "rag_sources": ["notes.pdf"]}
    eng._brain_state = None
    return eng


def test_assistant_save_writes_provenance_and_announces_the_row(app):
    eng = _engine(app)
    steps = [{"iteration": 1, "tool_calls": [{"tool_name": "web_search"}, {"tool_name": "web_search"}]}]
    with patch("backend.services.agent_control_service.get_agent_control_service") as acs:
        acs.return_value.drain_recipe_usage.return_value = {"recipe": {"name": "youtube_search", "fallback": False},
                                                            "agent_tasks": ["open youtube"]}
        new_id = eng._save_message("s1", "assistant", "hello", extra_data={"steps": steps})
    row = db.session.get(LLMMessage, new_id)
    prov = row.extra_data["provenance"]
    assert prov["request_id"] == "req-1"
    assert prov["tier"] == 2
    assert prov["model"] == "gemma4:e4b"
    assert prov["memory_ids"] == ["m1"]
    assert prov["rag_sources"] == ["notes.pdf"]
    assert prov["tools"] == ["web_search"]
    assert prov["recipe"] == {"name": "youtube_search", "fallback": False}
    assert prov["agent_tasks"] == ["open youtube"]
    assert row.extra_data["steps"] == steps, "existing keys survive"
    eng._emit_fn.assert_called_once()
    event, payload = eng._emit_fn.call_args.args
    assert event == "chat:message_saved"
    assert payload == {"session_id": "s1", "request_id": "req-1", "message_id": new_id, "role": "assistant"}


def test_user_save_neither_annotates_nor_announces(app):
    eng = _engine(app)
    new_id = eng._save_message("s1", "user", "hi")
    row = db.session.get(LLMMessage, new_id)
    assert row.extra_data is None
    eng._emit_fn.assert_not_called()


def test_memory_ids_are_popped_once(app):
    from backend.api import memory_api
    m = AgentMemory(id="mem-a", content="the user prefers short answers", source="manual",
                    type="note", importance=0.9, confidence=1.0, status="active")
    db.session.add(m)
    db.session.commit()
    text = memory_api._get_memories_for_context_inner(limit=5, query="short answers")
    assert "short answers" in text
    assert memory_api.pop_last_selected_ids() == ["mem-a"]
    assert memory_api.pop_last_selected_ids() == [], "pop, never peek"


def test_recipe_usage_drains_once():
    from backend.services.agent_control_service import AgentControlService
    svc = AgentControlService.__new__(AgentControlService)
    svc._recipe_usage_buffer = []
    svc.note_recipe_usage("open youtube")
    svc.note_recipe_usage("search cats", "youtube_search", fallback=True)
    assert svc.drain_recipe_usage() == {"agent_tasks": ["open youtube", "search cats"],
                                        "recipe": {"name": "youtube_search", "fallback": True}}
    assert svc.drain_recipe_usage() == {"recipe": None}


def test_brain_persist_turn_announces_assistant_rows_only(app):
    from backend.services.agent_brain import _persist_turn, _brain_provenance
    emit = MagicMock()
    with patch("backend.services.agent_control_service.get_agent_control_service") as acs:
        acs.return_value.drain_recipe_usage.return_value = {"recipe": None}
        prov = _brain_provenance("req-9", 1, None, [{"tools": ["agent_task_execute"]}])
    assert prov == {"request_id": "req-9", "tier": 1, "tools": ["agent_task_execute"], "recipe": None}
    uid = _persist_turn(app, "s2", "user", "ping", None, emit_fn=emit, request_id="req-9")
    aid = _persist_turn(app, "s2", "assistant", "pong", {"provenance": prov}, emit_fn=emit, request_id="req-9")
    assert uid and aid and aid > uid
    emit.assert_called_once_with("chat:message_saved", {
        "session_id": "s2", "request_id": "req-9", "message_id": aid, "role": "assistant"})
    assert db.session.get(LLMMessage, aid).extra_data["provenance"]["tier"] == 1


def test_engine_reuses_the_request_id_it_was_given():
    """The HTTP layer's id must be the one on chat:complete and the saved row."""
    from backend.services.unified_chat_engine import UnifiedChatEngine
    import inspect
    src = inspect.getsource(UnifiedChatEngine.chat)
    assert 'options or {}).get("request_id")' in src


def test_a_reflex_reply_is_persisted_under_the_turn_id(app):
    """Tier 1 used to answer without writing any row; a thumb had nothing to name."""
    from backend.services.agent_brain import AgentBrain
    from backend.services.brain_state import BrainState
    from types import SimpleNamespace
    BrainState.reset()
    try:
        state = BrainState.get_instance()
        state.health.reflexes_loaded = True
        state.health.llm_available = False
        state.health.tools_available = False
        reflex = SimpleNamespace(
            name="greeting",
            handler=lambda msg, match, ctx: SimpleNamespace(
                success=True, response="Hello there.", tool_called=None, tool_params=None),
        )
        state.match_reflex = lambda message: (reflex, None)
        brain = AgentBrain(state=state)
        emit = MagicMock()
        result = brain.process("s3", "hello", {"request_id": "req-http"}, emit, app=app)
        assert result["tier"] == 1 and result["request_id"] == "req-http"
        events = [c.args[0] for c in emit.call_args_list]
        assert events[-1] == "chat:message_saved"
        saved = emit.call_args_list[-1].args[1]
        rows = LLMMessage.query.filter_by(session_id="s3").order_by(LLMMessage.id).all()
        assert [r.role for r in rows] == ["user", "assistant"]
        assert saved["message_id"] == rows[1].id and saved["request_id"] == "req-http"
        prov = rows[1].extra_data["provenance"]
        assert prov["tier"] == 1 and prov["reflex"] == "greeting" and prov["request_id"] == "req-http"
        complete = [c.args[1] for c in emit.call_args_list if c.args[0] == "chat:complete"][0]
        assert complete["request_id"] == "req-http", "the ack, chat:complete and the row agree"
    finally:
        BrainState.reset()
