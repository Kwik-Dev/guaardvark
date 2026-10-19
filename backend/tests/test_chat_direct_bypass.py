"""The pre-router slash/direct-tool bypass behaves the same on REST and Socket.IO.

Both entry points call slash_command_executor.run_direct_tool_turn. These
tests pin what a user gets from each: the tool runs with no model turn, the
turn is recorded as a direct turn, and an unknown tool is answered by the
direct path rather than routed to the model. Only the Socket.IO emit, the
tool registry factory, the LLM factory and the telemetry writer are replaced,
at the names looked up when the code runs.
"""

import threading

import pytest
from flask import Flask

from backend.services.agent_tools import BaseTool, ToolParameter, ToolRegistry, ToolResult


class _EchoTool(BaseTool):
    name = "echo_text"
    description = "Echo the given text back."
    parameters = {"text": ToolParameter(name="text", type="string", required=True)}

    def __init__(self):
        super().__init__()
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs.get("text"))
        return ToolResult(success=True, output=f"echo: {kwargs.get('text')}")


class _Room:
    def __init__(self):
        self.events = []
        self.done = threading.Event()

    def emit(self, event, payload, room=None):
        self.events.append((event, dict(payload)))
        if event in ("chat:complete", "chat:error"):
            self.done.set()

    def complete(self):
        return [p for e, p in self.events if e == "chat:complete"]


@pytest.fixture
def env(monkeypatch):
    import backend.config as config
    import backend.services.turn_router as turn_router
    import backend.socketio_instance as sio_instance
    import backend.tools.tool_registry_init as registry_init
    import backend.utils.llm_service as llm_service
    from backend.services.unified_chat_engine import UnifiedChatEngine

    room = _Room()
    tool = _EchoTool()
    registry = ToolRegistry()
    registry.register(tool)
    turns, model_turns = [], []
    monkeypatch.setattr(sio_instance.socketio, "emit", room.emit, raising=False)
    monkeypatch.setattr(sio_instance.socketio, "server", object(), raising=False)
    monkeypatch.setattr(registry_init, "initialize_all_tools", lambda: registry)
    monkeypatch.setattr(llm_service, "get_llm_for_startup", lambda: object())
    monkeypatch.setattr(config, "AGENT_BRAIN_ENABLED", False)
    monkeypatch.setattr(turn_router, "direct_turn",
                        lambda display, tool_name, **kw: turns.append((display, tool_name)))
    monkeypatch.setattr(UnifiedChatEngine, "chat",
                        lambda self, *a, **k: model_turns.append(a) or room.done.set())

    import backend.utils.session_ids as session_ids
    try:
        session_ids.max_session_id_length()  # reads the models' column size
    except Exception:  # an environment without the database layer
        monkeypatch.setattr(session_ids, "session_id_refusal", lambda _sid: None)

    from backend.api.unified_chat_api import unified_chat_bp
    app = Flask("direct_bypass")
    app.config["LLAMA_INDEX_LLM"] = object()
    app.register_blueprint(unified_chat_bp)
    return app, room, tool, turns, model_turns


def _rest(app, room, body):
    resp = app.test_client().post("/api/chat/unified", json=body)
    assert resp.status_code == 200, resp.get_json()
    assert room.done.wait(10), room.events


def _socket(app, room, payload):
    from backend import socketio_events
    with app.test_request_context("/"):
        socketio_events._handle_chat_send_local(payload)
    assert room.done.wait(10), room.events


@pytest.mark.parametrize("send", [_rest, _socket], ids=["rest", "socket"])
def test_a_direct_tool_runs_without_a_model_turn_and_is_recorded(env, send):
    app, room, tool, turns, model_turns = env
    send(app, room, {
        "session_id": f"bypass-{send.__name__}",
        "message": "/echo hi",
        "options": {"direct_tool": "echo_text", "direct_tool_params": {"text": "hi"}},
    })
    assert model_turns == []
    assert tool.calls == ["hi"]
    assert room.complete()[-1]["response"] == "echo: hi"
    assert turns == [("/echo hi", "echo_text")]


@pytest.mark.parametrize("send", [_rest, _socket], ids=["rest", "socket"])
def test_an_unknown_direct_tool_is_answered_by_the_direct_path(env, send):
    app, room, _tool, turns, model_turns = env
    send(app, room, {
        "session_id": f"bypass-unknown-{send.__name__}",
        "message": "/nope",
        "options": {"direct_tool": "no_such_tool"},
    })
    assert model_turns == []
    assert room.complete()[-1]["response"] == (
        "Sorry, that didn't work: Tool 'no_such_tool' not found in registry"
    )


def test_options_without_a_direct_tool_route_normally(env):
    app, room, tool, turns, model_turns = env
    _rest(app, room, {"session_id": "bypass-none", "message": "hello there"})
    assert len(model_turns) == 1 and tool.calls == [] and turns == []
