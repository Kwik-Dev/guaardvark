"""The listing tells the Plugins page which plugins contend for the GPU.

The page kept its own rule (a VRAM threshold) under a "mirror" comment and
it drifted from the backend's set; now the backend's set is served.
"""

import json

import pytest

from backend.plugins.plugin_manager import GPU_EXCLUSIVE_PLUGIN_IDS, PluginManager
from backend.plugins.plugin_registry import PluginRegistry


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS", raising=False)
    import backend.extensions as _ext
    monkeypatch.setattr(_ext, "plugin_dirs", lambda *_a, **_k: [])
    root = tmp_path / "plugins"
    for plugin_id, vram in (("ollama", 8000), ("heavy_tool", 10000), ("light", 0)):
        plugin = root / plugin_id
        plugin.mkdir(parents=True)
        (plugin / "plugin.json").write_text(json.dumps({
            "id": plugin_id, "name": plugin_id, "version": "1.0.0", "type": "tool",
            "vram_estimate_mb": vram, "config": {"default_enabled": False},
        }))
    return root


def test_listing_carries_the_gate_set_not_a_vram_threshold(plugins_dir, monkeypatch):
    monkeypatch.setattr(PluginManager, "_check_service_running", lambda self, metadata: False)
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))

    flags = {p["id"]: p["gpu_exclusive"] for p in manager.list_plugins()}

    assert "ollama" in GPU_EXCLUSIVE_PLUGIN_IDS
    assert flags == {"ollama": True, "heavy_tool": False, "light": False}
