"""E3 — merger agent routes writes through the guarded-code chokepoint.

Also verifies enable_merger_agent now defaults OFF.
"""

import os

import pytest


def test_load_config_default_merger_agent_off(tmp_path):
    """With no config.yaml override, the merger agent is disabled by default."""
    from service.config import SwarmConfig, load_config

    # Dataclass default
    assert SwarmConfig().enable_merger_agent is False

    # And a config file that doesn't set it keeps it off.
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("defaults:\n  max_concurrent_agents: 3\n")
    cfg = load_config(cfg_path)
    assert cfg.enable_merger_agent is False


def test_merger_routes_through_guarded_service(tmp_path, monkeypatch):
    """Resolution writes go through apply_exact_replacement, never raw git add."""
    import backend.services.guarded_code_service as gcs
    from service.merger_agent import MergerAgent

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    target = repo / "src" / "thing.py"
    conflicting = "<<<<<<< HEAD\na = 1\n=======\na = 2\n>>>>>>> branch\n"
    target.write_text(conflicting)

    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))

    # Fake the LLM call so no model is invoked.
    class FakeLLMResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "a = 2\n"}

    import service.merger_agent as merger_mod

    monkeypatch.setattr(merger_mod.requests, "post", lambda *a, **k: FakeLLMResp())

    # Capture apply_exact_replacement calls.
    calls = []

    def fake_apply(path, old_text=None, new_text=None, repo_root=None, **kwargs):
        calls.append({"path": path, "old": old_text, "new": new_text, "root": repo_root})
        # Mimic the real apply_exact_replacement: it writes the resolved content.
        from pathlib import Path as _P

        _P(path).write_text(new_text)

        class R:
            pass

        return R()

    monkeypatch.setattr(gcs, "apply_exact_replacement", fake_apply)

    # Guard against any raw subprocess (git add) escaping.
    import subprocess as real_subprocess

    def boom(*a, **k):
        raise AssertionError(f"unexpected subprocess call: {a}")

    monkeypatch.setattr(real_subprocess, "run", boom)

    agent = MergerAgent("http://localhost:5000/api")
    ok = agent.resolve_conflicts(
        repo, "branch-x", ["src/thing.py"], "Fix thing", "make a == 2"
    )

    assert ok is True
    assert len(calls) == 1
    assert calls[0]["old"] == conflicting
    assert calls[0]["new"] == "a = 2\n"
    assert calls[0]["root"] == str(repo.resolve())


def test_merger_returns_false_on_guarded_error(tmp_path, monkeypatch):
    """A GuardedCodeError -> log + return False (NEEDS_REVIEW), no crash."""
    import backend.services.guarded_code_service as gcs
    from service.merger_agent import MergerAgent

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    target = repo / "src" / "thing.py"
    target.write_text("<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> b\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))

    class FakeLLMResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "x\n"}

    import service.merger_agent as merger_mod

    monkeypatch.setattr(merger_mod.requests, "post", lambda *a, **k: FakeLLMResp())

    def raise_guard(*a, **k):
        raise gcs.GuardedCodeError("protected", "PROTECTED_FILE", 403)

    monkeypatch.setattr(gcs, "apply_exact_replacement", raise_guard)

    agent = MergerAgent("http://localhost:5000/api")
    ok = agent.resolve_conflicts(repo, "b", ["src/thing.py"], "t", "d")
    assert ok is False


def test_merger_blocks_outside_repo_root(tmp_path, monkeypatch):
    """Files outside the repo root are refused (return False), no write."""
    from service.merger_agent import MergerAgent

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "thing.py"
    target.write_text("<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> b\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))

    class FakeLLMResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "x\n"}

    import service.merger_agent as merger_mod

    monkeypatch.setattr(merger_mod.requests, "post", lambda *a, **k: FakeLLMResp())

    agent = MergerAgent("http://localhost:5000/api")
    # rel_path resolves outside repo root
    ok = agent._resolve_file(outside, "thing.py", "t", "d")
    assert ok is False
