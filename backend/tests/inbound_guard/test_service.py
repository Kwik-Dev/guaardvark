"""The backend adapter: how one replacement becomes a Change, and what the gate lets through."""
import json
from pathlib import Path

import pytest

from backend.services import inbound_guard_service as guard

CASES = json.loads((Path(__file__).with_name("cases.json")).read_text())["cases"]


def _case(name):
    return next(c for c in CASES if c["name"] == name)


def test_replacement_reads_whole_lines_at_their_real_numbers():
    current = "a = 1\nb = 2\nvalue = compute(x)\nc = 3\n"
    change = guard.change_for_replacement("backend/x.py", current, "compute(x)", "compute(y)")
    assert change.added == [(3, "value = compute(y)")]
    assert change.removed == ["value = compute(x)"]
    assert change.new_text == current.replace("compute(x)", "compute(y)")


def test_replacement_spanning_lines():
    current = "head\nfirst\nsecond\ntail\n"
    change = guard.change_for_replacement("backend/x.py", current, "first\nsecond", "one\ntwo\nthree")
    assert [n for n, _ in change.added] == [2, 3, 4]


def _verdict(name, mode):
    case = _case(name)
    change = guard.change_for_file(case["path"], case["old"] or None, case["new"])
    return guard.engine().scan([change], source="self_code", subject=name, mode=mode)


@pytest.fixture
def nothing_approved(monkeypatch):
    monkeypatch.setattr(guard, "is_approved", lambda digest: False)


def test_gate_refuses_only_when_enforcing(nothing_approved):
    guard.gate(_verdict("shell through subprocess", "observe"))
    with pytest.raises(guard.InboundRefused) as held:
        guard.gate(_verdict("shell through subprocess", "enforce"))
    assert held.value.held and held.value.code == "INBOUND_HELD"


def test_a_persons_approval_answers_a_hold_but_not_a_block(nothing_approved):
    guard.gate(_verdict("shell through subprocess", "enforce"), human_approved=True)
    with pytest.raises(guard.InboundRefused) as blocked:
        guard.gate(_verdict("bidi control", "enforce"), human_approved=True)
    assert not blocked.value.held and blocked.value.code == "INBOUND_BLOCKED"


def test_an_approval_for_this_exact_change_passes_a_block(monkeypatch):
    verdict = _verdict("bidi control", "enforce")
    monkeypatch.setattr(guard, "is_approved", lambda digest: digest == verdict.digest)
    guard.gate(verdict)


def test_code_editor_holds_nothing_and_still_blocks():
    case = _case("shell through subprocess")
    change = guard.change_for_file(case["path"], None, case["new"])
    assert guard.engine().scan([change], source="code_editor", subject="x", mode="enforce").verdict == "allow"
    case = _case("bidi control")
    change = guard.change_for_file(case["path"], None, case["new"])
    assert guard.engine().scan([change], source="code_editor", subject="x", mode="enforce").verdict == "block"


def test_registries_take_and_drop_providers():
    guard.register_inbound_scanner("sample", lambda changes, ctx: [])
    guard.register_inbound_listener("sample", lambda event: None)
    assert "sample" in guard.registered()["scanners"]
    assert "sample" in guard.registered()["listeners"]
    assert guard.unregister_inbound_scanner("sample")
    assert guard.unregister_inbound_listener("sample")
    assert "code-index" in guard.registered()["listeners"]
