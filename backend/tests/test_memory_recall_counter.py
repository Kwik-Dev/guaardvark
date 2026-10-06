"""A memory counts as recalled (access_count, last_accessed_at) only when its
line reached the prompt; rows the character budget cut stay as they were."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask
from backend.models import db, AgentMemory


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


def _note(mid, content):
    m = AgentMemory(id=mid, content=content, source="manual", type="note",
                    importance=0.8, confidence=1.0, status="active")
    db.session.add(m)
    db.session.commit()
    return m


def _counts(ids):
    return {mid: int(db.session.get(AgentMemory, mid).access_count or 0) for mid in ids}


def test_chat_recall_counts_only_the_notes_that_fit_the_budget(app):
    from backend.api import memory_api
    all_ids = [f"m{i:02d}" for i in range(30)]
    for mid in all_ids:
        # Each renders as a 402-character line (the note cap of 400 plus "- ").
        _note(mid, f"Project falcon detail {mid}: " + "x" * 420)

    # 550 tokens is a 2200-character budget: five lines fit, a sixth does not.
    text = memory_api._get_memories_for_context_inner(limit=30, max_tokens=550, query="falcon")
    shown = [mid for mid in all_ids if f"falcon detail {mid}:" in text]
    assert len(shown) == 5
    assert sorted(memory_api.pop_last_selected_ids()) == shown

    counts = _counts(all_ids)
    assert {mid for mid, n in counts.items() if n} == set(shown)
    assert all(counts[mid] == 1 for mid in shown)
    for mid in all_ids:
        accessed = db.session.get(AgentMemory, mid).last_accessed_at
        assert (accessed is not None) == (mid in shown)


def test_chat_recall_counts_each_shown_note_once_per_prompt(app):
    from backend.api import memory_api
    _note("a", "Project falcon ships on Fridays")
    _note("b", "Project falcon uses the blue palette")

    memory_api._get_memories_for_context_inner(limit=10, max_tokens=500, query="falcon")
    memory_api._get_memories_for_context_inner(limit=10, max_tokens=500, query="falcon")

    assert _counts(["a", "b"]) == {"a": 2, "b": 2}


def test_agent_prompt_counts_only_the_notes_that_fit_max_chars(app):
    from backend.api import memory_api
    all_ids = [f"n{i}" for i in range(8)]
    for mid in all_ids:
        # Each renders as a 402-character block ("- " plus the 400-char cap).
        _note(mid, f"Operating note {mid}: " + "y" * 420)

    # 1300 characters holds three blocks (each costs 402 plus a 2-char gap).
    text = memory_api._get_lessons_for_agent_prompt_inner(max_rows=8, max_chars=1300)
    shown = [mid for mid in all_ids if f"Operating note {mid}:" in text]
    assert len(shown) == 3

    counts = _counts(all_ids)
    assert {mid for mid, n in counts.items() if n} == set(shown)
    assert all(counts[mid] == 1 for mid in shown)
