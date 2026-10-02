"""ACE-Step counts as installed only when every file its pipeline reads is on
this machine, and a partial snapshot is refused before anything loads.

The Install probe used to check one file (ace_step_transformer/config.json),
so an interrupted Install showed as installed; the daemon's
snapshot_download(local_files_only=True) returns a partial snapshot folder
as readily as a complete one, and ACEStepPipeline then tries to download the
repo, which the offline plugin cannot. No network, GPU or subprocess here.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from backend.services import audio_foundry_models as afm

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "audio_foundry"
FILES = json.loads((PLUGIN / "backends" / "acestep_files.json").read_text())


def _entry():
    return next(e for e in afm.AUDIO_FOUNDRY_MODELS if e["id"] == "ace-step")


def test_the_install_probe_lists_every_folder_the_pipeline_loads():
    required = afm.required_hub_files(_entry())
    assert required == FILES["files"]
    assert FILES["hf_repo"] == _entry()["hf_repo"]
    # ACEStepPipeline.load_checkpoint: four folders, config and weights each.
    for folder, weights in (("ace_step_transformer", "diffusion_pytorch_model.safetensors"),
                            ("music_dcae_f8c8", "diffusion_pytorch_model.safetensors"),
                            ("music_vocoder", "diffusion_pytorch_model.safetensors"),
                            ("umt5-base", "model.safetensors")):
        assert f"{folder}/config.json" in required and f"{folder}/{weights}" in required
    assert "umt5-base/tokenizer.json" in required


def test_a_partial_snapshot_is_not_installed_and_install_resumes_it(monkeypatch):
    afm.reset_download_state()
    cached = {"ace_step_transformer/config.json"}
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, name: name in cached)
    row = afm._hub_row(_entry())
    assert row["installed"] is False
    assert "ace_step_transformer/diffusion_pytorch_model.safetensors" in row["missing_files"]

    started = []
    monkeypatch.setattr(afm, "_run_install", lambda entry, epoch: started.append(entry["id"]))
    monkeypatch.setattr(afm.threading, "Thread",
                        lambda target, args, **k: type("T", (), {"start": lambda self: target(*args)})())
    payload, status = afm.start_download("ace-step")
    assert status == 200 and payload.get("status") == "started" and started == ["ace-step"]

    cached.update(FILES["files"])
    afm.reset_download_state()
    assert afm._hub_row(_entry())["installed"] is True
    payload, status = afm.start_download("ace-step")
    assert payload.get("already_installed") is True
    afm.reset_download_state()


def _daemon():
    spec = importlib.util.spec_from_file_location("run_acestep_under_test",
                                                  PLUGIN / "scripts" / "run_acestep.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_daemon_finds_what_a_partial_snapshot_lacks(tmp_path):
    daemon = _daemon()
    snap = tmp_path / "snapshot"
    for name in FILES["files"]:
        (snap / name).parent.mkdir(parents=True, exist_ok=True)
        (snap / name).write_text("x")
    assert daemon._missing_files(str(snap)) == []

    (snap / "umt5-base" / "model.safetensors").unlink()
    # A snapshot entry whose blob is gone is a dangling symlink.
    weights = snap / "music_vocoder" / "diffusion_pytorch_model.safetensors"
    weights.unlink()
    os.symlink(tmp_path / "blobs" / "gone", weights)
    assert daemon._missing_files(str(snap)) == [
        "music_vocoder/diffusion_pytorch_model.safetensors", "umt5-base/model.safetensors"]


@pytest.fixture
def plugin_import(monkeypatch):
    monkeypatch.syspath_prepend(str(PLUGIN))
    saved = {k: v for k, v in sys.modules.items() if k == "backends" or k.startswith("backends.")}
    for key in saved:
        del sys.modules[key]
    yield importlib.import_module
    for key in [k for k in sys.modules if k == "backends" or k.startswith("backends.")]:
        del sys.modules[key]
    sys.modules.update(saved)


def test_the_plugin_refuses_before_starting_the_daemon(plugin_import, monkeypatch, tmp_path):
    pytest.importorskip("huggingface_hub")
    music = plugin_import("backends.music_gen_acestep")
    hub = plugin_import("backends.hub_weights")
    monkeypatch.setattr(hub, "cached_hub_file",
                        lambda repo, name: "/cache/x" if name == "ace_step_transformer/config.json" else None)

    def no_spawn(*a, **k):
        raise AssertionError("the daemon must not start")

    monkeypatch.setattr(music.subprocess, "Popen", no_spawn)
    with pytest.raises(hub.WeightsNotInstalled, match="Manage models"):
        music.ACEStepBackend(output_root=tmp_path).load()
