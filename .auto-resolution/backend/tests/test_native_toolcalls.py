#!/usr/bin/env python3
"""Tests for the native Ollama function-calling path (Lever 1).

Covers the previously-untested pieces flagged in the validate-before-enabling
checklist (claude-mem obs #1032):
  - ToolRegistry.as_ollama_tools  -> correct JSON-Schema shape + type map + allow-list
  - model_supports_tools          -> reads /api/show capabilities (mocked)
  - UnifiedChatEngine._native_tool_calls_to_response
        -> dict args, JSON-string args, malformed args, nameless call dropped,
           empty calls -> final_answer (loop-terminating parity with XML path)

These are deterministic / no live Ollama. Live validation lives in a separate
script (scripts/validate_native_toolcalls.py).
"""

from unittest.mock import patch

import pytest

from backend.services.agent_tools import ToolRegistry, BaseTool, ToolParameter, ToolResult


# ── Fixtures: a tiny registry with two tools ────────────────────────────────
class _SearchTool(BaseTool):
    name = "web_search"
    description = "Search the web. Args: query"
    parameters = {
        "query": ToolParameter(name="query", type="string", required=True,
                               description="Search query"),
        "limit": ToolParameter(name="limit", type="int", required=False,
                               description="Max results"),
    }

    def execute(self, **kwargs):
        return ToolResult(success=True, output="ok")


class _FlagTool(BaseTool):
    name = "set_flag"
    description = "Set a boolean flag. Args: on"
    parameters = {
        "on": ToolParameter(name="on", type="bool", required=True, description="Flag value"),
    }

    def execute(self, **kwargs):
        return ToolResult(success=True, output="ok")


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register(_SearchTool())
    r.register(_FlagTool())
    return r


# ── as_ollama_tools ─────────────────────────────────────────────────────────
def test_as_ollama_tools_schema_shape(registry):
    tools = registry.as_ollama_tools()
    assert isinstance(tools, list) and len(tools) == 2
    search = next(t for t in tools if t["function"]["name"] == "web_search")
    assert search["type"] == "function"
    fn = search["function"]
    assert fn["description"] == "Search the web. Args: query"
    params = fn["parameters"]
    assert params["type"] == "object"
    # type mapping: string stays string, int -> integer, bool -> boolean
    assert params["properties"]["query"]["type"] == "string"
    assert params["properties"]["limit"]["type"] == "integer"
    # required reflects ToolParameter.required (query required, limit not)
    assert params["required"] == ["query"]


def test_as_ollama_tools_bool_type_map(registry):
    tools = registry.as_ollama_tools(tool_names=["set_flag"])
    assert len(tools) == 1
    props = tools[0]["function"]["parameters"]["properties"]
    assert props["on"]["type"] == "boolean"


def test_as_ollama_tools_allow_list_filters_and_orders(registry):
    # explicit allow-list emits only named tools, preserving order
    tools = registry.as_ollama_tools(tool_names=["set_flag", "web_search"])
    names = [t["function"]["name"] for t in tools]
    assert names == ["set_flag", "web_search"]
    # unknown names are silently skipped (no crash)
    tools = registry.as_ollama_tools(tool_names=["nope", "web_search"])
    assert [t["function"]["name"] for t in tools] == ["web_search"]


# ── model_supports_tools (mock /api/show) ───────────────────────────────────
def test_model_supports_tools_true():
    from backend.utils import ollama_resource_manager as orm
    with patch.object(orm, "get_model_info", return_value={"capabilities": ["completion", "tools"]}):
        assert orm.model_supports_tools("qwen2.5:14b") is True


def test_model_supports_tools_false_for_completion_only():
    from backend.utils import ollama_resource_manager as orm
    with patch.object(orm, "get_model_info", return_value={"capabilities": ["completion"]}):
        assert orm.model_supports_tools("llama3:latest") is False


def test_model_supports_tools_false_when_info_missing():
    from backend.utils import ollama_resource_manager as orm
    with patch.object(orm, "get_model_info", return_value=None):
        assert orm.model_supports_tools("ghost:latest") is False


# ── _native_tool_calls_to_response ──────────────────────────────────────────
@pytest.fixture
def engine():
    """A UnifiedChatEngine instance WITHOUT running its heavy __init__.

    _native_tool_calls_to_response uses no instance state (only module logger +
    local imports), so __new__ is a safe, fast way to exercise the method.
    """
    from backend.services.unified_chat_engine import UnifiedChatEngine
    return UnifiedChatEngine.__new__(UnifiedChatEngine)


def test_native_convert_dict_args(engine):
    native = [{"function": {"name": "web_search", "arguments": {"query": "cats"}}}]
    resp = engine._native_tool_calls_to_response(native, "let me search")
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.tool_name == "web_search"
    assert tc.parameters == {"query": "cats"}
    assert resp.final_answer is None
    # streamed content preserved as thoughts
    assert resp.thoughts == "let me search"


def test_native_convert_json_string_args(engine):
    # some runners emit arguments as a JSON string
    native = [{"function": {"name": "web_search", "arguments": '{"query": "dogs"}'}}]
    resp = engine._native_tool_calls_to_response(native, "")
    assert resp.tool_calls[0].parameters == {"query": "dogs"}


def test_native_convert_malformed_args_falls_back_to_raw(engine):
    native = [{"function": {"name": "web_search", "arguments": "not json"}}]
    resp = engine._native_tool_calls_to_response(native, "")
    assert resp.tool_calls[0].parameters == {"_raw": "not json"}


def test_native_convert_nameless_call_dropped(engine):
    native = [
        {"function": {"name": "", "arguments": {}}},
        {"function": {"name": "set_flag", "arguments": {"on": True}}},
    ]
    resp = engine._native_tool_calls_to_response(native, "")
    assert [tc.tool_name for tc in resp.tool_calls] == ["set_flag"]


def test_native_no_calls_becomes_final_answer(engine):
    # No structured calls -> streamed content is the final answer; loop terminates
    resp = engine._native_tool_calls_to_response([], "Here is your answer.")
    assert resp.tool_calls == []
    assert resp.final_answer == "Here is your answer."


def test_native_empty_everything_is_safe(engine):
    resp = engine._native_tool_calls_to_response([], "")
    assert resp.tool_calls == []
    assert resp.final_answer is None
