"""POST /api/chat/unified keeps project_root only when it names a folder.

The chat page sends the first /x/y path in a message as project_root when the
text mentions analyze, suggest or review, so "review the endpoint
/api/users/list" arrives with an API route as the root. Only an existing
folder may root GUAARDVARK.md loading and code search for the turn.

Real blueprint through Flask's test client; the engine is replaced by one
that records what it was handed, and the vision pipeline reports no stream.
"""

import threading
import types

import pytest
from flask import Flask

import backend.api.unified_chat_api as api


def test_a_path_that_is_not_a_folder_is_dropped(tmp_path):
    a_file = tmp_path / "notes.txt"
    a_file.write_text("x")

    assert api._project_root_dir("/api/users/list") is None
    assert api._project_root_dir(str(a_file)) is None
    assert api._project_root_dir("") is None
    assert api._project_root_dir(None) is None


def test_an_existing_folder_is_kept(tmp_path):
    assert api._project_root_dir(str(tmp_path)) == str(tmp_path)


@pytest.fixture
def send(monkeypatch):
    import backend.config as config
    import backend.services.unified_chat_engine as uce
    import backend.tools.tool_registry_init as tool_registry_init
    import backend.utils.vision_context_utils as vision_context_utils

    received = []

    class _Engine:
        def __init__(self, *a, **k):
            pass

        def chat(self, session_id, message, options, emit_fn, **kwargs):
            received.append(dict(options))

    started = []

    class _Thread(threading.Thread):
        def start(self):
            started.append(self)
            super().start()

    monkeypatch.setattr(config, "AGENT_BRAIN_ENABLED", False)
    monkeypatch.setattr(tool_registry_init, "initialize_all_tools", lambda: object())
    monkeypatch.setattr(uce, "UnifiedChatEngine", _Engine)
    monkeypatch.setattr(vision_context_utils, "get_vision_context", lambda: None)
    monkeypatch.setattr(api, "_inflight", {})
    monkeypatch.setattr(api, "threading", types.SimpleNamespace(
        Thread=_Thread, get_ident=threading.get_ident,
        current_thread=threading.current_thread, Lock=threading.Lock,
    ))

    app = Flask("test_unified_chat_project_root")
    app.config["LLAMA_INDEX_LLM"] = object()
    app.register_blueprint(api.unified_chat_bp)
    client = app.test_client()

    def _send(session_id, message, options):
        response = client.post("/api/chat/unified", json={
            "session_id": session_id, "message": message, "options": options,
        })
        for thread in started:
            thread.join(timeout=10)
        assert response.status_code == 200
        return received[-1]

    return _send


def test_an_api_route_is_not_sent_as_the_project_root(send):
    options = send("sess-root-1", "review the endpoint /api/users/list",
                   {"project_root": "/api/users/list"})

    assert "project_root" not in options
    assert "projectRoot" not in options


def test_a_camel_case_root_that_is_not_a_folder_is_dropped_too(send):
    options = send("sess-root-2", "review the endpoint /api/users/list",
                   {"projectRoot": "/api/users/list"})

    assert "project_root" not in options
    assert "projectRoot" not in options


def test_a_real_folder_is_sent_as_the_project_root(send, tmp_path):
    options = send("sess-root-3", f"analyze {tmp_path}", {"project_root": str(tmp_path)})

    assert options["project_root"] == str(tmp_path)
