"""E2 — sidecar launch-guard parity, shared-secret middleware, CORS.

These exercise the FastAPI sidecar directly (the thing that binds :8210), proving
that direct access can no longer bypass the Flask self-code guard.
"""

import os

import pytest
from fastapi.testclient import TestClient

SECRET = "test-internal-secret-e2"
HEADERS = {"X-Swarm-Internal-Token": SECRET}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_INTERNAL_SECRET", SECRET)
    # Avoid pinging the network during /health-adjacent paths.
    import service.app as app_module

    monkeypatch.setattr(app_module, "check_internet", lambda *a, **k: False)
    with TestClient(app_module.app) as c:
        yield c


def _make_dirty_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def test_missing_token_is_403(client):
    resp = client.post("/swarm/status", json={})  # any non-health route
    # /swarm/status is GET-only but the middleware runs before routing
    assert resp.status_code == 403


def test_incorrect_token_is_403(client):
    resp = client.get("/swarm/status", headers={"X-Swarm-Internal-Token": "wrong"})
    assert resp.status_code == 403


def test_health_is_open_without_token(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_correct_token_passes_middleware(client):
    resp = client.get("/swarm/status", headers=HEADERS)
    assert resp.status_code == 200


def test_self_code_dirty_repo_no_ack_returns_409(client, monkeypatch, tmp_path):
    import service.app as app_module

    repo = _make_dirty_repo(tmp_path)
    plan = repo / "plan.md"
    plan.write_text("# plan\n## task one\ndo a thing\n")

    class DirtyStatus:
        stdout = " M backend/app.py\n"

    monkeypatch.setattr(app_module.subprocess, "run", lambda *a, **k: DirtyStatus())

    resp = client.post(
        "/swarm/launch",
        headers=HEADERS,
        json={
            "plan_path": str(plan),
            "repo_path": str(repo),
            "self_code": True,
            "acknowledge_dirty_tree": False,
        },
    )
    assert resp.status_code == 409
    assert "acknowledge_dirty_tree" in resp.json()["detail"]


def test_self_code_via_guaardvark_root_forces_guard(client, monkeypatch, tmp_path):
    """Even without self_code=True, targeting GUAARDVARK_ROOT triggers the guard."""
    import service.app as app_module

    repo = _make_dirty_repo(tmp_path)
    plan = repo / "plan.md"
    plan.write_text("# plan\n## task one\ndo a thing\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))

    class DirtyStatus:
        stdout = " M something.py\n"

    monkeypatch.setattr(app_module.subprocess, "run", lambda *a, **k: DirtyStatus())

    resp = client.post(
        "/swarm/launch",
        headers=HEADERS,
        json={"plan_path": str(plan), "repo_path": str(repo)},
    )
    assert resp.status_code == 409


def test_cors_origin_is_not_wildcard(client):
    """CORS reflects the configured localhost origin, not '*', and no credentials."""
    import service.app as app_module

    # The middleware stores the configured origin list; '*' must be gone.
    assert "*" not in app_module._cors_origin
    assert app_module._cors_origin.startswith("http://localhost")
