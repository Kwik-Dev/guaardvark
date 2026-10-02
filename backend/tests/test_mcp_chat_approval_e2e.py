"""A policy-gated MCP tool, called from chat, goes through the approval card.

The chat engine's tool loop runs for real with the model's reply scripted at
its seam (``_call_llm_streaming``). The tool is the proxy for the stdio fixture
server's ``delete_thing`` (annotated destructive, so the policy says confirm),
registered in the real tool registry. The person's answer travels the way the
browser sends it: a ``chat:tool_approval_response`` event through a Flask-SocketIO
test client into the real handler. The MCP audit log is the record of what
reached the server.
"""
import json

import pytest
from flask import Flask

# Fixtures and the SDK import guard are shared with the MCP client tests.
from backend.tests.test_mcp_client import _import_mcp_sdk, mcp_service, proxied  # noqa: F401

import backend.services.unified_chat_engine as uce

_import_mcp_sdk()

SESSION = "sess-mcp-e2e"
TOOL = "mcp__fx__delete_thing"
CALL = f"<tool_call>\n<tool>{TOOL}</tool>\n<name>report</name>\n</tool_call>"


@pytest.fixture
def socket_client():
    """The app's own Socket.IO instance and handlers, on a bare Flask app."""
    from backend.socketio_instance import socketio
    import backend.socketio_events  # noqa: F401 - registers the handlers

    app = Flask("mcp-approval-e2e")
    socketio.init_app(app)
    client = socketio.test_client(app)
    assert client.is_connected()
    yield client
    client.disconnect()


def _engine(monkeypatch, registry, replies):
    """The scripted-model harness from test_unified_chat_iteration_limit, on the real registry."""
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e.registry = registry
    e.llm = type("_LLM", (), {"model": "test-model"})()
    e.max_iterations = 3
    e._image_data = None
    e._skip_tools = False
    e._brain_state = None
    e.saved = []

    class _Selector:
        def select(self, message, registry):
            return [TOOL]

    e._semantic_selector = _Selector()
    e._load_history = lambda session_id, limit=None: []
    e._load_rules = lambda model_name: "ENGINE PERSONA"
    e._get_routed_tools = lambda message: []
    e._retrieve_rag_context = lambda message: ""
    e._should_skip_rag = lambda message: True
    e._format_interface_context = lambda options: ""
    e._compact_history = lambda history, *a, **k: history
    e._analyze_pasted_image = lambda *a, **k: None
    e._warmup_chat_llm_async = lambda *a, **k: None
    e._maybe_summarize_session = lambda session_id: None
    e._save_message = lambda session_id, role, content, extra_data=None: e.saved.append((role, content))
    e._try_direct_tool = lambda *a, **k: None
    for name in (
        "_try_image_generate_retry", "_try_image_edit_retry", "_try_media_direct",
        "_try_image_edit_direct", "_try_music_video_direct", "_try_film_crew_direct",
        "_try_video_generate_direct", "_try_image_generate_direct",
    ):
        setattr(e, name, lambda *a, **k: None)

    scripted = list(replies)

    def _llm(messages, emit_fn, session_id, emit_tokens=True, max_tokens=768, iteration=1):
        return (scripted.pop(0) if scripted else "Done."), 1, 1

    e._call_llm_streaming = _llm
    e._last_llm_call_meta = {}
    monkeypatch.setattr(uce, "is_aborted", lambda session_id: False)
    monkeypatch.setattr(uce, "match_workstation_direct", lambda message: None)
    monkeypatch.setattr(uce, "APPROVAL_TIMEOUT_S", 5)
    import backend.utils.settings_utils as su
    monkeypatch.setattr(su, "get_setting", lambda key, default=None: default)
    return e


def _turn(engine, client, approve):
    """Run one chat turn; answer the card through the socket like the browser does."""
    events = []

    def emit(name, payload):
        events.append((name, payload))
        if name == "chat:tool_approval_request":
            client.emit("chat:tool_approval_response", {
                "session_id": payload["session_id"], "approved": approve, "approval_scope": "once",
            })

    result = engine._run_chat(SESSION, "delete the report", {"think": False}, emit, "req-e2e", [])
    return result, events


def _delete_calls(service):
    return [e for e in service.get_audit_log(100) if e.get("tool") == "delete_thing"]


def _tool_results(events):
    return [p for n, p in events if n == "chat:tool_result" and p.get("tool") == TOOL]


def test_the_gated_tool_raises_the_card(proxied, mcp_service, socket_client, monkeypatch):
    assert proxied.get_tool(TOOL).requires_approval is True
    engine = _engine(monkeypatch, proxied, [CALL, "It was not deleted."])

    _, events = _turn(engine, socket_client, approve=False)

    cards = [p for n, p in events if n == "chat:tool_approval_request"]
    assert len(cards) == 1
    assert cards[0]["tools"] == [TOOL]
    assert cards[0]["session_id"] == SESSION
    assert cards[0]["tool_details"][0]["tool"] == TOOL


def test_a_denial_never_reaches_the_server(proxied, mcp_service, socket_client, monkeypatch):
    engine = _engine(monkeypatch, proxied, [CALL, "It was not deleted."])

    result, events = _turn(engine, socket_client, approve=False)

    assert _delete_calls(mcp_service) == [], "the server was called after a denial"
    results = _tool_results(events)
    assert results and results[0]["result"]["success"] is False
    assert "USER REJECTED" in results[0]["result"]["error"]
    assert result["response"] == "It was not deleted."


def test_an_approval_runs_the_tool_once(proxied, mcp_service, socket_client, monkeypatch):
    engine = _engine(monkeypatch, proxied, [CALL, "Deleted it."])

    result, events = _turn(engine, socket_client, approve=True)

    calls = _delete_calls(mcp_service)
    assert len(calls) == 1
    assert calls[0]["success"] is True and json.loads(calls[0]["args"]) == {"name": "report"}
    results = _tool_results(events)
    assert results and results[0]["result"]["success"] is True
    assert "deleted report" in (results[0]["result"].get("output") or "")
    assert result["response"] == "Deleted it."


def test_a_once_approval_does_not_carry_to_the_next_turn(proxied, mcp_service, socket_client, monkeypatch):
    engine = _engine(monkeypatch, proxied, [CALL, "Deleted it.", CALL, "Not this time."])
    _turn(engine, socket_client, approve=True)

    _, events = _turn(engine, socket_client, approve=False)

    assert [n for n, _ in events].count("chat:tool_approval_request") == 1
    assert len(_delete_calls(mcp_service)) == 1, "the second call ran without a new yes"


def test_the_chat_context_is_not_sent_to_the_server(proxied, mcp_service, socket_client, monkeypatch):
    """The registry hands tools the chat message and project path as _agent_context;
    an MCP proxy must pass the server only the tool's own arguments."""
    engine = _engine(monkeypatch, proxied, [CALL, "Deleted it."])

    _turn(engine, socket_client, approve=True)

    sent = json.loads(_delete_calls(mcp_service)[0]["args"])
    assert sent == {"name": "report"}
    assert "delete the report" not in json.dumps(sent)
