"""The Ollama card honours "I run Ollama myself: never start or stop it".

The switch (GUAARDVARK_OLLAMA_EXTERNAL=1 in .env) was read by the top-level
start.sh and stop.sh only. The card's own toggle still ran
plugins/ollama/scripts/start.sh and stop.sh, which start and stop the systemd
service, kill `ollama serve` and clear port 11434, and Off also unloaded
every model first. Now the manager checks the same switch: On only confirms
the user's Ollama answers, Off is refused with the reason.
"""

import json
import time
from types import SimpleNamespace

import pytest

from backend.plugins import plugin_manager as pm_module
from backend.plugins.plugin_manager import PluginManager, PluginStatus
from backend.plugins.plugin_registry import PluginRegistry

PORT = 11434


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS", raising=False)
    monkeypatch.setenv("GUAARDVARK_SKIP_DEP_RECONCILER", "1")
    import backend.extensions as _ext
    monkeypatch.setattr(_ext, "plugin_dirs", lambda *_a, **_k: [])
    root = tmp_path / "plugins"
    plugin = root / "ollama"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "scripts" / "start.sh").write_text("#!/bin/bash\necho started\n")
    (plugin / "scripts" / "stop.sh").write_text("#!/bin/bash\necho stopped\n")
    manifest = {
        "id": "ollama",
        "name": "Ollama",
        "version": "1.0.0",
        "type": "service",
        "port": PORT,
        "core": True,
        "config": {"default_enabled": True, "service_url": f"http://127.0.0.1:{PORT}", "timeout": 5},
        "endpoints": {"health": "/"},
    }
    (plugin / "plugin.json").write_text(json.dumps(manifest))
    return root


@pytest.fixture
def daemon(monkeypatch):
    """What the health probe sees; ``up`` flips when a stop script runs."""
    state = {"up": True, "scripts": [], "unloads": 0}

    monkeypatch.setattr(PluginManager, "_check_service_running", lambda self, metadata: state["up"])
    monkeypatch.setattr(
        PluginManager, "_unload_all_ollama_models", lambda self: state.__setitem__("unloads", state["unloads"] + 1)
    )

    def fake_run(argv, cwd, timeout):
        state["scripts"].append(argv[-1].rsplit("/", 1)[-1])
        if argv[-1].endswith("stop.sh"):
            state["up"] = False
        return {"ok": True, "rc": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(pm_module, "_run_plugin_script", fake_run)
    # stop_plugin sleeps between the script and its probe; not in a unit test.
    monkeypatch.setattr(pm_module, "time", SimpleNamespace(sleep=lambda s: None, time=time.time, monotonic=time.monotonic))
    return state


@pytest.fixture
def external(monkeypatch):
    """The switch, as the settings API writes it, isolated from this box's .env."""
    import backend.profiles as profiles
    values = {}
    monkeypatch.setattr(profiles, "env_file", lambda root=None: SimpleNamespace(is_file=lambda: True))
    monkeypatch.setattr(profiles, "read_env_value", lambda key, root=None: values.get(key))
    monkeypatch.delenv(pm_module.OLLAMA_EXTERNAL_ENV, raising=False)

    def set_switch(on):
        values[pm_module.OLLAMA_EXTERNAL_ENV] = "1" if on else None

    return set_switch


def _manager(plugins_dir):
    return PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))


def test_off_is_refused_and_touches_nothing(plugins_dir, daemon, external):
    external(True)
    manager = _manager(plugins_dir)
    assert manager.get_status("ollama") is PluginStatus.RUNNING

    result = manager.disable_plugin("ollama")

    assert result["success"] is False
    assert pm_module.OLLAMA_EXTERNAL_SWITCH in result["error"]
    assert daemon["scripts"] == []
    assert daemon["unloads"] == 0
    assert manager.get_status("ollama") is PluginStatus.RUNNING
    assert manager.is_effectively_enabled("ollama") is True


def test_stop_is_refused(plugins_dir, daemon, external):
    external(True)
    manager = _manager(plugins_dir)

    result = manager.stop_plugin("ollama")

    assert result["success"] is False
    assert "Turn that switch off" in result["error"]
    assert daemon["scripts"] == []
    assert daemon["up"] is True


def test_on_only_confirms_the_users_ollama_answers(plugins_dir, daemon, external):
    external(True)
    daemon["up"] = False
    manager = _manager(plugins_dir)
    assert manager.get_status("ollama") is PluginStatus.STOPPED

    daemon["up"] = True
    result = manager.start_plugin("ollama")

    assert result["success"] is True
    assert "never starts or stops it" in result["message"]
    assert daemon["scripts"] == []
    assert manager.get_status("ollama") is PluginStatus.RUNNING


def test_on_with_nothing_answering_says_what_to_do(plugins_dir, daemon, external):
    external(True)
    daemon["up"] = False
    manager = _manager(plugins_dir)

    result = manager.start_plugin("ollama")

    assert result["success"] is False
    assert "Start your Ollama" in result["error"]
    assert daemon["scripts"] == []
    assert manager.get_status("ollama") is PluginStatus.STOPPED


def test_switch_off_keeps_the_old_behaviour(plugins_dir, daemon, external):
    external(False)
    manager = _manager(plugins_dir)

    result = manager.disable_plugin("ollama")

    assert result["success"] is True
    assert daemon["unloads"] == 1
    assert daemon["scripts"] == ["stop.sh"]
    assert manager.get_status("ollama") is PluginStatus.DISABLED


def test_env_counts_only_without_a_dotenv(monkeypatch):
    import backend.profiles as profiles
    monkeypatch.setattr(profiles, "env_file", lambda root=None: SimpleNamespace(is_file=lambda: False))
    monkeypatch.setattr(profiles, "read_env_value", lambda key, root=None: "1")

    monkeypatch.setenv(pm_module.OLLAMA_EXTERNAL_ENV, "1")
    assert pm_module.ollama_is_external() is True
    monkeypatch.delenv(pm_module.OLLAMA_EXTERNAL_ENV)
    assert pm_module.ollama_is_external() is False
