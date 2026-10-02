"""GET /api/settings/security/check reports the running install's guards and
exposure instead of failing on a missing module, and never returns a key.

The real settings blueprint and security_summary with an in-memory database;
the socket table is replaced where a test needs a particular exposure.
"""

from __future__ import annotations

import pytest
from flask import Flask

from backend.services import security_summary as summary

KEY = "unit-test-key-value-that-must-not-appear"


@pytest.fixture
def client(monkeypatch):
    from backend.api.settings_api import settings_bp
    from backend.models import db

    for name in ("GUAARDVARK_API_KEY", "GUAARDVARK_PROTECT_TOOL_ENDPOINTS",
                 "VITE_ALLOWED_HOSTS", "GUAARDVARK_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(settings_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _checks(client) -> tuple[dict, dict]:
    resp = client.get("/api/settings/security/check")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()["data"]
    return data, {c["id"]: c for c in data["checks"]}


def test_the_check_answers_and_counts_its_warnings(client):
    data, checks = _checks(client)
    for check_id in ("api_key", "tool_endpoints", "host_check", "cors_origins", "debug",
                     "web_access", "tool_paths"):
        assert check_id in checks
    warns = [c for c in data["checks"] if c["status"] == "warn"]
    assert data["warning_count"] == len(warns) == len(data["warnings"])
    assert data["security_level"] == ("high" if not warns else "medium" if len(warns) < 3 else "low")


def test_a_configured_key_is_reported_but_never_returned(client, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", KEY)
    resp = client.get("/api/settings/security/check")
    assert KEY not in resp.get_data(as_text=True)
    assert {c["id"]: c for c in resp.get_json()["data"]["checks"]}["api_key"]["status"] == "ok"


def test_weakened_guards_are_warnings(client, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_PROTECT_TOOL_ENDPOINTS", "false")
    monkeypatch.setenv("VITE_ALLOWED_HOSTS", "all")
    monkeypatch.setenv("GUAARDVARK_CORS_ORIGINS", "https://ui.example.lan")
    _, checks = _checks(client)
    assert checks["tool_endpoints"]["status"] == "warn"
    assert checks["host_check"]["status"] == "warn"
    assert checks["cors_origins"]["status"] == "info"
    assert "ui.example.lan" not in checks["cors_origins"]["detail"]


@pytest.mark.parametrize("addresses, expected", [
    (set(), "none"),
    ({"127.0.0.1", "::1"}, "loopback"),
    ({"0.0.0.0"}, "network"),
    ({"::"}, "network"),
    ({"127.0.0.1", "192.168.1.20"}, "network"),
])
def test_exposure_is_read_from_the_listen_addresses(addresses, expected):
    assert summary.exposure(addresses) == expected


def test_a_sidecar_on_the_network_is_a_warning_and_the_backend_is_info(client, monkeypatch):
    monkeypatch.setenv("FLASK_PORT", "5000")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(summary, "_plugin_ports", lambda: [("audio_foundry", "Audio Foundry", 8206)])
    monkeypatch.setattr(summary, "listening_addresses", lambda: {
        5000: {"0.0.0.0"},
        6379: {"0.0.0.0"},
        8206: {"127.0.0.1"},
    })
    _, checks = _checks(client)
    assert checks["backend"]["status"] == "info" and "0.0.0.0" in checks["backend"]["detail"]
    assert checks["redis"]["status"] == "warn"
    assert "without Guaardvark's API key" in checks["redis"]["detail"]
    assert checks["plugin:audio_foundry"]["status"] == "ok"


def test_a_network_sidecar_names_the_setting_that_binds_it(client, monkeypatch):
    monkeypatch.setattr(summary, "_plugin_ports", lambda: [("audio_foundry", "Audio Foundry", 8206)])
    monkeypatch.setattr(summary, "listening_addresses", lambda: {8206: {"0.0.0.0"}})
    _, checks = _checks(client)
    assert checks["plugin:audio_foundry"]["status"] == "warn"
    assert "GUAARDVARK_AUDIO_FOUNDRY_HOST" in checks["plugin:audio_foundry"]["detail"]


def test_an_unreadable_socket_table_is_said_so(client, monkeypatch):
    monkeypatch.setattr(summary, "listening_addresses", lambda: None)
    _, checks = _checks(client)
    assert checks["listening"]["status"] == "info"
    assert "backend" not in checks
