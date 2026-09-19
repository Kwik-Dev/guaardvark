"""A service plugin's health probe follows the path its manifest declares.

zvec_grep's daemon answers /healthz, not /health. With no health key in its
manifest, _check_service_running fell back to the default /health, got a 404
and the Plugins page showed the plugin stopped while its daemon was serving
searches on 127.0.0.1:7999.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.plugins.plugin_manager as pm
from backend.plugins.plugin_manager import PluginManager

REPO_ROOT = Path(__file__).resolve().parents[3]


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


def _service(endpoints, service_url="http://127.0.0.1:7999", port=7999):
    return SimpleNamespace(
        type="service",
        endpoints=endpoints,
        config=SimpleNamespace(service_url=service_url),
        port=port,
    )


@pytest.fixture
def probed(monkeypatch):
    """Records probed URLs; only /healthz answers 200, as the daemon does."""
    urls = []

    def fake_get(url, timeout=None):
        urls.append(url)
        return _Response(200 if url.endswith("/healthz") else 404)

    monkeypatch.setattr(pm.requests, "get", fake_get)
    return urls


def test_declared_health_path_is_used(probed):
    manager = PluginManager.__new__(PluginManager)

    assert PluginManager._check_service_running(manager, _service({"mcp": "/mcp", "health": "/healthz"}))
    assert probed == ["http://127.0.0.1:7999/healthz"]


def test_without_a_declared_path_the_default_is_probed_and_misses(probed):
    manager = PluginManager.__new__(PluginManager)

    assert not PluginManager._check_service_running(manager, _service({"mcp": "/mcp"}))
    assert probed == ["http://127.0.0.1:7999/health"]


def test_zvec_grep_manifest_declares_the_path_its_daemon_answers():
    manifest = json.loads((REPO_ROOT / "plugins" / "zvec_grep" / "plugin.json").read_text())

    assert manifest["endpoints"]["health"] == "/healthz"
    assert manifest["endpoints"]["mcp"] == "/mcp"
