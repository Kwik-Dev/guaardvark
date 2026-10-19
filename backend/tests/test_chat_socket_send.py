"""chat:send over Socket.IO: what reaches the engine.

Drives the real ``_handle_chat_send_local``; only the Socket.IO emit, the
tool registry factory and the LLM factory are replaced, at the names the
handler looks up when it runs.
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
        self.calls.append(kwargs)
        return ToolResult(success=True, output=f"echo: {kwargs.get('text')}")


class _Room:
    """Collects what the handler emits; ``done`` fires on the turn's last event."""

    def __init__(self):
        self.events = []
        self.done = threading.Event()

    def emit(self, event, payload, room=None):
        self.events.append((event, dict(payload)))
        if event in ("chat:complete", "chat:error"):
            self.done.set()


@pytest.fixture
def socket_turn(monkeypatch):
    import backend.config as config
    import backend.socketio_instance as sio_instance
    import backend.tools.tool_registry_init as registry_init
    import backend.utils.llm_service as llm_service

    room = _Room()
    tool = _EchoTool()
    registry = ToolRegistry()
    registry.register(tool)

    monkeypatch.setattr(sio_instance.socketio, "emit", room.emit, raising=False)
    monkeypatch.setattr(registry_init, "initialize_all_tools", lambda: registry)
    monkeypatch.setattr(llm_service, "get_llm_for_startup", lambda: object())
    # The legacy engine path: no brain_state on this app.
    monkeypatch.setattr(config, "AGENT_BRAIN_ENABLED", False)

    app = Flask("socket_chat_send")
    app.config["LLAMA_INDEX_LLM"] = object()
    return app, room, tool


def test_slash_command_over_socket_takes_the_direct_tool_path(socket_turn, monkeypatch):
    """A direct tool sent over chat:send runs without a model turn."""
    from backend import socketio_events
    from backend.services.unified_chat_engine import UnifiedChatEngine

    app, room, tool = socket_turn
    model_turns = []
    monkeypatch.setattr(
        UnifiedChatEngine, "chat",
        lambda self, *a, **k: model_turns.append(a) or room.done.set(),
    )

    with app.test_request_context("/"):
        socketio_events._handle_chat_send_local({
            "session_id": "sock-direct-1",
            "message": "/echo hi",
            "options": {"direct_tool": "echo_text", "direct_tool_params": {"text": "hi"}},
        })
    assert room.done.wait(10), room.events

    assert model_turns == [], "the slash command fell through to a model turn"
    assert [c.get("text") for c in tool.calls] == ["hi"]
    complete = [p for e, p in room.events if e == "chat:complete"]
    assert complete and complete[-1]["response"] == "echo: hi"
