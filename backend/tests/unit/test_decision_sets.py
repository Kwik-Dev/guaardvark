"""The labelled sets under backend/tests/fixtures/decisions fit the decisions they score.

Each set must answer only with the decision's options, carry exactly its state
fields within their size limits, be large enough to measure against a floor,
cover every hard negative the decision names, and hold nothing tied to a
machine or a person.
"""
from __future__ import annotations

import json
import re

import pytest

from backend.services.decision_data import DECISIONS
from backend.tests.fixtures.decision_sets import BASE_TAGS, list_sets, load_set, scored_items

SET_DECISIONS = sorted(k for k, v in DECISIONS.items() if v.get("set"))

MIN_SCORED = 18
MIN_SCORED_BY_DECISION = {"outreach_thread_fit": 30}

# decision -> {hard-negative tag: why no item can carry it yet}. Add items before adding a row here.
KNOWN_TAG_GAPS: dict[str, dict[str, str]] = {}

ITEM_KEYS = {"id", "state", "want", "tags"}
OPTIONAL_ITEM_KEYS = {"note"}

FORBIDDEN = [
    (re.compile(r"/home/|/Users/"), "a home directory path"),
    (re.compile(r"\b[A-Za-z]:\\"), "a Windows drive path"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), "an email address"),
    (re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), "an IPv4 literal"),
    (re.compile(r"localhost", re.IGNORECASE), "a local host name"),
    (re.compile(r"guaardvark", re.IGNORECASE), "the project's own name (sets stay generic)"),
]


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _strings(value)


def _set(decision_id: str) -> dict:
    return load_set(DECISIONS[decision_id]["set"])


def test_every_fixture_belongs_to_a_decision():
    for name in list_sets():
        assert name in DECISIONS, f"{name}.json has no entry in DECISIONS"
        assert DECISIONS[name].get("set") == f"{name}.json"


@pytest.mark.parametrize("decision_id", SET_DECISIONS)
def test_fixture_exists_and_names_its_decision(decision_id):
    assert DECISIONS[decision_id]["set"] == f"{decision_id}.json"
    assert decision_id in list_sets(), f"missing fixture {DECISIONS[decision_id]['set']}"
    data = _set(decision_id)
    assert data["decision"] == decision_id
    assert isinstance(data["set_version"], int) and not isinstance(data["set_version"], bool)
    assert data["set_version"] >= 1
    assert isinstance(data["provenance"], str) and data["provenance"].strip()


@pytest.mark.parametrize("decision_id", SET_DECISIONS)
def test_options_match_the_decision(decision_id):
    entry, data = DECISIONS[decision_id], _set(decision_id)
    if entry["kind"] == "bool":
        assert data["options"] == [True, False]
    else:
        assert len(data["options"]) == len(set(data["options"]))
        assert set(data["options"]) == set(entry["choices"])


@pytest.mark.parametrize("decision_id", SET_DECISIONS)
def test_items_are_well_formed(decision_id):
    entry, data = DECISIONS[decision_id], _set(decision_id)
    allowed_tags = BASE_TAGS | set(entry["hard_negatives"])
    ids = [it["id"] for it in data["items"]]
    assert len(ids) == len(set(ids)), "item ids must be unique"
    for it in data["items"]:
        assert ITEM_KEYS <= set(it) <= ITEM_KEYS | OPTIONAL_ITEM_KEYS, it["id"]
        assert isinstance(it["id"], str) and it["id"], it
        assert it["want"] is None or it["want"] in data["options"], (it["id"], it["want"])
        assert isinstance(it["tags"], list) and it["tags"], it["id"]
        assert set(it["tags"]) & BASE_TAGS, f"{it['id']} needs one of {sorted(BASE_TAGS)}"
        assert not set(it["tags"]) - allowed_tags, (it["id"], set(it["tags"]) - allowed_tags)
        assert ("ambiguous" in it["tags"]) == (it["want"] is None), (
            f"{it['id']}: an item is tagged ambiguous exactly when its want is null"
        )


@pytest.mark.parametrize("decision_id", SET_DECISIONS)
def test_state_keys_match_and_fit(decision_id):
    entry, data = DECISIONS[decision_id], _set(decision_id)
    for it in data["items"]:
        assert set(it["state"]) == set(entry["state"]), (it["id"], sorted(it["state"]))
        for field, value in it["state"].items():
            max_chars = entry["state"][field][1]
            if isinstance(value, str):
                size = len(value)
            else:
                assert isinstance(value, bool), (it["id"], field, value)
                size = len(json.dumps(value))
            assert size <= max_chars, f"{it['id']}.{field} is {size} chars, limit {max_chars}"


@pytest.mark.parametrize("decision_id", SET_DECISIONS)
def test_enough_scored_items(decision_id):
    need = MIN_SCORED_BY_DECISION.get(decision_id, MIN_SCORED)
    assert len(scored_items(_set(decision_id))) >= need


@pytest.mark.parametrize("decision_id", SET_DECISIONS)
def test_every_hard_negative_is_covered(decision_id):
    data = _set(decision_id)
    if data.get("partial"):
        pytest.skip(f"{decision_id} is marked partial")
    gaps = KNOWN_TAG_GAPS.get(decision_id, {})
    covered = {tag for it in scored_items(data) for tag in it["tags"]}
    missing = [t for t in DECISIONS[decision_id]["hard_negatives"] if t not in covered and t not in gaps]
    assert not missing, f"no scored item carries {missing}"


def test_injection_probes_present_when_named():
    named = [d for d in SET_DECISIONS if "injection" in DECISIONS[d]["hard_negatives"]]
    for decision_id in named:
        assert any("injection" in it["tags"] for it in scored_items(_set(decision_id))), decision_id


def test_known_tag_gaps_name_real_tags():
    for decision_id, gaps in KNOWN_TAG_GAPS.items():
        assert decision_id in DECISIONS
        for tag, reason in gaps.items():
            assert tag in DECISIONS[decision_id]["hard_negatives"] and tag != "injection", (decision_id, tag)
            assert reason.strip()


@pytest.mark.parametrize("decision_id", SET_DECISIONS)
def test_nothing_machine_specific(decision_id):
    data = _set(decision_id)
    for text in _strings(data):
        for pattern, what in FORBIDDEN:
            assert not pattern.search(text), f"{decision_id}: {what} in {text[:80]!r}"
