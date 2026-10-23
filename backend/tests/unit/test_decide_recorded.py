"""Real model replies, replayed through the reply path decide() uses.

backend/tests/fixtures/decisions/recorded/<decision>.json holds what named
builds actually answered to each labelled item, recorded by
``scripts/eval_decisions.py --record``, with what the parser made of each
reply then. Replaying them through json_chat_result and ask_question (a stub
Ollama client hands back the recorded text) must give the same reading. Hand-
written stubs miss shapes like these: a choice echoed with its description
("status: A question about ..."), or the prompt's own placeholder copied in
as a key ("how sure you are").

A recording is tied to the wording and labelled set it was taken on; when
either changes, record again rather than editing the file.
"""
from __future__ import annotations

import json
from pathlib import Path

import ollama
import pytest

from backend.services import nl_control_plane as ncp
from backend.services.decision_data import DECISIONS
from backend.services.decision_prompt import wording_sha
from backend.tests.fixtures.decision_sets import load_set

RECORDED_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "decisions" / "recorded"
RECORDINGS = sorted(RECORDED_DIR.glob("*.json"))
NOT_REPLAYABLE = {"timeout", "error"}   # no reply text came back


def _replies():
    for path in RECORDINGS:
        data = json.loads(path.read_text(encoding="utf-8"))
        for run in data["runs"]:
            for reply in run["replies"]:
                if reply["reason"] not in NOT_REPLAYABLE:
                    yield pytest.param(data["decision"], reply, id=f"{data['decision']}-{run['digest']}-{reply['id']}")


def test_there_are_recordings():
    assert RECORDINGS, "record with scripts/eval_decisions.py --record"


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda p: p.stem)
def test_a_recording_matches_the_current_wording_and_set(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = DECISIONS[data["decision"]]
    assert data["wording_sha"] == wording_sha(entry), "the wording changed: record again"
    assert data["set_version"] == load_set(entry["set"])["set_version"], "the labelled set changed: record again"
    item_ids = {item["id"] for item in load_set(entry["set"])["items"]}
    for run in data["runs"]:
        assert len(run["digest"]) == 12 and run["model"]
        assert {reply["id"] for reply in run["replies"]} <= item_ids


@pytest.fixture
def replay(monkeypatch):
    """A stub Ollama client that answers with whatever text the test sets."""
    from backend.utils import ollama_resource_manager as orm

    text = {"raw": ""}

    class Client:
        def __init__(self, host=None, timeout=None):
            pass

        def chat(self, **kwargs):
            return {"message": {"content": text["raw"]}}

    monkeypatch.setattr(ollama, "Client", Client)
    monkeypatch.setattr(ncp, "_clients", {})
    monkeypatch.setattr(orm, "get_model_info", lambda *a, **k: None)
    return text


@pytest.mark.parametrize("decision_id, reply", list(_replies()))
def test_a_recorded_reply_reads_the_same_today(replay, decision_id, reply):
    entry = DECISIONS[decision_id]
    item = next(i for i in load_set(entry["set"])["items"] if i["id"] == reply["id"])
    replay["raw"] = reply["raw"]
    asked = ncp.ask_question(entry, item["state"], model="recorded:replay", timeout_s=5.0, num_ctx=8192)
    assert asked.reason == reply["reason"]
    if asked.reason == "ok":
        assert asked.answer == reply["answer"]
        assert type(asked.answer) is type(item["want"])
