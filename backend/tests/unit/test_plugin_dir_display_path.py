"""The plugin folder the Plugins page shows never names the home directory.

"More details" printed the absolute plugin folder, and every status snapshot
carried it to the browser: the login name and the checkout's location on
every machine, in every screen recording. The listing and the detail route
now carry the display form (``plugins/ollama``), the same one the CLI uses.
"""

import json

import pytest

from backend.plugins.plugin_manager import PluginManager
from backend.plugins.plugin_registry import PluginRegistry


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS", raising=False)
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    import backend.extensions as _ext
    monkeypatch.setattr(_ext, "plugin_dirs", lambda *_a, **_k: [])
    plugin = tmp_path / "plugins" / "demo"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "version": "1.0.0", "type": "tool",
        "config": {"default_enabled": False},
    }))
    return tmp_path


def test_listing_shows_the_path_relative_to_the_install(checkout):
    registry = PluginRegistry(plugins_dir=checkout / "plugins")

    listed = {p["id"]: p for p in registry.list_plugins()}["demo"]

    assert listed["plugin_dir"] == "plugins/demo"
    assert str(checkout) not in listed["plugin_dir"]
    # The real path is still there for the manager's own use.
    assert registry.get_plugin_dir("demo") == checkout / "plugins" / "demo"


def test_detail_route_shows_the_same_form(checkout, monkeypatch):
    monkeypatch.setattr(PluginManager, "_check_service_running", lambda self, metadata: False)
    manager = PluginManager(registry=PluginRegistry(plugins_dir=checkout / "plugins"))

    info = manager.get_plugin_info("demo")

    assert info["plugin_dir"] == "plugins/demo"
    assert str(checkout) not in json.dumps(manager.list_plugins())
