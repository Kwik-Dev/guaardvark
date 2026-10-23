"""Characterization: the turn router's legacy route is the handler today's chain picks.

Each row of backend/tests/fixtures/turn_router/legacy_corpus.json is run twice:
through ``turn_router.route_turn(dry=True)`` and through the real
``AgentBrain.process`` and engine intercepts with every handler stubbed
(harness.chain_outcome). Both must give the recorded route. A failure in the
chain test means the chain changed; a failure in the router test alone means
the router no longer mirrors it. Either way, update the other side or the
corpus deliberately, never one to hide the other.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest

from backend.services import agent_brain as ab
from backend.services import turn_router as tr
from backend.services.slash_command_executor import resolve_slash_direct_tool
from backend.tests.fixtures.turn_router import harness

ROWS = harness.load_corpus()
IDS = [r["id"] for r in ROWS]

# Routes no chat turn reaches through AgentBrain today.
NOT_REACHED_BY_THE_CHAIN = {tr.Route.FILE_GENERATION, tr.Route.CLI_COMMAND}


@pytest.fixture
def image_dir(tmp_path):
    return str(tmp_path)


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_router_gives_the_recorded_route(row, image_dir):
    assert harness.router_outcome(row, image_dir) == row["expected"]


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_todays_chain_gives_the_recorded_route(row, image_dir):
    assert harness.chain_outcome(row, image_dir) == row["expected"]


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_the_route_came_from_a_trigger(row, image_dir):
    turn = harness.router_turn(row, image_dir)
    assert turn.route is turn.legacy_route
    assert turn.route in turn.candidates, turn.triggers
    assert turn.candidates[0] is tr.Route.CHAT
    assert turn.side_effect == tr.side_effect_for(turn.route, turn.route_args)


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_facts_follow_the_turn(row, image_dir):
    env = harness.row_env(row)
    options = row.get("options") or {}
    facts = harness.router_turn(row, image_dir).facts
    assert facts["has_image"] is bool(env["image"])
    assert facts["screen_active"] is bool(options.get("agent_screen_active"))
    assert facts["screen_viewer_open"] is bool(options.get("screen_viewer_open"))
    assert facts["image_focus"] is bool(env["image_focus"])
    assert facts["last_image"] is bool(env["last_image"])
    assert facts["has_recent_image"] is bool(env["image"] or (env["last_image"] and env["image_focus"]))
    assert facts["chars"] == len(row["message"])
    assert facts["slash_tool"] == resolve_slash_direct_tool(dict(options))[0]
    short = len(row["message"].strip()) <= 200
    assert bool(facts["pending_offer"]) is bool(short and ab.pending_offer(env["last_assistant_text"]))


def test_corpus_rows_are_well_formed():
    assert len(IDS) == len(set(IDS))
    allowed = {"id", "message", "source", "options", "env", "expected"}
    for row in ROWS:
        assert set(row) <= allowed and {"id", "message", "expected"} <= set(row), row["id"]
        assert set(row.get("env") or {}) <= set(harness.ENV_DEFAULTS), row["id"]
        assert set(row["expected"]) <= set(harness.OUTCOME_KEYS), row["id"]


def test_corpus_reaches_every_route_the_chain_has():
    seen = Counter(row["expected"]["route"] for row in ROWS)
    for route in set(tr.Route) - NOT_REACHED_BY_THE_CHAIN:
        assert seen[route.value] > 0, route


FORBIDDEN = [
    (re.compile(r"/home/|/Users/"), "a home directory path"),
    (re.compile(r"\b[A-Za-z]:\\"), "a Windows drive path"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), "an email address"),
    (re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), "an IPv4 literal"),
]


def test_corpus_holds_nothing_tied_to_a_machine_or_person():
    text = harness.CORPUS_PATH.read_text(encoding="utf-8")
    for pattern, what in FORBIDDEN:
        assert not pattern.search(text), what
