"""What a finished, empty, stuck or committed agent run turns into.

Real git repositories in a temporary directory and the real orchestrator,
worktree manager and Claude backend wrapper; only the agent CLI is a small
shell script that commits, does nothing, or sleeps.
"""

import subprocess
import time
from pathlib import Path

import pytest

import service.orchestrator as orch
from service.config import BackendConfig, SwarmConfig
from service.models import SwarmStatus

PLAN = "# Swarm Plan: test\n\n## Task: Add a file\n- depends_on: none\n\nAdd hello.txt.\n"

COMMITS = """#!/bin/bash
echo hello > hello.txt
git add hello.txt
git -c user.name=t -c user.email=t@t commit -q -m "add hello"
echo "done"
"""
NOTHING = "#!/bin/bash\necho 'I looked around and changed nothing.'\n"
LEAVES_UNCOMMITTED_FILE = "#!/bin/bash\necho hi > new_file.txt\n"
SLEEPS = "#!/bin/bash\nsleep 60\n"


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    (tmp_path / "plan.md").write_text(PLAN)
    return repo


@pytest.fixture(autouse=True)
def fast_loop(monkeypatch):
    monkeypatch.setattr(orch, "POLL_INTERVAL", 0.2)
    # The freeze guard reads this machine's free RAM; a small test box must not
    # withhold the spawn.
    monkeypatch.setattr(orch, "_spawn_freeze_guard_block_reason", lambda: None)

    class _Healthy:
        def is_healthy(self):
            return True

        def get_system_stats(self):
            return {}

    monkeypatch.setattr(orch, "get_resource_monitor", lambda: _Healthy())
    # No event pushes to a backend that is not running.
    monkeypatch.setattr(orch.SwarmOrchestrator, "_emit_event", lambda self, *a, **k: None)


def _run(repo, tmp_path, script, timeout_minutes=120):
    agent = tmp_path / "fake-agent"
    agent.write_text(script)
    agent.chmod(0o755)
    config = SwarmConfig(
        max_concurrent_agents=1,
        auto_merge=False,
        enable_diagnostic_agent=False,
        agent_timeout_minutes=timeout_minutes,
        backends={"claude": BackendConfig(name="claude", command=str(agent), args=[],
                                         requires_internet=False, priority=1)},
    )
    o = orch.SwarmOrchestrator(repo, config)
    o.max_retries = 0
    started = time.time()
    result = o.launch(tmp_path / "plan.md", auto_merge=False)
    return o, result.tasks[0], time.time() - started


def test_a_committed_change_is_done_and_shows_in_the_diff(repo, tmp_path):
    o, task, _ = _run(repo, tmp_path, COMMITS)
    assert task.status == SwarmStatus.DONE, task.error
    diff = o.get_task_diff(task.id)
    assert "+hello" in diff and "hello.txt" in diff


def test_a_clean_exit_with_nothing_done_says_no_changes(repo, tmp_path):
    o, task, _ = _run(repo, tmp_path, NOTHING)
    assert task.status == SwarmStatus.NO_CHANGES
    assert "without committing or changing anything" in task.error
    assert o.get_task_diff(task.id) == "(no changes in worktree)"


def test_an_uncommitted_new_file_needs_review_and_is_listed(repo, tmp_path):
    o, task, _ = _run(repo, tmp_path, LEAVES_UNCOMMITTED_FILE)
    assert task.status == SwarmStatus.NEEDS_REVIEW
    diff = o.get_task_diff(task.id)
    assert "new_file.txt" in diff
    # The swarm's own bookkeeping files are not the agent's work.
    assert ".swarm-run.sh" not in diff and "completion.md" not in diff


def test_an_agent_past_the_limit_is_stopped_and_timed_out(repo, tmp_path):
    o, task, took = _run(repo, tmp_path, SLEEPS, timeout_minutes=1 / 60)  # one second
    assert task.status == SwarmStatus.TIMED_OUT
    assert "limit" in task.error and "stopped" in task.error
    assert took < 30, "the run waited for the agent instead of stopping it"
    # The wrapper and its sleeping child are gone.
    pid = task.agent_pid
    time.sleep(0.5)
    alive = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    assert alive == "" or alive.startswith("Z")


def test_timeout_is_declared_in_the_config():
    from service.config import load_config

    assert load_config().agent_timeout_minutes == 120


def test_a_backend_whose_cli_is_missing_is_not_installed(tmp_path, monkeypatch):
    from service.agent_backends import cline_backend

    missing = BackendConfig(name="claude", command=str(tmp_path / "no-such-cli"))
    assert orch.backend_installed("claude", missing) is False

    agent = tmp_path / "claude-cli"
    agent.write_text("#!/bin/bash\n")
    agent.chmod(0o755)
    assert orch.backend_installed("claude", BackendConfig(name="claude", command=str(agent))) is True

    # cline also accepts other CLI names (openclaw, cline-cli): its own check decides.
    monkeypatch.setattr(cline_backend.shutil, "which", lambda cmd: "/bin/openclaw" if cmd == "openclaw" else None)
    assert orch.backend_installed("cline", BackendConfig(name="cline", command="cline")) is True


def test_connectivity_marks_an_uninstalled_backend_unavailable(tmp_path, monkeypatch):
    app_module = pytest.importorskip("service.app")
    monkeypatch.setattr(app_module, "check_internet", lambda *a, **k: True)
    monkeypatch.setattr(app_module, "_config", SwarmConfig(backends={
        "claude": BackendConfig(name="claude", command=str(tmp_path / "missing"), requires_internet=True),
    }))
    monkeypatch.setattr(orch, "_backend_classes", lambda: {})
    row = app_module.check_connectivity()["backends"][0]
    assert row["installed"] is False and row["available"] is False
