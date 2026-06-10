"""Unit tests for plugin auto-orchestration bridge."""
import pytest

import backend.services.plugin_bridge as pb
from backend.plugins.plugin_base import PluginStatus


class _FakePM:
    def __init__(self):
        self.enabled = set()
        self.status = {}
        self.start_calls = []
        self.stop_calls = []

    def is_effectively_enabled(self, plugin_id):
        return plugin_id in self.enabled

    def enable_plugin(self, plugin_id):
        self.enabled.add(plugin_id)
        return {"success": True}

    def get_status(self, plugin_id):
        return self.status.get(plugin_id, PluginStatus.STOPPED)

    def start_plugin(self, plugin_id):
        self.start_calls.append(plugin_id)
        if plugin_id == "comfyui" and len(self.start_calls) == 1:
            return {
                "success": False,
                "gated": True,
                "cooldown_remaining": 0.01,
                "error": "Plugin system cooling down",
            }
        self.status[plugin_id] = PluginStatus.RUNNING
        return {"success": True, "message": "started"}

    def stop_plugin(self, plugin_id):
        self.stop_calls.append(plugin_id)
        self.status[plugin_id] = PluginStatus.STOPPED
        return {"success": True, "message": "stopped"}


@pytest.fixture(autouse=True)
def reset_bridge_state(monkeypatch):
    pb._last_route = None
    pb._orchestrator_claims.clear()
    pb._user_controlled.clear()
    monkeypatch.setattr(pb, "auto_orchestrator_enabled", lambda: True)
    monkeypatch.setattr(pb, "_emit_plugins_status", lambda *a, **k: None)
    monkeypatch.setattr(pb, "_stop_blocked_reason", lambda _pid: None)
    fake = _FakePM()
    monkeypatch.setattr(pb, "_plugin_manager", lambda: fake)
    yield fake


def test_plugins_for_route_normalizes_ids(reset_bridge_state):
    assert pb.plugins_for_route("/projects/abc123") == []
    assert pb.plugins_for_route("/video") == ["comfyui"]
    assert pb.plugins_for_route("/music-video") == ["comfyui", "video_editor", "ollama"]


def test_prepare_starts_needed_plugins(reset_bridge_state):
    fake = reset_bridge_state
    fake.enabled.update(["comfyui", "video_editor", "ollama"])

    result = pb.prepare_plugins_for_route("/music-video")

    assert "comfyui" in fake.start_calls
    assert "video_editor" in fake.start_calls
    assert "ollama" in fake.start_calls
    assert set(result["orchestrator_claims"]) == {"comfyui", "video_editor", "ollama"}


def test_prepare_stops_orchestrator_claims_on_route_change(reset_bridge_state):
    fake = reset_bridge_state
    fake.enabled.update(["comfyui", "ollama"])
    fake.status["comfyui"] = PluginStatus.RUNNING
    pb._orchestrator_claims.add("comfyui")
    pb._last_route = "/video"

    pb.prepare_plugins_for_route("/chat")

    assert "comfyui" in fake.stop_calls
    assert "ollama" in fake.start_calls


def test_user_controlled_plugin_not_auto_stopped(reset_bridge_state):
    fake = reset_bridge_state
    fake.enabled.add("comfyui")
    fake.status["comfyui"] = PluginStatus.RUNNING
    pb._orchestrator_claims.add("comfyui")
    pb._user_controlled.add("comfyui")
    pb._last_route = "/video"

    pb.prepare_plugins_for_route("/chat")

    assert "comfyui" not in fake.stop_calls


def test_start_retries_on_gate_cooldown(reset_bridge_state, monkeypatch):
    fake = reset_bridge_state
    fake.enabled.add("comfyui")
    sleeps = []
    monkeypatch.setattr(pb.time, "sleep", lambda s: sleeps.append(s))

    pb.ensure_plugin_running("comfyui")

    assert fake.start_calls.count("comfyui") == 2
    assert fake.status["comfyui"] == PluginStatus.RUNNING
    assert sleeps


def test_disabled_flag_skips_prepare(reset_bridge_state, monkeypatch):
    monkeypatch.setattr(pb, "auto_orchestrator_enabled", lambda: False)
    result = pb.prepare_plugins_for_route("/video")
    assert result.get("skipped") is True
    assert reset_bridge_state.start_calls == []