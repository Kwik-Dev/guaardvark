"""The decision mode store: off unless switched, per decision, readable from any thread."""
from __future__ import annotations

import threading

import pytest
from flask import Flask

from backend.models import Setting, db
from backend.services import decision_settings as S

ENV = "GUAARDVARK_DECISION_MODES"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    S.clear_cache()
    yield
    S.clear_cache()


@pytest.fixture
def rows(monkeypatch):
    """Settings rows served from a dict; counts the reads."""
    store = {"values": {}, "reads": 0}

    def read(key):
        store["reads"] += 1
        return store["values"].get(key)

    monkeypatch.setattr(S, "_read_row", read)
    return store


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


def test_the_override_variable_is_declared_in_the_env_map():
    from backend.utils.settings_utils import ENV_VAR_MAP

    assert ENV_VAR_MAP[S.ENV_KEY] == ENV


def test_a_missing_row_is_off(rows):
    assert S.get_decision_mode("needs_live_data") == "off"


def test_a_stored_row_is_read(rows):
    rows["values"]["decision_mode.needs_live_data"] = "shadow"
    assert S.get_decision_mode("needs_live_data") == "shadow"


@pytest.mark.parametrize("value", ["maybe", "", "ON ", None])
def test_an_unusable_row_is_off_or_normalised(rows, value):
    rows["values"]["decision_mode.chat_route"] = value
    assert S.get_decision_mode("chat_route") == ("on" if value == "ON " else "off")


@pytest.mark.parametrize("raw", [
    '{"needs_live_data": "on", "chat_route": "shadow"}',
    "needs_live_data=on, chat_route=shadow",
])
def test_the_override_wins_over_the_rows(rows, monkeypatch, raw):
    rows["values"]["decision_mode.needs_live_data"] = "off"
    monkeypatch.setenv(ENV, raw)
    assert S.get_decision_mode("needs_live_data") == "on"
    assert S.get_decision_mode("chat_route") == "shadow"
    assert S.get_decision_mode("image_intent") == "off"


@pytest.mark.parametrize("raw", ["{not json", "needs_live_data=loud", "needs_live_data"])
def test_a_bad_override_is_ignored(rows, monkeypatch, raw):
    monkeypatch.setenv(ENV, raw)
    assert S.get_decision_mode("needs_live_data") == "off"


def test_reads_are_cached_for_the_ttl(rows, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(S.time, "monotonic", lambda: clock["now"])
    rows["values"]["decision_mode.chat_route"] = "shadow"
    assert S.get_decision_mode("chat_route") == "shadow"
    rows["values"]["decision_mode.chat_route"] = "on"
    clock["now"] += S.CACHE_TTL_S - 1
    assert S.get_decision_mode("chat_route") == "shadow"
    assert rows["reads"] == 1
    clock["now"] += 2
    assert S.get_decision_mode("chat_route") == "on"
    assert rows["reads"] == 2


def test_a_thread_without_an_app_context_reads_through_the_beat_reader(monkeypatch):
    calls = []

    def read_setting(key, default=None, table="settings"):
        calls.append(key)
        return "shadow"

    from backend import celery_beat_gates

    monkeypatch.setattr(celery_beat_gates, "_read_setting", read_setting)
    seen = {}
    thread = threading.Thread(target=lambda: seen.update(mode=S.get_decision_mode("needs_live_data")))
    thread.start()
    thread.join()
    assert seen["mode"] == "shadow"
    assert calls == ["decision_mode.needs_live_data"]


def test_set_and_read_back_in_an_app_context(app):
    assert S.set_decision_mode("needs_live_data", "shadow") == "shadow"
    assert db.session.get(Setting, "decision_mode.needs_live_data").value == "shadow"
    S.clear_cache()
    assert S.get_decision_mode("needs_live_data") == "shadow"


@pytest.mark.parametrize("decision_id, mode", [("no_such_decision", "on"), ("needs_live_data", "loud")])
def test_set_refuses_what_is_not_declared(app, decision_id, mode):
    with pytest.raises(ValueError):
        S.set_decision_mode(decision_id, mode)


def test_set_needs_an_app_context():
    with pytest.raises(RuntimeError):
        S.set_decision_mode("needs_live_data", "shadow")
