"""E4: task_logs disk fallback.

When no orchestrator is in memory, the logs endpoint must load the existing
worktree from disk and tail its .swarm-agent.log, returning 200 + the tail.
A truly-missing worktree still 404s.
"""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

import service.app as app_module
from service.app import app, _AGENT_LOG_FILE

# E2 added an internal-token middleware; these endpoints now require the header.
_SECRET = "test-secret-tasklogs"
_HDRS = {"X-Swarm-Internal-Token": _SECRET}


def _init_repo(repo):
    """git init + an initial commit so `git rev-parse HEAD` resolves."""
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A git repo with a swarm worktree + manifest + agent log on disk."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    swarm_id = "swarm-test-123"
    task_id = "build-thing"
    worktree_base = ".swarm-worktrees"
    swarm_dir = repo / worktree_base / swarm_id
    wt_path = swarm_dir / task_id
    wt_path.mkdir(parents=True)

    # the agent log the endpoint should tail
    log_lines = [f"line {i}" for i in range(1, 21)]
    (wt_path / _AGENT_LOG_FILE).write_text("\n".join(log_lines), encoding="utf-8")

    # manifest WorktreeManager.load_existing reads
    manifest = {
        "swarm_id": swarm_id,
        "repo_path": str(repo),
        "base_branch": "main",
        "worktrees": {
            task_id: {
                "task_id": task_id,
                "swarm_id": swarm_id,
                "branch_name": f"swarm/{swarm_id}/{task_id}",
                "worktree_path": str(wt_path),
                "created": True,
            }
        },
    }
    (swarm_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # point the app at this repo, and ensure no orchestrator is tracked
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("SWARM_INTERNAL_SECRET", _SECRET)
    monkeypatch.setattr(app_module, "_internal_secret", _SECRET, raising=False)
    monkeypatch.setattr(app_module, "_active_orchestrators", {}, raising=True)

    return repo, swarm_id, task_id, log_lines


def test_logs_fallback_tails_disk_log(fake_repo):
    repo, swarm_id, task_id, log_lines = fake_repo
    client = TestClient(app)

    resp = client.get(f"/swarm/{swarm_id}/logs/{task_id}", params={"lines": 5}, headers=_HDRS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["task_id"] == task_id
    # tail of 5
    assert data["logs"] == "\n".join(log_lines[-5:])


def test_logs_fallback_no_logfile(tmp_path, monkeypatch):
    """Worktree exists but no log file -> 200 with '(no log file)'."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    swarm_id = "swarm-empty"
    task_id = "t1"
    swarm_dir = repo / ".swarm-worktrees" / swarm_id
    wt_path = swarm_dir / task_id
    wt_path.mkdir(parents=True)  # no .swarm-agent.log inside

    manifest = {
        "swarm_id": swarm_id, "repo_path": str(repo), "base_branch": "main",
        "worktrees": {task_id: {
            "task_id": task_id, "swarm_id": swarm_id,
            "branch_name": f"swarm/{swarm_id}/{task_id}",
            "worktree_path": str(wt_path), "created": True,
        }},
    }
    (swarm_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("SWARM_INTERNAL_SECRET", _SECRET)
    monkeypatch.setattr(app_module, "_internal_secret", _SECRET, raising=False)
    monkeypatch.setattr(app_module, "_active_orchestrators", {}, raising=True)

    client = TestClient(app)
    resp = client.get(f"/swarm/{swarm_id}/logs/{task_id}", headers=_HDRS)
    assert resp.status_code == 200
    assert resp.json()["logs"] == "(no log file)"


def test_logs_missing_worktree_404(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)

    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("SWARM_INTERNAL_SECRET", _SECRET)
    monkeypatch.setattr(app_module, "_internal_secret", _SECRET, raising=False)
    monkeypatch.setattr(app_module, "_active_orchestrators", {}, raising=True)

    client = TestClient(app)
    resp = client.get("/swarm/does-not-exist/logs/nope", headers=_HDRS)
    assert resp.status_code == 404
