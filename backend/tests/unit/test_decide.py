"""decide(): today's answer unless a qualified, loaded model may answer.

The seams are module-level in nl_control_plane: ``_resident`` (is the chat
model loaded), ``gpu_busy`` (is GPU work running), ``_chat`` (the model call)
and ``_chat_model`` (which model the turn uses). Every test also counts calls
on ollama.chat, ollama.Client, requests and socket.connect: json_chat
swallows exceptions, so a stub that raised would prove nothing, while a count
of zero proves nothing was called.
"""
from __future__ import annotations

import socket

import ollama
import pytest
import requests

from backend.services import decision_settings
from backend.services import nl_control_plane as ncp
from backend.services.decision_data import DECISIONS

NETWORK = ("ollama.chat", "ollama.Client", "requests", "socket.connect")


@pytest.fixture
def network(monkeypatch):
    """Counts every way decide() could reach Ollama or the network."""
    counts = dict.fromkeys(NETWORK, 0)

    def counter(name):
        def call(*args, **kwargs):
            counts[name] += 1
            raise ConnectionError(f"{name} is not reachable in this test")
        return call

    monkeypatch.setattr(ollama, "chat", counter("ollama.chat"))
    monkeypatch.setattr(ollama, "Client", counter("ollama.Client"))
    for verb in ("get", "post", "put", "patch", "delete", "head", "request"):
        monkeypatch.setattr(requests, verb, counter("requests"))
    monkeypatch.setattr(requests.Session, "request", counter("requests"))
    monkeypatch.setattr(socket.socket, "connect", counter("socket.connect"))
    monkeypatch.setattr(ncp, "_clients", {})
    return counts


@pytest.fixture
def modes(monkeypatch):
    """Decision modes for the test; anything unset is off."""
    table: dict = {}
    monkeypatch.setattr(decision_settings, "get_decision_mode", lambda d: table.get(d, "off"))
    return table


def test_off_returns_the_fallback_and_calls_nothing(network, modes):
    result = ncp.decide("needs_live_data", {"message": "weather in Paris today?"}, fallback=True)
    assert result.answer is True
    assert (result.source, result.reason) == ("fallback", "off")
    assert result.model is None
    assert network == dict.fromkeys(NETWORK, 0)


@pytest.mark.parametrize("decision_id", sorted(DECISIONS))
def test_every_decision_is_off_by_default(network, monkeypatch, decision_id):
    """With no settings row and no override, the real mode store reads off."""
    monkeypatch.delenv("GUAARDVARK_DECISION_MODES", raising=False)
    monkeypatch.setattr(decision_settings, "_read_row", lambda key: None)
    decision_settings.clear_cache()
    result = ncp.decide(decision_id, {}, fallback="today")
    decision_settings.clear_cache()
    assert (result.answer, result.reason) == ("today", "off")
    assert network == dict.fromkeys(NETWORK, 0)


def test_an_undeclared_decision_returns_the_fallback(network, modes):
    result = ncp.decide("no_such_decision", {"message": "x"}, fallback=42)
    assert (result.answer, result.source, result.reason) == (42, "fallback", "unknown")
    assert network == dict.fromkeys(NETWORK, 0)


def test_decide_never_raises(network, monkeypatch):
    def broken(decision_id):
        raise RuntimeError("settings store exploded")

    monkeypatch.setattr(decision_settings, "get_decision_mode", broken)
    result = ncp.decide("needs_live_data", {"message": "x"}, fallback=False)
    assert (result.answer, result.reason) == (False, "error")


def test_a_recorder_failure_does_not_change_the_answer(network, modes, monkeypatch):
    def recorder(event, state):
        raise RuntimeError("disk full")

    modes["needs_live_data"] = "shadow"
    monkeypatch.setattr(ncp, "_recorder", recorder)
    result = ncp.decide("needs_live_data", {"message": "x"}, fallback=False)
    assert (result.answer, result.reason) == (False, "shadow")


def test_off_records_nothing(network, modes, monkeypatch):
    seen = []
    monkeypatch.setattr(ncp, "_recorder", lambda event, state: seen.append(event))
    ncp.decide("needs_live_data", {"message": "x"}, fallback=False)
    assert seen == []


def test_the_record_carries_a_hash_not_the_text(network, modes, monkeypatch):
    seen = []
    modes["needs_live_data"] = "shadow"
    monkeypatch.setattr(ncp, "_recorder", lambda event, state: seen.append(event))
    ncp.decide("needs_live_data", {"message": "my private sentence"}, fallback=False)
    (event,) = seen
    assert "my private sentence" not in repr(event)
    assert len(event["state_sha256"]) == 16
