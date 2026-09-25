"""Root conftest: the bootstrap extension tests need.

backend/tests/conftest.py inserts the repo root on sys.path and sets the
skip flags for its own tree; tests under extensions/<id>/tests/ are outside
that tree and get the same here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PYTEST_SKIP_MIGRATION_CHECK", "1")
os.environ.setdefault("PYTEST_SKIP_LLAMA_CHECK", "1")
os.environ.setdefault("GUAARDVARK_MODE", "test")

import socket

import pytest

# A unit test that reaches the real Ollama loads whatever model is the
# default (a 26B chat model on a workstation), queues behind the user's own
# work, and passes or fails on what happens to be installed. CI has no
# Ollama, so those tests only ever passed on a developer's box. Outside
# tests marked `integration`, a connection to the local Ollama port is
# refused, exactly as it is in CI. GUAARDVARK_TEST_LIVE_OLLAMA=1 lifts it.
_OLLAMA_PORT = int(os.environ.get("GUAARDVARK_TEST_OLLAMA_PORT", "11434"))
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _is_local_ollama(address) -> bool:
    return (isinstance(address, tuple) and len(address) >= 2
            and address[1] == _OLLAMA_PORT and str(address[0]) in _LOCAL_HOSTS)


@pytest.fixture(autouse=True)
def _no_live_ollama(request, monkeypatch):
    if (request.node.get_closest_marker("integration")
            or os.environ.get("GUAARDVARK_TEST_LIVE_OLLAMA") == "1"):
        yield
        return
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def connect(self, address):
        if _is_local_ollama(address):
            raise ConnectionRefusedError(
                f"unit tests do not reach the live Ollama on :{_OLLAMA_PORT}; "
                "mark the test `integration` or mock the call")
        return real_connect(self, address)

    def connect_ex(self, address):
        if _is_local_ollama(address):
            return 111  # ECONNREFUSED
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    yield
