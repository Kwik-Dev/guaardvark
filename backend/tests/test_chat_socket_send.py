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


# A 1x1 PNG, base64: enough for the server to save it as a chat picture.
_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture
def brain_turn(socket_turn, monkeypatch, tmp_path):
    """chat:send routed to AgentBrain, with process() recording what it got."""
    import backend.config as config
    from backend.services.agent_brain import AgentBrain

    app, room, _tool = socket_turn
    monkeypatch.setattr(config, "AGENT_BRAIN_ENABLED", True)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path), raising=False)
    app.brain_state = type("State", (), {"is_ready": True})()
    calls = []

    def process(self, **kwargs):
        calls.append(kwargs)
        room.done.set()

    monkeypatch.setattr(AgentBrain, "process", process)
    return app, room, calls


def _send(app, room, payload):
    from backend import socketio_events

    with app.test_request_context("/"):
        socketio_events._handle_chat_send_local(payload)
    assert room.done.wait(10), room.events


def test_an_image_turn_over_socket_reaches_the_brain_with_its_saved_url(brain_turn, tmp_path):
    app, room, calls = brain_turn
    _send(app, room, {"session_id": "sock-img-1", "message": "what is this?", "image": _PNG})

    kwargs = calls[0]
    assert kwargs["image_data"] == _PNG
    url = kwargs["image_url"]
    assert url and url.startswith("/api/enhanced-chat/vision/image/chat_image_")
    assert (tmp_path / "chat_images" / url.rsplit("/", 1)[-1]).is_file()


def test_a_voice_turn_over_socket_reaches_the_brain_as_voice(brain_turn):
    app, room, calls = brain_turn
    _send(app, room, {"session_id": "sock-voice-1", "message": "hello there", "is_voice_message": True})

    assert calls[0]["is_voice_message"] is True


def test_a_second_chat_send_does_not_run_beside_a_live_turn(socket_turn, monkeypatch):
    """chat:send shares the REST in-flight slot: no two turns on one session at once."""
    import backend.config as config
    from backend import socketio_events
    from backend.api import unified_chat_api as api
    from backend.services.agent_brain import AgentBrain

    app, room, _tool = socket_turn
    monkeypatch.setattr(config, "AGENT_BRAIN_ENABLED", True)
    monkeypatch.setattr(api, "INFLIGHT_GRACE_S", 0.2)
    app.brain_state = type("State", (), {"is_ready": True})()
    release = threading.Event()
    started = []
    running = threading.Semaphore(0)

    def process(self, **kwargs):
        started.append(kwargs["message"])
        running.release()
        release.wait(10)

    monkeypatch.setattr(AgentBrain, "process", process)
    sid = "sock-serial-1"
    try:
        with app.test_request_context("/"):
            socketio_events._handle_chat_send_local({"session_id": sid, "message": "first"})
            assert running.acquire(timeout=5)
            socketio_events._handle_chat_send_local({"session_id": sid, "message": "second"})
        assert started == ["first"]
        errors = [p for e, p in room.events if e == "chat:error"]
        assert errors and "still running" in errors[-1]["error"]
    finally:
        release.set()
        with api._inflight_lock:
            t = api._inflight.get(sid)
        if t is not None:
            t.join(5)
