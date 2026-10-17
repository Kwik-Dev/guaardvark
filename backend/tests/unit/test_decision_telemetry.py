"""The decision telemetry writer: question rows, hashing, failures and the size cap."""
from __future__ import annotations

import json
import os

import pytest

from backend.services import decision_telemetry as dt


@pytest.fixture
def telemetry_file(tmp_path, monkeypatch):
    path = tmp_path / "decision_telemetry.jsonl"
    monkeypatch.setattr(dt, "TELEMETRY_PATH", str(path))
    monkeypatch.setenv(dt.ENV_SWITCH, "true")
    return path


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]



def test_question_row_hashes_state_unless_llm_debug(telemetry_file):
    state = {"assistant_offer": "Shall I search your files?", "user_reply": "sure, the PDFs"}
    assert dt.write_question(
        "accepted_offer", request_id="r1", mode="shadow", model="m", digest="abc", backend="chat",
        state=state, llm_answer=True, llm_confidence=1.0, fallback_answer=False, used="fallback",
        reason="shadow", latency_ms=310, llm_debug=False,
    )
    (row,) = _rows(telemetry_file)
    assert {"kind", "ts", "request_id", "decision", "mode", "model", "digest", "backend", "state_sha256",
            "state_chars", "llm_answer", "llm_confidence", "fallback_answer", "agree", "used", "reason",
            "latency_ms"} <= set(row)
    assert row["kind"] == "question" and row["agree"] is False
    assert row["state_sha256"] == dt.state_sha256(state) and len(row["state_sha256"]) == 16
    assert row["state_chars"] == sum(len(v) for v in state.values())
    assert "state" not in row and "PDFs" not in json.dumps(row)

    dt.write_question("accepted_offer", state=state, llm_debug=True)
    assert _rows(telemetry_file)[1]["state"] == state
    assert _rows(telemetry_file)[1]["agree"] is None


def test_a_failed_write_is_swallowed(tmp_path, monkeypatch):
    monkeypatch.setenv(dt.ENV_SWITCH, "true")
    monkeypatch.setattr(dt, "TELEMETRY_PATH", str(tmp_path))  # a directory: open() fails
    assert dt.append_row({"kind": "turn"}) is False
    assert dt.write_question("needs_live_data", state={"message": "x"}, llm_debug=False) is False


def test_the_switch_turns_writing_off(telemetry_file, monkeypatch):
    monkeypatch.setenv(dt.ENV_SWITCH, "false")
    assert dt.append_row({"kind": "turn"}) is False
    assert not telemetry_file.exists()


def test_the_file_rotates_at_its_cap(telemetry_file, monkeypatch):
    monkeypatch.setattr(dt, "MAX_BYTES", 200)
    for i in range(40):
        dt.append_row({"kind": "turn", "n": i, "pad": "x" * 40})
    assert telemetry_file.exists()
    assert os.path.exists(f"{telemetry_file}.1") and os.path.exists(f"{telemetry_file}.2")
    assert not os.path.exists(f"{telemetry_file}.3")
    assert os.path.getsize(telemetry_file) < 200 + 100

def test_a_shadow_question_is_written_with_the_switch_off(telemetry_file, monkeypatch):
    monkeypatch.setenv(dt.ENV_SWITCH, "false")
    assert dt.write_question("needs_live_data", mode="off", state={"message": "x"}) is False
    assert dt.write_question("needs_live_data", mode="shadow", state={"message": "x"}) is True
    rows = [json.loads(line) for line in telemetry_file.read_text().splitlines()]
    assert [r["mode"] for r in rows] == ["shadow"]
