"""A thumb credits or blames the memories behind a reply, writes a correction
from a stated reason, and an un-thumb reverses exactly that."""
import os
import sys

import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask
from backend.models import db, LLMMessage, ToolFeedback, AgentMemory, AgentMemoryAudit
from backend.services import feedback_teacher as ft


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context(), patch("backend.config.GUAARDVARK_ROOT", str(tmp_path)):
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _mem(mid, conf=1.0, status="active", source="manual", content=None):
    m = AgentMemory(id=mid, content=content or f"memory {mid}", source=source, type="note",
                    importance=0.8, confidence=conf, status=status)
    db.session.add(m)
    db.session.commit()
    return m


def _fb(verdict, memory_ids=(), recipe=None, why=None, tags=None):
    u = LLMMessage(session_id="s", role="user", content="what is the plan?")
    a = LLMMessage(session_id="s", role="assistant", content="Here is the plan.",
                   extra_data={"provenance": {"request_id": "r", "memory_ids": list(memory_ids), "recipe": recipe}})
    db.session.add_all([u, a])
    db.session.commit()
    fb = ToolFeedback(session_id="s", tool_name="x", task="Here is the plan.", positive=(verdict == "up"),
                      message_id=a.id, request_id="r", kind="response", verdict=verdict,
                      provenance=a.extra_data["provenance"], applied=[], why_text=why, why_tags=list(tags or []))
    db.session.add(fb)
    db.session.commit()
    return fb, a, u


def test_one_down_leaves_a_full_confidence_memory_at_three_quarters(app):
    m = _mem("m1")
    fb, a, u = _fb("down", ["m1"])
    taught = ft.apply(fb, a, u)
    assert m.confidence == pytest.approx(0.75)
    assert m.status == "active"
    assert m.extra_data["feedback"]["downs"] == 1 and m.extra_data["feedback"]["last_verdict"] == "down"
    assert [t["kind"] for t in taught] == ["memory_blame"]
    assert AgentMemoryAudit.query.filter_by(action="feedback_down", memory_id="m1").count() == 1
    kinds = [e["kind"] for e in fb.applied]
    assert kinds == ["memory_confidence", "memory_counter"]


def test_blame_is_spread_over_the_prompt(app):
    for i in range(6):
        _mem(f"m{i}")
    fb, a, u = _fb("down", [f"m{i}" for i in range(6)])
    ft.apply(fb, a, u)
    # alpha 0.25 * min(1, 3/6) = 0.125
    assert db.session.get(AgentMemory, "m0").confidence == pytest.approx(0.875)


def test_credit_moves_slowly_toward_one(app):
    m = _mem("m1", conf=0.5)
    fb, a, u = _fb("up", ["m1"])
    ft.apply(fb, a, u)
    assert m.confidence == pytest.approx(0.55)
    assert m.extra_data["feedback"]["ups"] == 1


def test_archive_needs_both_gates_and_never_marks_wrong(app):
    m = _mem("m1", conf=0.30)
    m.extra_data = {"feedback": {"ups": 0, "downs": 2}}
    db.session.commit()
    fb, a, u = _fb("down", ["m1"])
    taught = ft.apply(fb, a, u)
    assert m.status == "archived", "confidence under the floor and net -3"
    assert any(t["kind"] == "memory_archived" for t in taught)
    assert m.status != "wrong"
    # Same net but confidence still healthy: no archive.
    m2 = _mem("m2", conf=0.9)
    m2.extra_data = {"feedback": {"ups": 0, "downs": 5}}
    db.session.commit()
    fb2, a2, u2 = _fb("down", ["m2"])
    ft.apply(fb2, a2, u2)
    assert m2.status == "active"


def test_a_stated_reason_becomes_a_correction_the_prompts_read(app):
    fb, a, u = _fb("down", why="Do not answer in bullet lists", tags=["format"])
    taught = ft.apply(fb, a, u)
    rows = AgentMemory.query.filter_by(source="learned_from_feedback").all()
    assert len(rows) == 1
    mem = rows[0]
    assert mem.content == "Do not answer in bullet lists"
    assert "correction" in mem.tags and "format" in mem.tags
    assert mem.importance == pytest.approx(0.80) and mem.confidence == pytest.approx(0.90)
    assert mem.extra_data["feedback_id"] == fb.id and mem.extra_data["task"] == "what is the plan?"
    assert any(t["kind"] == "memory_created" for t in taught)
    from backend.api.memory_api import get_memories_for_context, get_lessons_for_agent_prompt
    chat = get_memories_for_context(limit=10, query="bullet lists")
    assert chat.endswith("Corrections from your feedback (do not repeat these):\n- Do not answer in bullet lists")
    agent = get_lessons_for_agent_prompt()
    assert "### Corrections from feedback\n- (avoid) Do not answer in bullet lists" in agent
    # The same reason again reinforces instead of duplicating.
    fb2, a2, u2 = _fb("down", why="do not answer in  bullet lists")
    ft.apply(fb2, a2, u2)
    assert AgentMemory.query.filter_by(source="learned_from_feedback").count() == 1
    assert mem.extra_data["feedback"]["ups"] == 1


def test_a_positive_reason_is_a_keep_note(app):
    fb, a, u = _fb("up", why="Short answers like this are perfect")
    ft.apply(fb, a, u)
    mem = AgentMemory.query.filter_by(source="learned_from_feedback").one()
    assert "keep" in mem.tags
    from backend.api.memory_api import get_memories_for_context
    assert "Confirmed by your feedback (keep doing this):" in get_memories_for_context(limit=10, query="short answers")


def test_retract_restores_confidence_counters_and_deletes_an_untouched_correction(app):
    m = _mem("m1")
    fb, a, u = _fb("down", ["m1"], why="too verbose")
    ft.apply(fb, a, u)
    assert m.confidence == pytest.approx(0.75)
    assert AgentMemory.query.filter_by(source="learned_from_feedback").count() == 1
    taught = ft.retract(fb)
    assert m.confidence == pytest.approx(1.0)
    assert m.extra_data["feedback"]["downs"] == 0
    assert AgentMemory.query.filter_by(source="learned_from_feedback").count() == 0
    assert fb.applied == []
    assert taught[0]["kind"] == "retracted"


def test_retract_unarchives_only_if_still_archived(app):
    m = _mem("m1", conf=0.30)
    m.extra_data = {"feedback": {"ups": 0, "downs": 2}}
    db.session.commit()
    fb, a, u = _fb("down", ["m1"])
    ft.apply(fb, a, u)
    assert m.status == "archived"
    m.status = "wrong"  # a human decided meanwhile
    db.session.commit()
    ft.retract(fb)
    assert m.status == "wrong", "a human change wins"


def test_recipe_down_disables_and_retract_reenables(app, tmp_path):
    from backend.services import recipe_stats
    fb, a, u = _fb("down", recipe={"name": "youtube_search", "fallback": False})
    taught = ft.apply(fb, a, u)
    assert recipe_stats.is_disabled("youtube_search")
    assert recipe_stats.get("youtube_search")["disabled_reason"] == f"thumbs_down:{fb.id}"
    assert any(t["kind"] == "recipe_disabled" for t in taught)
    ft.retract(fb)
    assert not recipe_stats.is_disabled("youtube_search")
    assert recipe_stats.get("youtube_search")["downs"] == 0


def test_a_down_on_a_fallback_run_counts_but_does_not_disable_alone(app):
    from backend.services import recipe_stats
    recipe_stats.touch_run("r", fallback=True)
    fb, a, u = _fb("down", recipe={"name": "r", "fallback": True})
    ft.apply(fb, a, u)
    assert not recipe_stats.is_disabled("r"), "one fallback: the loop produced the outcome"
    recipe_stats.touch_run("r", fallback=True)
    fb2, a2, u2 = _fb("down", recipe={"name": "r", "fallback": True})
    ft.apply(fb2, a2, u2)
    assert recipe_stats.is_disabled("r"), "two fallbacks and a down: a bad shortcut"


def test_provisional_recipe_graduates_on_two_clean_ups(app):
    from backend.services import recipe_stats
    recipe_stats.set_provisional("auto_thing", True)
    for _ in range(2):
        fb, a, u = _fb("up", recipe={"name": "auto_thing", "fallback": False})
        taught = ft.apply(fb, a, u)
    assert recipe_stats.get("auto_thing")["provisional"] is False
    assert any(t["kind"] == "recipe_graduated" for t in taught)
