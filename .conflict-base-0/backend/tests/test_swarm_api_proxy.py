from flask import Flask
import pytest


def _app():
    pytest.importorskip("llama_index")
    from backend.api.swarm_api import swarm_bp

    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(swarm_bp)
    return app


def test_swarm_diff_proxy(monkeypatch):
    pytest.importorskip("llama_index")
    from backend.api import swarm_api

    seen = {}

    def fake_get(path, timeout=swarm_api.SWARM_TIMEOUT):
        seen["path"] = path
        return {"success": True, "diff": "diff --git a/x b/x"}, 200

    monkeypatch.setattr(swarm_api, "_proxy_get", fake_get)

    with _app().test_client() as client:
        response = client.get("/api/swarm/swarm-1/diff/task-1")

    assert response.status_code == 200
    assert seen["path"] == "/swarm/swarm-1/diff/task-1"


def test_swarm_bus_routes_proxy(monkeypatch):
    pytest.importorskip("llama_index")
    from backend.api import swarm_api

    seen = {}

    def fake_post(path, json_data=None, timeout=swarm_api.SWARM_TIMEOUT):
        seen["path"] = path
        seen["json"] = json_data
        return {"success": True}, 200

    monkeypatch.setattr(swarm_api, "_proxy_post", fake_post)

    with _app().test_client() as client:
        response = client.post(
            "/api/swarm/swarm-1/bus/broadcast",
            json={"sender": "agent", "event_type": "note", "data": {"ok": True}},
        )

    assert response.status_code == 200
    assert seen["path"] == "/swarm/swarm-1/bus/broadcast"
    assert seen["json"]["event_type"] == "note"


def test_self_code_swarm_launch_blocks_dirty_tree(tmp_path, monkeypatch):
    pytest.importorskip("llama_index")
    from backend.api import swarm_api

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.setattr(swarm_api, "default_repo_root", lambda: repo)

    class Result:
        stdout = " M backend/app.py\n"

    monkeypatch.setattr(swarm_api.subprocess, "run", lambda *args, **kwargs: Result())

    with _app().test_client() as client:
        response = client.post(
            "/api/swarm/launch",
            json={"self_code": True, "plan_path": "plan.md", "repo_path": str(repo)},
        )

    assert response.status_code == 409
