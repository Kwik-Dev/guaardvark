"""The declared decision questions are complete, in range, and switched off by default.

``wording_sha`` here is the fingerprint a MEASURED_ACCURACY row records: a row
holds only while the key, instructions and choices it was measured with are
unchanged.
"""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from backend.services.decision_data import (
    DECISIONS,
    GATE_FLOOR,
    LAYERS,
    MEASURED_ACCURACY,
    SIDE_EFFECTS,
    SOURCES,
    TYPES,
)
from backend.tests.fixtures.decision_sets import load_set

FIELDS = {
    "feature", "layer", "type", "key", "kind", "instructions", "choices", "state", "anchor", "precheck",
    "fallback", "safe_side", "side_effect", "mode_default", "floor", "set", "hard_negatives", "notes",
}
SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
GENERIC_KEYS = {
    "q", "a", "answer", "result", "output", "response", "decision", "label", "value", "choice", "yes", "ok",
}


def wording_sha(entry: dict) -> str:
    """First 12 hex digits of the sha256 of the wording a decision model sees."""
    payload = {"key": entry["key"], "instructions": entry["instructions"], "choices": entry["choices"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]


DECISION_IDS = sorted(DECISIONS)


@pytest.mark.parametrize("decision_id", DECISION_IDS)
def test_entry_has_exactly_the_declared_fields(decision_id):
    assert set(DECISIONS[decision_id]) == FIELDS


@pytest.mark.parametrize("decision_id", DECISION_IDS)
def test_enumerated_values_are_declared(decision_id):
    entry = DECISIONS[decision_id]
    assert entry["type"] in TYPES
    assert entry["layer"] in LAYERS
    assert entry["side_effect"] in SIDE_EFFECTS
    for field, spec in entry["state"].items():
        assert spec[0] in SOURCES, (field, spec)


@pytest.mark.parametrize("decision_id", DECISION_IDS)
def test_kind_and_choices_agree(decision_id):
    entry = DECISIONS[decision_id]
    assert entry["kind"] in ("bool", "choice")
    if entry["kind"] == "choice":
        assert isinstance(entry["choices"], dict) and len(entry["choices"]) >= 2
        assert all(isinstance(desc, str) and desc.strip() for desc in entry["choices"].values())
        assert entry["safe_side"] in entry["choices"]
    else:
        assert entry["choices"] is None
        assert isinstance(entry["safe_side"], bool)


@pytest.mark.parametrize("decision_id", DECISION_IDS)
def test_every_decision_ships_off(decision_id):
    assert DECISIONS[decision_id]["mode_default"] == "off"


@pytest.mark.parametrize("decision_id", DECISION_IDS)
def test_floor_in_range(decision_id):
    floor = DECISIONS[decision_id]["floor"]
    assert isinstance(floor, (int, float)) and not isinstance(floor, bool)
    assert 0 < floor <= 1


def test_public_posts_need_the_gate_floor():
    public = [k for k, v in DECISIONS.items() if v["side_effect"] == "public_post"]
    for decision_id in public:
        assert DECISIONS[decision_id]["floor"] >= GATE_FLOOR, decision_id


@pytest.mark.parametrize("decision_id", DECISION_IDS)
def test_state_fields_have_int_limits(decision_id):
    state = DECISIONS[decision_id]["state"]
    assert isinstance(state, dict) and state
    for field, spec in state.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", field), field
        assert isinstance(spec, tuple) and len(spec) == 2, (field, spec)
        max_chars = spec[1]
        assert isinstance(max_chars, int) and not isinstance(max_chars, bool) and max_chars > 0, (field, spec)


@pytest.mark.parametrize("decision_id", DECISION_IDS)
def test_answer_key_states_the_fact(decision_id):
    key = DECISIONS[decision_id]["key"]
    assert SNAKE_CASE.fullmatch(key), f"{key!r} is not multi-word snake_case"
    assert key not in GENERIC_KEYS
    assert re.fullmatch(r"[a-z][a-z0-9_]*", decision_id), decision_id


def test_measured_rows_match_current_wording():
    for decision_id, rows in MEASURED_ACCURACY.items():
        assert decision_id in DECISIONS, decision_id
        entry = DECISIONS[decision_id]
        current = wording_sha(entry)
        for row in rows:
            assert row["wording_sha"] == current, (
                f"{decision_id}: row measured on wording {row['wording_sha']}, entry is now {current}"
            )
            if "set_version" in row and entry.get("set"):
                assert row["set_version"] == load_set(entry["set"])["set_version"], decision_id


@pytest.mark.parametrize("decision_id", DECISION_IDS)
def test_wording_sha_tracks_only_the_wording(decision_id):
    entry = DECISIONS[decision_id]
    sha = wording_sha(entry)
    assert re.fullmatch(r"[0-9a-f]{12}", sha)
    assert wording_sha({**entry, "notes": "changed", "floor": 0.5}) == sha
    assert wording_sha({**entry, "instructions": entry["instructions"] + " "}) != sha
    assert wording_sha({**entry, "key": entry["key"] + "_x"}) != sha
