"""A service that answers its health path with 200 is healthy, whatever the body.

ComfyUI's manifest declares "/" as its health path, which serves the UI page,
so the probe's JSON parse failed and /api/plugins/comfyui/health (and the
plugin's info) reported {"status": "error", "error": "Expecting value ..."}
while ComfyUI was running.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import backend.plugins.plugin_manager as pm
from backend.plugins.plugin_manager import PluginManager

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, str):
            raise requests.exceptions.JSONDecodeError("Expecting value", self._body, 0)
        return self._body


def _manager(health_path="/"):
    meta = SimpleNamespace(
        type="service",
        endpoints={"health": health_path},
        config=SimpleNamespace(enabled=True, service_url="http://127.0.0.1:8188"),
        port=8188,
    )
    manager = PluginManager.__new__(PluginManager)
    manager.registry = SimpleNamespace(get_plugin=lambda plugin_id: meta)
    manager._plugin_status = {}
    return manager


@pytest.fixture
def reply(monkeypatch):
    def set_reply(status_code, body):
        monkeypatch.setattr(pm.requests, "get", lambda url, timeout=None: _Response(status_code, body))
    return set_reply


def test_a_page_in_reply_is_healthy(reply):
    reply(200, "<!doctype html><html>")
    assert _manager().health_check("comfyui") == {"status": "healthy", "plugin_id": "comfyui"}


def test_json_that_is_not_an_object_is_healthy(reply):
    reply(200, ["ok"])
    assert _manager().health_check("comfyui") == {"status": "healthy", "plugin_id": "comfyui"}


def test_a_json_reply_is_relayed_with_credentials_redacted(reply):
    reply(200, {"status": "ok", "auth_token": "s3cret"})
    health = _manager().health_check("upscaling")
    assert health["status"] == "ok" and health["plugin_id"] == "upscaling"
    assert health["auth_token"] != "s3cret"


def test_a_non_200_reply_is_unhealthy(reply):
    reply(404, "<html>")
    assert _manager().health_check("comfyui")["status"] == "unhealthy"


def test_comfyui_declares_its_page_as_the_health_path():
    manifest = json.loads((REPO_ROOT / "plugins" / "comfyui" / "plugin.json").read_text())
    assert manifest["endpoints"]["health"] == "/"
