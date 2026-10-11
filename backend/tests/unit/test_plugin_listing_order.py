"""The plugin listing, and so the Plugins page, is in display-name order.

It used to follow the directory listing, which differs between file systems
and shuffled the cards whenever a plugin was added.
"""

import json

import pytest

from backend.plugins.plugin_registry import PluginRegistry


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS", raising=False)
    import backend.extensions as _ext
    monkeypatch.setattr(_ext, "plugin_dirs", lambda *_a, **_k: [])
    root = tmp_path / "plugins"
    for folder, name in (("zeta", "Vision"), ("alpha", "ollama"), ("mid", "ComfyUI"), ("b", "Audio Foundry")):
        plugin = root / folder
        plugin.mkdir(parents=True)
        (plugin / "plugin.json").write_text(json.dumps({
            "id": folder, "name": name, "version": "1.0.0", "type": "tool",
            "config": {"default_enabled": False},
        }))
    return root


def test_listing_is_sorted_by_name_ignoring_case(plugins_dir):
    registry = PluginRegistry(plugins_dir=plugins_dir)

    names = [p["name"] for p in registry.list_plugins()]

    assert names == ["Audio Foundry", "ComfyUI", "ollama", "Vision"]
    assert set(registry.get_all_plugins()) == {"zeta", "alpha", "mid", "b"}
