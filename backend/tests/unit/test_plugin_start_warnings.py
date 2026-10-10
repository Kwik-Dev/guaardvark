"""A start script's warnings reach the Plugins page as the card's status message.

plugins/video_editor/plugin.json lists melt and auto-editor as required
binaries; its start.sh reports a missing one as a "Warning:" line and exits
0, so the service runs without that endpoint and the card said Running and
nothing else. The manager keeps those lines until the next stop or start.
"""

import json
import time
from types import SimpleNamespace

import pytest

from backend.plugins import plugin_manager as pm_module
from backend.plugins.plugin_manager import PluginManager, PluginStatus
from backend.plugins.plugin_registry import PluginRegistry

PLUGIN_ID = "editor"
START_OUTPUT = (
    "Resolved melt: /usr/bin/melt\n"
    "Warning: auto-editor not found — the silence-trim endpoint (/auto-editor/trim) will fail until it is installed.\n"
    "  Install: pip install -r requirements.txt\n"
    "Starting Video Editor...\n"
    "Warning: Health endpoint not responsive after 30s\n"
)
# Warnings a start script resolves or retries before it exits 0, as
# audio_foundry/start.sh and discord/start.sh print them.
TRANSIENT_OUTPUT = (
    "Warning: install_pytorch.sh returned non-zero (continuing to verify)\n"
    "Warning: {root}/scripts/install_pytorch.sh missing — cannot repair torch/CUDA mismatch\n"
    "Warning: Backend not reachable at localhost:5000 (bot will start anyway)\n"
)


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS", raising=False)
    monkeypatch.setenv("GUAARDVARK_SKIP_DEP_RECONCILER", "1")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    import backend.extensions as _ext
    monkeypatch.setattr(_ext, "plugin_dirs", lambda *_a, **_k: [])
    root = tmp_path / "plugins"
    plugin = root / PLUGIN_ID
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "scripts" / "start.sh").write_text("#!/bin/bash\n")
    (plugin / "scripts" / "stop.sh").write_text("#!/bin/bash\n")
    (plugin / "plugin.json").write_text(json.dumps({
        "id": PLUGIN_ID, "name": "Editor", "version": "0.1.0", "type": "service", "port": 8207,
        "config": {"default_enabled": True, "service_url": "http://127.0.0.1:8207", "timeout": 5},
        "endpoints": {"health": "/health"},
        "requirements": {"system_binaries": ["melt", "auto-editor"]},
    }))
    return root


@pytest.fixture
def service(monkeypatch):
    state = {"up": False, "stdout": START_OUTPUT}
    monkeypatch.setattr(PluginManager, "_check_service_running", lambda self, metadata: state["up"])

    def fake_run(argv, cwd, timeout):
        state["up"] = argv[-1].endswith("start.sh")
        return {"ok": True, "rc": 0, "stdout": state["stdout"], "stderr": ""}

    monkeypatch.setattr(pm_module, "_run_plugin_script", fake_run)
    monkeypatch.setattr(pm_module, "time", SimpleNamespace(sleep=lambda s: None, time=time.time, monotonic=time.monotonic))
    return state


def _listing(manager):
    return {p["id"]: p for p in manager.list_plugins()}[PLUGIN_ID]


def test_missing_tool_warning_is_the_status_message(plugins_dir, service):
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))

    result = manager.start_plugin(PLUGIN_ID)

    assert result["success"] is True
    assert manager.get_status(PLUGIN_ID) is PluginStatus.RUNNING
    message = _listing(manager)["status_message"]
    assert "auto-editor not found" in message
    assert "Health endpoint" not in message
    assert "Resolved melt" not in message
    assert manager.get_plugin_info(PLUGIN_ID)["status_message"] == message


def test_warnings_the_script_went_on_to_resolve_are_dropped(plugins_dir, service, tmp_path):
    service["stdout"] = TRANSIENT_OUTPUT.format(root=tmp_path) + "Starting...\n"
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))

    manager.start_plugin(PLUGIN_ID)

    assert "status_message" not in _listing(manager)


def test_a_lasting_warning_keeps_no_absolute_path(plugins_dir, service, tmp_path):
    service["stdout"] = (
        f"Warning: venv-music torch import failed — ACE-Step music gen will be unavailable\n"
        f"Warning: melt not found at {tmp_path}/plugins/video_editor/melt — render-to-MP4 endpoint will return error until installed.\n"
    )
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))

    manager.start_plugin(PLUGIN_ID)

    message = _listing(manager)["status_message"]
    assert "ACE-Step music gen will be unavailable" in message
    assert "./plugins/video_editor/melt" in message
    assert str(tmp_path) not in message


def test_a_clean_start_has_no_message(plugins_dir, service):
    service["stdout"] = "Resolved melt: /usr/bin/melt\nVideo Editor health endpoint ready\n"
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))

    manager.start_plugin(PLUGIN_ID)

    assert "status_message" not in _listing(manager)


def test_stop_clears_the_message(plugins_dir, service):
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))
    manager.start_plugin(PLUGIN_ID)
    assert "status_message" in _listing(manager)
    manager._gate._last_finished_at.clear()
    manager._gate._global_last_finished_at = 0.0

    assert manager.stop_plugin(PLUGIN_ID)["success"] is True

    assert "status_message" not in _listing(manager)


def test_a_stranger_on_the_port_outranks_start_warnings(plugins_dir, service):
    manager = PluginManager(registry=PluginRegistry(plugins_dir=plugins_dir))
    manager.start_plugin(PLUGIN_ID)
    manager._note_instance_problem(PLUGIN_ID, "another Editor holds port 8207")

    assert _listing(manager)["status_message"] == "another Editor holds port 8207"
