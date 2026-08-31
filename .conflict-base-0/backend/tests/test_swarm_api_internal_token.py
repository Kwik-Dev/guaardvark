"""E2 — the Flask swarm proxy attaches the shared internal token on every call."""

import pytest
from flask import Flask


def _app():
    from backend.api.swarm_api import swarm_bp

    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(swarm_bp)
    return app


def test_proxy_get_attaches_internal_token(monkeypatch):
    from backend.api import swarm_api

    monkeypatch.setattr(swarm_api, "_internal_secret", lambda: "secret-abc")

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"success": True, "swarms": [], "count": 0}

    def fake_get(url, params=None, timeout=None, headers=None):
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(swarm_api.requests, "get", fake_get)

    with _app().test_client() as client:
        resp = client.get("/api/swarm/status")

    assert resp.status_code == 200
    assert captured["headers"][swarm_api.INTERNAL_TOKEN_HEADER] == "secret-abc"


def test_proxy_post_attaches_internal_token(monkeypatch):
    from backend.api import swarm_api

    monkeypatch.setattr(swarm_api, "_internal_secret", lambda: "secret-xyz")

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"success": True, "message": "ok"}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(swarm_api.requests, "post", fake_post)

    with _app().test_client() as client:
        resp = client.post("/api/swarm/cancel", json={"swarm_id": "s1"})

    assert resp.status_code == 200
    assert captured["headers"][swarm_api.INTERNAL_TOKEN_HEADER] == "secret-xyz"
