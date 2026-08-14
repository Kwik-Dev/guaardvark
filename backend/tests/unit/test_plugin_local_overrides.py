"""Unit tests for plugin.local.json per-install manifest overrides.

The untracked plugin.local.json next to a plugin.json must win on load, so
operator edits (e.g. a custom ComfyUI port) survive git updates.
"""
import json

from backend.plugins.plugin_base import PluginMetadata


def _write_manifest(tmp_path, data, name="plugin.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


BASE = {
    "id": "demo",
    "name": "Demo",
    "version": "1.0.0",
    "type": "service",
    "port": 8188,
    "config": {"enabled": True, "timeout": 30},
}


def test_no_local_file_uses_manifest_values(tmp_path):
    manifest = _write_manifest(tmp_path, BASE)
    meta = PluginMetadata.from_json_file(manifest)
    assert meta.port == 8188


def test_local_port_override_wins(tmp_path):
    manifest = _write_manifest(tmp_path, BASE)
    _write_manifest(tmp_path, {"port": 8000}, name="plugin.local.json")
    meta = PluginMetadata.from_json_file(manifest)
    assert meta.port == 8000
    assert meta.id == "demo"
    assert meta.version == "1.0.0"


def test_local_config_merges_instead_of_replacing(tmp_path):
    manifest = _write_manifest(tmp_path, BASE)
    _write_manifest(tmp_path, {"config": {"timeout": 99}}, name="plugin.local.json")
    meta = PluginMetadata.from_json_file(manifest)
    assert meta.config.timeout == 99
    assert meta.config.enabled is True
    assert meta.port == 8188


def test_invalid_local_file_is_ignored(tmp_path):
    manifest = _write_manifest(tmp_path, BASE)
    (tmp_path / "plugin.local.json").write_text("{not json")
    meta = PluginMetadata.from_json_file(manifest)
    assert meta.port == 8188
