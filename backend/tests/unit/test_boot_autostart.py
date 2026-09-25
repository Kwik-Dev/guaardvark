"""A ./stop.sh; ./start.sh cycle brings ComfyUI back the way it was.

stop.sh kills ComfyUI; the backend's boot restore is the first line and
start.sh's Pass 0 the second, through the same plugin-manager path the
Plugins page toggle uses. The manager's start wait now runs for the
manifest's timeout (ComfyUI 60 s) instead of a fixed 10 s, and a service seen
answering later joins the persisted running set, so the next boot knows.
"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.plugins import boot_autostart
from backend.plugins import plugin_manager as pm_module
from backend.plugins.boot_autostart import should_start_comfyui
from backend.plugins.plugin_manager import PluginManager, PluginStatus

ROOT = Path(__file__).resolve().parents[3]


def _checkout(tmp_path, *, enabled=True, running=True, auto_start=False, models=True, port=8188, local=None):
    root = tmp_path / "checkout"
    plugin = root / "plugins" / "comfyui"
    plugin.mkdir(parents=True)
    manifest = {
        "id": "comfyui", "name": "ComfyUI", "type": "service", "port": port,
        "config": {"default_enabled": False, "default_auto_start": auto_start},
        "instance_check": {
            "lists_file_from": ["ComfyUI/models/unet", "ComfyUI/models/checkpoints"],
            "extensions": [".safetensors", ".gguf"],
        },
    }
    (plugin / "plugin.json").write_text(json.dumps(manifest))
    if local:
        (plugin / "plugin.local.json").write_text(json.dumps(local))
    (root / "data").mkdir()
    state = {"user_enabled": {"comfyui": enabled}, "running": ["comfyui", "ollama"] if running else ["ollama"]}
    (root / "data" / "plugin_state.json").write_text(json.dumps(state))
    unet = plugin / "ComfyUI" / "models" / "unet"
    unet.mkdir(parents=True)
    (unet / "put_unet_files_here").write_text("")
    if models:
        (unet / "Wan2.2" / "HighNoise").mkdir(parents=True)
        (unet / "Wan2.2" / "HighNoise" / "expert.gguf").write_bytes(b"w")
    return root


def _free(port):
    return False


@pytest.fixture(autouse=True)
def _no_ambient_model_home(monkeypatch):
    """Keep these cases off the developer's .env.

    backend/tests/conftest.py imports the backend, whose config calls
    load_dotenv(): a real .env with GUAARDVARK_COMFYUI_DIR set reaches
    os.environ, so the "no video models" cases pass here and fail in CI (which
    has no .env) — or the reverse. Every case passes its own ``environ``; these
    two are the ones the guard would otherwise read on its own.
    """
    for name in ("GUAARDVARK_COMFYUI_DIR", "GUAARDVARK_PROFILE_PLUGIN_DEFAULTS"):
        monkeypatch.delenv(name, raising=False)


def _shared(tmp_path, *, leaf="unet", name="expert.gguf"):
    """A model home OUTSIDE the checkout, as GUAARDVARK_COMFYUI_DIR points at."""
    base = tmp_path / "shared" / "models" / leaf / "Wan2.2" / "HighNoise"
    base.mkdir(parents=True)
    (base / name).write_bytes(b"w")
    return tmp_path / "shared"


def test_restores_a_plugin_that_was_running_with_models(tmp_path):
    start, reason = should_start_comfyui(_checkout(tmp_path), probe=_free)
    assert start is True
    assert reason == "it was running before the last stop and video models are installed"


def test_never_on_a_fresh_install_without_video_models(tmp_path):
    start, reason = should_start_comfyui(_checkout(tmp_path, models=False), probe=_free)
    assert start is False and "no video models" in reason


# ── a shared model home (weights outside the checkout) ───────────────────
# The plugin tree is empty scaffolding on a Comfy Desktop install, so probing it
# alone answered "no video models are installed yet" forever and start.sh never
# auto-started ComfyUI. Two documented routes hand the server the real tree.

def test_shared_model_home_counts_via_the_env_var(tmp_path):
    root = _checkout(tmp_path, models=False)
    without = should_start_comfyui(root, environ={}, probe=_free)
    with_env = should_start_comfyui(
        root, environ={"GUAARDVARK_COMFYUI_DIR": str(_shared(tmp_path))}, probe=_free)
    assert without[0] is False and "no video models" in without[1]
    assert with_env[0] is True and with_env[1] == (
        "it was running before the last stop and video models are installed")


def test_shared_model_home_counts_via_extra_model_paths(tmp_path):
    root = _checkout(tmp_path, models=False)
    comfy = root / "plugins" / "comfyui" / "ComfyUI"
    comfy.mkdir(parents=True, exist_ok=True)
    (comfy / "extra_model_paths.yaml").write_text(
        "# bridge the shared tree\n"
        "comfy-desktop:\n"
        f"    base_path: {_shared(tmp_path)}\n"
        "    unet: models/unet/\n")
    start, reason = should_start_comfyui(root, environ={}, probe=_free)
    assert start is True and "video models are installed" in reason


def test_a_lora_is_not_a_video_model(tmp_path):
    """Only the folders ComfyUI serves video from count; a stray .safetensors in
    loras/ must not read as "a video model is installed"."""
    root = _checkout(tmp_path, models=False)
    start, reason = should_start_comfyui(
        root, environ={"GUAARDVARK_COMFYUI_DIR": str(_shared(tmp_path, leaf="loras"))}, probe=_free)
    assert start is False and "no video models" in reason


def test_an_interrupted_download_is_not_a_video_model(tmp_path):
    """HuggingFace partials live under unet/.cache — .incomplete carries no model
    extension in its own name, and must not satisfy the guard."""
    root = _checkout(tmp_path, models=False)
    stale = root / "plugins" / "comfyui" / "ComfyUI" / "models" / "unet" / ".cache" / "huggingface"
    stale.mkdir(parents=True)
    (stale / "Fawps-DAs5.163e9e5a.8340d9f5.incomplete").write_bytes(b"w")
    (stale / "Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf.lock").write_bytes(b"")
    start, reason = should_start_comfyui(root, environ={}, probe=_free)
    assert start is False and "no video models" in reason


def test_a_missing_shared_home_falls_back_to_the_plugin_tree(tmp_path):
    """The env var pointing somewhere empty must not disable a working checkout."""
    root = _checkout(tmp_path)  # plugin tree has a real .gguf
    start, reason = should_start_comfyui(
        root, environ={"GUAARDVARK_COMFYUI_DIR": str(tmp_path / "nope")}, probe=_free)
    assert start is True and "video models are installed" in reason


def test_not_running_before_and_no_auto_start_stays_down(tmp_path):
    start, reason = should_start_comfyui(_checkout(tmp_path, running=False), probe=_free)
    assert start is False and "not running before the last stop" in reason


def test_default_auto_start_counts(tmp_path):
    start, reason = should_start_comfyui(_checkout(tmp_path, running=False, auto_start=True), probe=_free)
    assert start is True and "default_auto_start" in reason


def test_disabled_plugin_is_left_alone(tmp_path):
    start, reason = should_start_comfyui(_checkout(tmp_path, enabled=False), probe=_free)
    assert start is False and reason == "the ComfyUI plugin is disabled"


def test_occupied_port_is_not_started_into(tmp_path):
    probed = []
    root = _checkout(tmp_path, local={"port": 8199})
    start, reason = should_start_comfyui(root, probe=lambda port: probed.append(port) or True)
    assert start is False and reason == "something already answers on port 8199"
    assert probed == [8199], "plugin.local.json's port is the one probed"


def test_profile_default_enables_when_the_user_never_toggled(tmp_path):
    root = _checkout(tmp_path)
    state = json.loads((root / "data" / "plugin_state.json").read_text())
    state["user_enabled"] = {}
    (root / "data" / "plugin_state.json").write_text(json.dumps(state))
    off, _ = should_start_comfyui(root, environ={}, probe=_free)
    on, _ = should_start_comfyui(root, environ={"GUAARDVARK_PROFILE_PLUGIN_DEFAULTS": "comfyui=true"}, probe=_free)
    assert (off, on) == (False, True)


def test_start_sh_runs_the_decision_by_path(tmp_path):
    """The exact heredoc start.sh uses, against a temp checkout (port left free)."""
    text = (ROOT / "start.sh").read_text()
    snippet = text.split("<<'AUTOSTART'", 1)[1].split("\n", 1)[1].split("\nAUTOSTART\n", 1)[0]
    root = _checkout(tmp_path, port=1)  # nothing answers on port 1
    (root / "backend" / "plugins").mkdir(parents=True)
    (root / "backend" / "plugins" / "boot_autostart.py").write_bytes(boot_autostart.__file__ and Path(boot_autostart.__file__).read_bytes())

    out = subprocess.run([sys.executable, "-", str(root)], input=snippet, capture_output=True, text=True, check=True).stdout

    assert out.strip() == "yes|it was running before the last stop and video models are installed"


@pytest.mark.parametrize("body, expected", [
    ('{"success": true, "data": {"success": true, "message": "Plugin started successfully"}}', "yes|Plugin started successfully"),
    ('{"success": true, "data": {"success": false, "gated": true, "error": "cooling down"}}', "no|cooling down"),
    ('{"success": false, "error": "Start script failed: x"}', "no|Start script failed: x"),
    ("", "no|no answer from the backend"),
])
def test_start_sh_reads_the_api_reply(body, expected):
    text = (ROOT / "start.sh").read_text()
    code = text.split("comfyui_start_outcome() {\n", 1)[1].split("\n}\n", 1)[0]
    code = code.split("-c '", 1)[1].rsplit("'", 1)[0]
    out = subprocess.run([sys.executable, "-c", code], input=body, capture_output=True, text=True, check=True).stdout
    assert out.strip() == expected


# ── the manager side ──────────────────────────────────────────────────────

def test_start_wait_follows_the_manifest_timeout():
    probes = PluginManager._health_wait_probes
    assert probes(SimpleNamespace(config=SimpleNamespace(timeout=60))) == 120
    assert probes(SimpleNamespace(config=SimpleNamespace(timeout=30))) == 60
    assert probes(SimpleNamespace(config=SimpleNamespace(timeout=3))) == 20
    assert probes(SimpleNamespace(config=None)) == 60


def test_start_plugin_waits_the_manifest_timeout_before_giving_up(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "start.sh").write_text("#!/bin/bash\n")
    metadata = SimpleNamespace(
        type="service", core=False, port=8188, dependencies=[], instance_check={},
        config=SimpleNamespace(enabled=True, service_url=None, timeout=60), endpoints={},
    )
    sleeps = []
    monkeypatch.setattr(pm_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(pm_module, "_run_plugin_script", lambda **kw: {"ok": True, "rc": 0, "stdout": "", "stderr": ""})
    stub = SimpleNamespace(
        registry=SimpleNamespace(get_plugin=lambda pid: metadata, get_plugin_dir=lambda pid: tmp_path, is_registered=lambda pid: True),
        is_effectively_enabled=lambda pid: True,
        _plugin_status={}, _instance_problems={},
        _broadcast_plugins_status=lambda reason: None,
        _check_service_running=lambda meta: False,
        _save_running=lambda: None,
        _fail_plugin_start=lambda pid, payload: payload,
        _health_wait_probes=lambda meta: PluginManager._health_wait_probes(meta),
        _gate=SimpleNamespace(try_acquire=lambda pid: (True, 0.0, ""), release=lambda pid: None),
        state_store=SimpleNamespace(reset_plugin_health_counters=lambda pid: None),
    )

    result = PluginManager.start_plugin(stub, "comfyui")

    assert result["success"] is False and "timeout" in result["error"]
    assert len(sleeps) == 120 and sum(sleeps) == pytest.approx(60.0)


def _refresh_stub(before, answering, running_recorded):
    metadata = SimpleNamespace(type="service", config=SimpleNamespace(enabled=True))
    saved = []
    stub = SimpleNamespace(
        registry=SimpleNamespace(get_all_plugins=lambda: {"comfyui": metadata}, get_plugin=lambda pid: metadata),
        _plugin_status={"comfyui": before} if before else {},
        _check_service_running=lambda meta: answering,
        state_store=SimpleNamespace(get_running=lambda: list(running_recorded), set_running=saved.append),
    )
    return stub, saved


def test_service_seen_answering_later_joins_the_running_set():
    stub, saved = _refresh_stub(PluginStatus.ERROR, answering=True, running_recorded=["ollama"])
    PluginManager._refresh_status(stub)
    assert stub._plugin_status["comfyui"] is PluginStatus.RUNNING
    assert saved == [["ollama", "comfyui"]] or saved == [["comfyui", "ollama"]]


def test_service_going_down_is_not_forgotten_by_a_refresh():
    stub, saved = _refresh_stub(PluginStatus.RUNNING, answering=False, running_recorded=["comfyui"])
    PluginManager._refresh_status(stub)
    assert stub._plugin_status["comfyui"] is PluginStatus.STOPPED
    assert saved == [], "stop.sh takes ComfyUI down before the backend; a poll in between must not erase it"


def test_already_recorded_service_is_not_rewritten():
    stub, saved = _refresh_stub(PluginStatus.STOPPED, answering=True, running_recorded=["comfyui"])
    PluginManager._refresh_status(stub)
    assert saved == []
