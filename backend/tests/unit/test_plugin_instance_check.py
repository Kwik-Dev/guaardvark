"""A service plugin's health probe must prove the process on its port is ours.

Two installs on one box: the other checkout's ComfyUI (empty models tree) held
:8188, the plugin page said "running", and every Wan batch failed ComfyUI
validation with ``unet_name not in []``. A manifest ``instance_check`` names
GET paths whose JSON reply has to mention a model file that exists under this
checkout's own tree; a stranger cannot satisfy that.
"""

import json
from types import SimpleNamespace

import pytest
import requests

from backend.plugins import plugin_manager as pm_module
from backend.plugins.plugin_manager import PluginManager, PluginStatus
from backend.plugins.plugin_registry import PluginRegistry

PLUGIN_ID = "fakecomfy"
OUR_FILE = "Wan2.2-I2V/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf"


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS", raising=False)
    # The registry also loads extension sidecars from the checkout it runs in;
    # this test must see only the temporary plugin, whatever the checkout holds.
    import backend.extensions as _ext
    monkeypatch.setattr(_ext, "plugin_dirs", lambda *_a, **_k: [])
    root = tmp_path / "plugins"
    plugin = root / PLUGIN_ID
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "scripts" / "start.sh").write_text("#!/bin/bash\necho already running\n")
    manifest = {
        "id": PLUGIN_ID,
        "name": "Fake Comfy",
        "version": "1.0.0",
        "type": "service",
        "port": 8188,
        "config": {"default_enabled": True, "service_url": "http://127.0.0.1:8188", "timeout": 5},
        "endpoints": {"health": "/"},
        "instance_check": {
            "paths": ["/object_info/UNETLoader", "/object_info/UnetLoaderGGUF"],
            "lists_file_from": ["ComfyUI/models/unet", "ComfyUI/models/checkpoints"],
            "extensions": [".safetensors", ".gguf"],
        },
    }
    (plugin / "plugin.json").write_text(json.dumps(manifest))
    return root


def _add_local_model(plugins_dir, rel=OUR_FILE):
    path = plugins_dir / PLUGIN_ID / "ComfyUI" / "models" / "unet" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"weights")
    # A placeholder without a model extension never counts as a local model.
    (path.parent.parent / "put_unet_files_here").write_text("")


def _fake_http(monkeypatch, listed_files, calls=None):
    """Health answers 200; object_info paths list ``listed_files``."""
    calls = calls if calls is not None else []

    def fake_get(url, timeout=None, **_kw):
        calls.append(url)
        if url.endswith(":8188/"):
            return SimpleNamespace(status_code=200, json=lambda: {})
        if "/object_info/" in url:
            node = url.rsplit("/", 1)[1]
            body = {node: {"input": {"required": {"unet_name": [list(listed_files)]}}}}
            return SimpleNamespace(status_code=200, json=lambda: body)
        raise requests.RequestException(url)

    monkeypatch.setattr(pm_module, "requests", SimpleNamespace(
        get=fake_get, RequestException=requests.RequestException,
    ))
    return calls


def _manager(plugins_dir):
    return PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))


def test_foreign_instance_is_not_running_and_says_why(plugins_dir, monkeypatch):
    _add_local_model(plugins_dir)
    _fake_http(monkeypatch, ["someone_elses_model.safetensors"])

    manager = _manager(plugins_dir)

    assert manager.get_status(PLUGIN_ID) is PluginStatus.STOPPED
    listing = {p["id"]: p for p in manager.list_plugins()}[PLUGIN_ID]
    assert listing["running"] is False
    assert "holds port 8188" in listing["status_message"]
    assert "ComfyUI/models/unet" in listing["status_message"]
    assert manager.get_plugin_info(PLUGIN_ID)["status_message"] == listing["status_message"]


def test_our_instance_lists_a_local_file(plugins_dir, monkeypatch):
    _add_local_model(plugins_dir)
    _fake_http(monkeypatch, ["unrelated.safetensors", OUR_FILE])

    manager = _manager(plugins_dir)

    assert manager.get_status(PLUGIN_ID) is PluginStatus.RUNNING
    assert "status_message" not in {p["id"]: p for p in manager.list_plugins()}[PLUGIN_ID]


def test_no_local_models_is_vacuously_ours(plugins_dir, monkeypatch):
    calls = _fake_http(monkeypatch, [])

    manager = _manager(plugins_dir)

    assert manager.get_status(PLUGIN_ID) is PluginStatus.RUNNING
    assert not any("/object_info/" in url for url in calls), "nothing to compare, no probe"


def test_start_refuses_a_foreign_instance_without_running_the_script(plugins_dir, monkeypatch):
    _add_local_model(plugins_dir)
    _fake_http(monkeypatch, ["someone_elses_model.safetensors"])
    ran = []
    monkeypatch.setattr(pm_module, "_run_plugin_script", lambda **kw: ran.append(kw) or {"ok": True, "rc": 0, "stdout": "", "stderr": ""})
    manager = _manager(plugins_dir)

    result = manager.start_plugin(PLUGIN_ID)

    assert result["success"] is False
    assert result["error"].startswith("Not started: a Fake Comfy that is not this checkout's holds port 8188")
    assert ran == []
    assert manager.get_status(PLUGIN_ID) is PluginStatus.ERROR


def test_plugin_without_instance_check_keeps_the_plain_health_probe(plugins_dir, monkeypatch):
    manifest_path = plugins_dir / PLUGIN_ID / "plugin.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["instance_check"]
    manifest_path.write_text(json.dumps(manifest))
    _add_local_model(plugins_dir)
    calls = _fake_http(monkeypatch, ["someone_elses_model.safetensors"])

    manager = _manager(plugins_dir)

    assert manager.get_status(PLUGIN_ID) is PluginStatus.RUNNING
    assert not any("/object_info/" in url for url in calls)
