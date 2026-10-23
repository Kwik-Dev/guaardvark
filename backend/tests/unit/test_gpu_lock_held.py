"""lock_held reports the exclusive GPU lock from the lock file alone."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from backend.services.gpu_resource_coordinator import GPUResourceCoordinator


@pytest.fixture
def coordinator(tmp_path, monkeypatch):
    """A coordinator whose lock file lives in tmp_path and that never asks systemctl."""
    coord = GPUResourceCoordinator.__new__(GPUResourceCoordinator)
    coord._initialized = True
    coord._internal_lock = __import__("threading").RLock()
    coord.LOCK_FILE = tmp_path / "gpu_lock.json"
    monkeypatch.setattr(GPUResourceCoordinator, "_is_ollama_running", lambda self: False)
    monkeypatch.setattr(GPUResourceCoordinator, "_stop_ollama", lambda self: True)
    monkeypatch.setattr(GPUResourceCoordinator, "_notify_vision_pipeline", lambda self, *a: None)
    return coord


def test_no_lock_file_is_not_held(coordinator):
    assert coordinator.lock_held() is False


def test_an_acquired_generic_lock_is_held_until_released(coordinator):
    assert coordinator.acquire_generic("image_gen")["success"] is True
    assert coordinator.lock_held() is True
    coordinator.release_generic("image_gen")
    assert coordinator.lock_held() is False


def test_a_video_lock_is_held(coordinator):
    assert coordinator.acquire_for_video_generation(batch_id="b1")["success"] is True
    assert coordinator.lock_held() is True


def test_an_expired_lease_is_not_held(coordinator):
    coordinator.acquire_generic("image_gen", lease_seconds=60)
    info = coordinator._read_lock_file()
    info.lease_expires_at = (datetime.now() - timedelta(seconds=1)).isoformat()
    coordinator._write_lock_file(info)
    assert coordinator.lock_held() is False


def test_a_dead_holder_is_not_held(coordinator, monkeypatch):
    coordinator.acquire_generic("image_gen")
    monkeypatch.setattr(GPUResourceCoordinator, "_is_process_alive", lambda self, pid: False)
    assert coordinator.lock_held() is False


def test_an_unreadable_lease_time_is_held(coordinator):
    coordinator.acquire_generic("image_gen")
    info = coordinator._read_lock_file()
    info.lease_expires_at = "not a time"
    coordinator._write_lock_file(info)
    assert coordinator.lock_held() is True


def test_lock_held_runs_no_subprocess(coordinator, monkeypatch):
    import subprocess

    def refuse(*a, **k):
        raise AssertionError("lock_held must not run a subprocess")

    coordinator.acquire_generic("image_gen")
    monkeypatch.setattr(subprocess, "run", refuse)
    assert coordinator.lock_held() is True
    assert os.getpid() == coordinator._read_lock_file().pid
