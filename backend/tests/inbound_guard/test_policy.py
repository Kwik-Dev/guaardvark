"""Modes and the severity thresholds that turn findings into a verdict."""
import subprocess

from scripts.inbound_guard.model import Finding
from scripts.inbound_guard.policy import decide, git_mode, normalize_mode

POLICY = {"hold_at": "medium", "block_at": "critical"}


def _f(severity):
    return Finding("r", "p", severity, "x.py", 1, "", "why")


def test_thresholds():
    assert decide([], POLICY, "cli") == "allow"
    assert decide([_f("low"), _f("info")], POLICY, "cli") == "allow"
    assert decide([_f("medium")], POLICY, "cli") == "hold"
    assert decide([_f("high"), _f("critical")], POLICY, "cli") == "block"


def test_per_source_override():
    policy = dict(POLICY, sources={"swarm": {"hold_at": "low"}})
    assert decide([_f("low")], policy, "swarm") == "hold"
    assert decide([_f("low")], policy, "cli") == "allow"


def test_unknown_mode_observes_rather_than_disabling_or_refusing():
    assert normalize_mode(None) == "off"
    assert normalize_mode("ENFORCE") == "enforce"
    assert normalize_mode("enforec") == "observe"


def test_environment_wins_over_git_config(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "inboundguard.mode", "enforce"], cwd=tmp_path, check=True)
    monkeypatch.delenv("GUAARDVARK_INBOUND_GUARD", raising=False)
    assert git_mode(tmp_path) == "enforce"
    monkeypatch.setenv("GUAARDVARK_INBOUND_GUARD", "off")
    assert git_mode(tmp_path) == "off"
