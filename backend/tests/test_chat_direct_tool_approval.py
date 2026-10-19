"""The direct (no-model) tool path runs an approved gated tool.

A slash command or direct_tool that names an MCP tool the server policy gates
shows the approval card first; after a yes the call must run as approved, as
it does in the tool loop. Drives the real engine and the real MCP proxy tool;
the MCP service (the external server) is the only stand-in.
"""

import pytest

import backend.services.unified_chat_engine as uce
from backend.services.agent_tools import ToolRegistry
from backend.services.mcp_policy import PolicyDecision

SID = "direct-gated"


def _mcp_tools():
    # Imported when a test runs, not at collection: the backend test conftest
    # puts backend/ on sys.path, where backend/mcp shadows the MCP SDK, and the
    # client service decides once at import whether the SDK is there. Test
    # modules that need the real SDK load it during collection.
    import backend.tools.mcp_tools as mcp_tools
    return mcp_tools


class _FakeMcpService:
    def __init__(self):
        self.calls = []

    def call_tool(self, server, tool, arguments, approved=False, caller=None):
        self.calls.append((server, tool, arguments))
        return {"success": True, "result": {"content": [{"type": "text", "text": "done"}]}}


@pytest.fixture
def gated(monkeypatch):
    mcp_tools = _mcp_tools()
    service = _FakeMcpService()
    monkeypatch.setattr(mcp_tools, "get_mcp_service", lambda: service)
    tool = mcp_tools.MCPProxyTool(
        "fx",
        {"name": "write_file", "description": "Write a file",
         "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                         "required": ["path"]}},
        PolicyDecision(action="confirm", reason="test"),
    )
    registry = ToolRegistry()
    registry.register(tool)
    engine = uce.UnifiedChatEngine(registry, object())
    engine._save_message = lambda *a, **k: None
    monkeypatch.setattr(uce, "APPROVAL_TIMEOUT_S", 1)
    for d in (uce._approval_events, uce._approval_responses, uce._approval_batch_meta,
              uce._session_tool_grants, uce._task_tool_grants):
        d.pop(SID, None)
    return engine, tool, service


def _run(engine, tool, approve):
    events = []

    def emit(event, payload):
        events.append((event, payload))
        if event == "chat:tool_approval_request":
            uce.set_approval_response(SID, approve, approval_id=payload["approval_id"])

    engine._run_direct_tool_execution(tool.name, {"path": "a.txt"}, SID, emit, "req-1", "/write a.txt")
    return events


def test_an_approved_gated_tool_runs_on_the_direct_path(gated):
    engine, tool, service = gated
    events = _run(engine, tool, True)

    assert [e for e, _ in events].count("chat:tool_approval_request") == 1
    assert service.calls == [("fx", "write_file", {"path": "a.txt"})]
    result = [p for e, p in events if e == "chat:tool_result"][-1]["result"]
    assert result["success"] is True, result


def test_a_declined_gated_tool_does_not_run(gated):
    engine, tool, service = gated
    _run(engine, tool, False)
    assert service.calls == []
