"""Admission reclaim asks the Audio Foundry sidecar to unload what it holds.

The sidecar owns its models' CUDA memory; the orchestrator only tracks its slots
and forgets them on a backend restart. These tests fake the sidecar's HTTP routes
at the ``requests`` calls the function makes, so no real sidecar is ever touched.
"""
from __future__ import annotations

import pytest
import requests

import backend.services.gpu_resource_policy as grp


class _Resp:
    def __init__(self, body, ok=True):
        self._body = body
        self.ok = ok

    def json(self):
        return self._body


@pytest.fixture
def sidecar(monkeypatch):
    state = {
        "backends": {
            "fx": {"loaded": False},
            "voice": {"loaded": True},
            "music": {"loaded": True},
        },
        "busy": set(),
        "posts": [],
        "down": False,
    }

    def fake_get(url, timeout=None):
        if state["down"]:
            raise requests.ConnectionError("refused")
        assert url.endswith("/status")
        return _Resp({"backends": state["backends"]})

    def fake_post(url, timeout=None):
        intent = url.rsplit("/", 1)[-1]
        state["posts"].append(intent)
        return _Resp({"intent": intent, "unloaded": intent not in state["busy"]})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    return state


def test_every_loaded_backend_is_asked_to_unload(sidecar):
    assert grp.evict_audio_foundry_backends() == ["voice", "music"]
    assert sidecar["posts"] == ["voice", "music"]


def test_a_backend_that_is_generating_stays_loaded(sidecar):
    sidecar["busy"].add("music")
    assert grp.evict_audio_foundry_backends() == ["voice"]
    assert sidecar["posts"] == ["voice", "music"]


def test_a_stopped_sidecar_is_skipped_quietly(sidecar):
    sidecar["down"] = True
    assert grp.evict_audio_foundry_backends() == []
    assert sidecar["posts"] == []


def test_a_malformed_status_never_raises(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _Resp(None))
    assert grp.evict_audio_foundry_backends() == []
