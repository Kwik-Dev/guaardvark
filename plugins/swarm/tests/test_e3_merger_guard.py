"""E3 — merger agent routes writes through the guarded-code chokepoint.

Also verifies enable_merger_agent now defaults OFF, and that a merge the
merger agent resolves is staged and committed, or aborted cleanly.
"""

import json
import os
import subprocess
import sys

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


# ---- attempt_merge with the merger agent ----------------------------------------
# A throwaway repo whose branch conflicts with main on one line. The merger is a
# stand-in, so no model runs; git itself does the merge, staging and commit.


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _commit_file(repo, text, message):
    (repo / "thing.py").write_text(text)
    _git(repo, "add", "thing.py")
    _git(repo, "commit", "-q", "-m", message)


def _make_conflicted_repo(tmp_path, monkeypatch, with_test=False):
    import service.merge_manager as merge_mod

    # Not the subject here, and it would read this machine's guard settings.
    monkeypatch.setattr(merge_mod, "_inbound_guard", lambda: None)

    repo = tmp_path / "repo"
    repo.mkdir()
    no_hooks = tmp_path / "no-hooks"
    no_hooks.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    for key, value in (("user.name", "Swarm Test"), ("user.email", "t@example.invalid"),
                       ("commit.gpgsign", "false"), ("core.hooksPath", str(no_hooks))):
        _git(repo, "config", key, value)
    if with_test:
        (repo / "tests").mkdir()
        (repo / "tests" / "test_thing.py").write_text("def test_a():\n    pass\n")
        _git(repo, "add", "tests/test_thing.py")
    _commit_file(repo, "a = 0\n", "base")
    _git(repo, "checkout", "-q", "-b", "swarm/t1")
    _commit_file(repo, "a = 2\n", "branch sets a to 2")
    _git(repo, "checkout", "-q", "main")
    _commit_file(repo, "a = 1\n", "main sets a to 1")
    return repo


@pytest.fixture
def conflicted_repo(tmp_path, monkeypatch):
    return _make_conflicted_repo(tmp_path, monkeypatch)


@pytest.fixture
def conflicted_repo_with_test(tmp_path, monkeypatch):
    """The conflicted module has a test file, tests/test_thing.py."""
    return _make_conflicted_repo(tmp_path, monkeypatch, with_test=True)


class _StubMerger:
    """Stands in for MergerAgent: writes a resolution, refuses, or raises."""

    def __init__(self, resolution="a = 2\n", resolved=True, raises=None):
        self.resolution = resolution
        self.resolved = resolved
        self.raises = raises
        self.seen = None

    def resolve_conflicts(self, repo_path, branch_name, conflict_files, title, description):
        self.seen = list(conflict_files)
        if self.raises:
            raise self.raises
        for rel in conflict_files:
            (repo_path / rel).write_text(self.resolution)
        return self.resolved


def _merge(repo, merger, **kwargs):
    from service.merge_manager import MergeManager
    from service.models import SwarmTask

    manager = MergeManager(repo, "main")
    manager._merger = merger
    task = SwarmTask(id="t1", title="Set a", description="a should be 2", branch_name="swarm/t1")
    return manager.attempt_merge(task, **kwargs), task


def _assert_not_mid_merge(repo):
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""


def test_resolved_conflict_is_staged_and_committed(conflicted_repo):
    from service.models import SwarmStatus

    merger = _StubMerger()
    result, task = _merge(conflicted_repo, merger)

    assert result.success is True, result.error
    assert task.status == SwarmStatus.MERGED
    assert merger.seen == ["thing.py"]
    assert (conflicted_repo / "thing.py").read_text() == "a = 2\n"
    parents = _git(conflicted_repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    assert len(parents) == 3, "a merge commit with both parents"
    _assert_not_mid_merge(conflicted_repo)


def test_a_merger_that_raises_leaves_no_merge_in_progress(conflicted_repo):
    from service.models import SwarmStatus

    head = _git(conflicted_repo, "rev-parse", "HEAD").stdout.strip()
    result, task = _merge(conflicted_repo, _StubMerger(raises=RuntimeError("model went away")))

    assert result.success is False
    assert "model went away" in result.error
    assert result.conflict_files == ["thing.py"]
    assert task.status == SwarmStatus.NEEDS_REVIEW
    assert _git(conflicted_repo, "rev-parse", "HEAD").stdout.strip() == head
    assert (conflicted_repo / "thing.py").read_text() == "a = 1\n"
    _assert_not_mid_merge(conflicted_repo)


def test_an_unresolved_conflict_is_aborted(conflicted_repo):
    from service.models import SwarmStatus

    head = _git(conflicted_repo, "rev-parse", "HEAD").stdout.strip()
    result, task = _merge(conflicted_repo, _StubMerger(resolved=False))

    assert result.success is False
    assert task.status == SwarmStatus.NEEDS_REVIEW
    assert _git(conflicted_repo, "rev-parse", "HEAD").stdout.strip() == head
    _assert_not_mid_merge(conflicted_repo)


# ---- tests on the resolved merge --------------------------------------------------

def _recording_test_command(tmp_path, exit_code):
    """A test command that records its arguments and working directory, then exits."""
    record = tmp_path / "test-run.json"
    script = tmp_path / "fake_test_runner.py"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(record)!r}, 'w').write(json.dumps({{'args': sys.argv[1:], 'cwd': os.getcwd()}}))\n"
        f"sys.exit({exit_code})\n"
    )

    def ran():
        return json.loads(record.read_text()) if record.exists() else None

    return f"{sys.executable} {script}", ran


def test_failing_tests_on_the_resolved_merge_abort_it(conflicted_repo_with_test, tmp_path):
    from service.models import SwarmStatus

    repo = conflicted_repo_with_test
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    command, ran = _recording_test_command(tmp_path, exit_code=1)

    result, task = _merge(repo, _StubMerger(), run_tests=True, test_command=command)

    assert ran() == {"args": ["tests/test_thing.py"], "cwd": str(repo.resolve())}
    assert result.success is False
    assert "Tests failed on the resolved merge" in result.error
    assert task.status == SwarmStatus.NEEDS_REVIEW
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head
    assert (repo / "thing.py").read_text() == "a = 1\n"
    _assert_not_mid_merge(repo)


def test_passing_tests_let_the_resolution_commit(conflicted_repo_with_test, tmp_path):
    from service.models import SwarmStatus

    repo = conflicted_repo_with_test
    command, ran = _recording_test_command(tmp_path, exit_code=0)

    result, task = _merge(repo, _StubMerger(), run_tests=True, test_command=command)

    assert ran() == {"args": ["tests/test_thing.py"], "cwd": str(repo.resolve())}
    assert result.success is True, result.error
    assert task.status == SwarmStatus.MERGED
    _assert_not_mid_merge(repo)


def test_a_resolution_no_test_covers_is_not_committed(conflicted_repo, tmp_path):
    from service.models import SwarmStatus

    head = _git(conflicted_repo, "rev-parse", "HEAD").stdout.strip()
    command, ran = _recording_test_command(tmp_path, exit_code=0)

    result, task = _merge(conflicted_repo, _StubMerger(), run_tests=True, test_command=command)

    assert ran() is None, "nothing to run, and never the whole suite"
    assert result.success is False
    assert "No tests cover" in result.error
    assert task.status == SwarmStatus.NEEDS_REVIEW
    assert _git(conflicted_repo, "rev-parse", "HEAD").stdout.strip() == head
    _assert_not_mid_merge(conflicted_repo)
