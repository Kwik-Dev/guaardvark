"""Rules from the Rules page reach the chat system prompt.

The unified engine and the brain's pre-rendered prefix carry the active rules
block (SYSTEM, LEARNED and USER_GLOBAL) behind the Settings "Rules" switch,
which also gates the persona; a rule write refreshes the prefix without a
restart; the seeded ``enhanced_chat`` prompt is selectable; and on the stock
seed, where every rule is inactive, the prompt is unchanged.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.rules, pytest.mark.db]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
os.environ["GUAARDVARK_MODE"] = "test"

try:
    from flask import Flask

    import backend.services.unified_chat_engine as uce
    from backend import rule_utils
    from backend.api.rules_api import rules_bp
    from backend.api.settings_api import settings_bp
    from backend.models import Rule, Setting, db
    from backend.services.brain_state import BrainState, refresh_chat_prompts
except Exception:  # pragma: no cover - environment without Flask/SQLAlchemy
    pytest.skip("Flask or backend modules not available", allow_module_level=True)

SEED_FILE = Path(__file__).resolve().parents[2] / "seed_rules.json"
DEFAULT_PROMPT = "You are a helpful AI assistant. Be accurate, concise, and honest."


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(rules_bp)
    app.register_blueprint(settings_bp)
    app.brain_state = None
    BrainState.reset()
    with app.app_context():
        db.create_all()
        rule_utils.clear_rule_caches()
        yield app
        db.session.remove()
        db.drop_all()
    BrainState.reset()


def _set_switch(enabled: bool):
    row = db.session.get(Setting, "rules_enabled")
    if row is None:
        db.session.add(Setting(key="rules_enabled", value="true" if enabled else "false"))
    else:
        row.value = "true" if enabled else "false"
    db.session.commit()


def _rule(text, level="USER_GLOBAL", active=True, targets='["__ALL__"]', **kw):
    row = Rule(
        level=level,
        type=kw.pop("type", "PROMPT_TEMPLATE"),
        rule_text=text,
        is_active=active,
        target_models_json=targets,
        **kw,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _seed_stock_rules():
    """Insert seed_rules.json the way ``seed_rules_from_file`` does, without
    importing ``seed_data`` (which boots the whole app)."""
    rows = json.loads(SEED_FILE.read_text())["rules"]
    for r in rows:
        db.session.add(
            Rule(
                name=r["name"],
                level=r.get("level", "USER_GLOBAL"),
                type=r.get("type", "PROMPT_TEMPLATE"),
                command_label=r.get("command_label"),
                rule_text=r["rule_text"],
                description=r.get("description", ""),
                output_schema_name=r.get("output_schema_name"),
                target_models_json=r.get("target_models_json", '["__ALL__"]'),
                is_active=r.get("is_active", False),
            )
        )
    db.session.commit()
    return rows


def _engine():
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.app = None
    return e


def _brain(model="test-model"):
    state = BrainState.get_instance()
    state._app = None
    state.active_model = model
    state._build_system_prompts()
    return state


def _old_prefix():
    """The prefix the brain rendered before rules joined it: honesty, then the
    live-block placeholders; the persona position is empty on the stock seed."""
    from backend.services.honesty_steering import HonestySteering

    honesty = HonestySteering().get_steering_prompt(intent="general", intensity="standard") or ""
    return (honesty + "\n\n" if honesty else "") + "{MEMORY_BLOCK}{DESKTOP_STATE}"


# -- the rules block reaches the unified engine --------------------------------

def test_user_global_rule_reaches_the_engine_prompt(app):
    _set_switch(True)
    _rule("Always answer in haiku.")
    _rule("Never mention the weather.", level="LEARNED")

    text = _engine()._load_rules("test-model")

    assert text.startswith(DEFAULT_PROMPT)
    assert "--- Applicable Rules & Guidelines ---" in text
    assert "- Always answer in haiku." in text
    assert "- Never mention the weather." in text
    assert text.index("## Learned Rules:") < text.index("## User_global Rules:")


def test_inactive_and_other_model_rules_stay_out(app):
    _set_switch(True)
    _rule("Always answer in haiku.")
    _rule("I am switched off.", active=False)
    _rule("I am for another model.", targets='["other-model:latest"]')

    text = _engine()._load_rules("test-model")

    assert "- Always answer in haiku." in text
    assert "I am switched off." not in text
    assert "I am for another model." not in text


def test_brain_prefix_carries_persona_and_rules(app):
    _set_switch(True)
    _rule("You are Ada, the lab assistant.", level="SYSTEM",
          name="global_default_chat_system_prompt")
    _rule("Always answer in haiku.")

    state = _brain()
    prefix = state.system_prompts["chat"]

    assert "You are Ada, the lab assistant." in prefix
    assert "- Always answer in haiku." in prefix
    assert prefix.index("Ada") < prefix.index("Applicable Rules") < prefix.index("{MEMORY_BLOCK}")
    assert state.persona_rule_id is not None

    with patch("backend.api.memory_api.get_memories_for_context", return_value=""), \
         patch("backend.utils.platform.screen_agent_available", return_value=False):
        prompt = state.get_system_prompt("chat", query="hello", skip_tools=True)
    assert "- Always answer in haiku." in prompt
    assert "{MEMORY_BLOCK}" not in prompt


# -- the Settings switch gates both ---------------------------------------------

def test_switch_off_removes_rules_block_and_persona(app):
    _rule("You are Ada, the lab assistant.", level="SYSTEM",
          name="global_default_chat_system_prompt")
    _rule("Always answer in haiku.")

    _set_switch(False)
    off = _brain()
    assert "Ada" not in off.system_prompts["chat"]
    assert "haiku" not in off.system_prompts["chat"]
    assert off.persona_rule_id is None
    assert _engine()._load_rules("test-model") == DEFAULT_PROMPT

    _set_switch(True)
    off.refresh_prompts()
    assert "Ada" in off.system_prompts["chat"]
    assert "haiku" in off.system_prompts["chat"]
    assert "haiku" in _engine()._load_rules("test-model")


def test_flipping_the_switch_refreshes_the_prefix(app):
    _rule("Always answer in haiku.")
    _set_switch(False)
    state = _brain()
    state._initialized = True
    app.brain_state = state
    client = app.test_client()

    res = client.post("/api/settings/rules_enabled", json={"rules_enabled": True})
    assert res.status_code == 200
    assert "haiku" in state.system_prompts["chat"]

    res = client.post("/api/settings/rules_enabled", json={"rules_enabled": False})
    assert res.status_code == 200
    assert "haiku" not in state.system_prompts["chat"]


# -- a rule write refreshes what chat reads ------------------------------------

def test_rule_writes_refresh_the_brain_prompts(app):
    brain = MagicMock(_initialized=True)
    app.brain_state = brain
    client = app.test_client()

    res = client.post("/api/rules", json={"name": "haiku", "rule_text": "Always answer in haiku."})
    assert res.status_code == 201
    rule_id = res.get_json()["data"]["id"]
    assert brain.refresh_prompts.call_count == 1

    assert client.put(f"/api/rules/{rule_id}", json={"is_active": False}).status_code == 200
    assert brain.refresh_prompts.call_count == 2

    assert client.delete(f"/api/rules/{rule_id}").status_code == 200
    assert brain.refresh_prompts.call_count == 3

    assert client.delete("/api/rules/learned").status_code == 200
    assert brain.refresh_prompts.call_count == 4


def test_refresh_is_skipped_without_an_initialized_brain(app):
    app.brain_state = MagicMock(_initialized=False)
    res = app.test_client().post(
        "/api/rules", json={"name": "haiku", "rule_text": "Always answer in haiku."}
    )
    assert res.status_code == 201
    assert app.brain_state.refresh_prompts.call_count == 0
    assert refresh_chat_prompts() is False


def test_saved_rule_reaches_the_next_prompt_without_restart(app):
    _set_switch(True)
    state = _brain()
    state._initialized = True
    app.brain_state = state
    client = app.test_client()
    assert "haiku" not in state.system_prompts["chat"]

    # What the Rules page's "New" sends: a PROMPT_TEMPLATE at USER_GLOBAL.
    res = client.post("/api/rules", json={
        "name": "haiku", "rule_text": "Always answer in haiku.",
        "level": "USER_GLOBAL", "type": "PROMPT_TEMPLATE",
    })
    assert res.status_code == 201
    assert "- Always answer in haiku." in state.system_prompts["chat"]

    client.put(f"/api/rules/{res.get_json()['data']['id']}", json={"is_active": False})
    assert "haiku" not in state.system_prompts["chat"]


def test_deleting_an_older_rule_leaves_no_stale_block(app):
    """The lookup cache is keyed on the newest updated_at; deleting an older row
    keeps that key, so the write path clears the cache."""
    _set_switch(True)
    client = app.test_client()
    first = client.post("/api/rules", json={
        "name": "a", "rule_text": "First rule here.", "level": "USER_GLOBAL",
    }).get_json()["data"]["id"]
    client.post("/api/rules", json={"name": "b", "rule_text": "Second rule here.", "level": "USER_GLOBAL"})
    assert "First rule here." in _engine()._load_rules("test-model")

    client.delete(f"/api/rules/{first}")

    text = _engine()._load_rules("test-model")
    assert "First rule here." not in text
    assert "Second rule here." in text


# -- the block is bounded --------------------------------------------------------

def test_rules_block_is_bounded_by_whole_rules(app):
    _set_switch(True)
    for i in range(40):
        _rule(f"Rule {i:02d} " + ("x" * 300))

    block = rule_utils.get_chat_rules_block("test-model")

    assert len(block) <= rule_utils.CHAT_RULES_MAX_CHARS
    assert "- Rule 00" in block
    for line in block.splitlines():
        if line.startswith("- "):
            assert line.endswith("x" * 300), "a rule was cut mid-text"
    assert not block.rstrip("\n").endswith("Rules:"), "an empty level header was left behind"


def test_rules_block_is_empty_when_no_rule_fits(app, monkeypatch):
    _set_switch(True)
    _rule("A rule longer than the whole budget " + ("x" * 200))
    monkeypatch.setattr(rule_utils, "CHAT_RULES_MAX_CHARS", 80)

    assert rule_utils.get_chat_rules_block("test-model") == ""
    assert _engine()._load_rules("test-model") == DEFAULT_PROMPT


# -- the seeded enhanced_chat prompt is selectable ------------------------------

def test_enhanced_chat_seed_is_selectable_once_active(app):
    rows = _seed_stock_rules()
    assert next(r for r in rows if r["name"] == "enhanced_chat")["level"] == "SYSTEM"

    assert rule_utils.get_active_system_prompt("enhanced_chat", db.session) == (None, None)

    row = Rule.query.filter_by(name="enhanced_chat").one()
    row.is_active = True
    db.session.commit()
    rule_utils.clear_rule_caches()

    text, rule_id = rule_utils.get_active_system_prompt("enhanced_chat", db.session)
    assert rule_id == row.id
    assert "{rules_str}" in text


def test_prompt_slot_rows_are_stored_at_system_level(app):
    """A pre-existing enhanced_chat row at USER_GLOBAL becomes selectable on its
    next save; a new one is stored at SYSTEM whatever level the client sent."""
    legacy = _rule("{rules_str}\n\nOld enhanced chat prompt.", level="USER_GLOBAL",
                   name="enhanced_chat", active=False)
    client = app.test_client()

    assert client.put(f"/api/rules/{legacy.id}", json={"is_active": True}).status_code == 200
    db.session.refresh(legacy)
    assert legacy.level == "SYSTEM"
    text, rule_id = rule_utils.get_active_system_prompt("enhanced_chat", db.session)
    assert rule_id == legacy.id

    res = client.post("/api/rules", json={
        "name": "global_default_chat_system_prompt", "rule_text": "You are Ada.",
        "level": "USER_GLOBAL", "is_active": False,
    })
    assert res.status_code == 201
    assert db.session.get(Rule, res.get_json()["data"]["id"]).level == "SYSTEM"

    res = client.post("/api/rules", json={"name": "haiku", "rule_text": "Always answer in haiku.",
                                          "level": "USER_GLOBAL"})
    assert db.session.get(Rule, res.get_json()["data"]["id"]).level == "USER_GLOBAL"


# -- stock impact: nothing changes until a rule is active ------------------------

@pytest.mark.parametrize("switch", [None, False, True])
def test_stock_seed_leaves_the_chat_prompt_unchanged(app, switch):
    """Every seeded rule is inactive, so with the switch unset (a fresh clone),
    off or on, the brain prefix and the engine persona are what they were
    before rules joined the prompt."""
    _seed_stock_rules()
    assert Rule.query.filter_by(is_active=True).count() == 0
    if switch is not None:
        _set_switch(switch)

    state = _brain()
    assert state.system_prompts["chat"] == _old_prefix()
    assert state.persona_rule_id is None
    assert _engine()._load_rules("test-model") == DEFAULT_PROMPT
    assert rule_utils.get_chat_rules_block("test-model") == ""


def test_empty_rules_table_leaves_the_chat_prompt_unchanged(app):
    _set_switch(True)
    assert _brain().system_prompts["chat"] == _old_prefix()
    assert _engine()._load_rules("test-model") == DEFAULT_PROMPT
