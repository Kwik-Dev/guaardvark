"""Chat tool calls are checked against the same JSON Schema MCP clients see.

Real ToolRegistry.execute_tool with a real tool; nothing is faked. Needs
jsonschema, which backend/requirements.txt installs for the MCP server.
"""
import pytest

pytest.importorskip("jsonschema")

from backend.services.agent_tools import (  # noqa: E402
    STRICT_TOOL_ARGS_ENV,
    BaseTool,
    ToolParameter,
    ToolRegistry,
    ToolResult,
    tool_argument_error,
)


class _ResizeTool(BaseTool):
    name = "resize_thing"
    description = "Resize a thing."
    parameters = {
        "path": ToolParameter(name="path", type="string", required=True),
        "scale": ToolParameter(name="scale", type="int", required=False, minimum=1, maximum=4),
        "mode": ToolParameter(name="mode", type="string", required=False, enum=["fast", "best"]),
    }

    def __init__(self):
        super().__init__()
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(success=True, output="resized")


@pytest.fixture
def registry():
    reg = ToolRegistry()
    tool = _ResizeTool()
    reg.register(tool)
    return reg, tool


def test_the_mcp_validator_names_the_violation(registry):
    _reg, tool = registry
    assert tool_argument_error(tool, {"path": "a.png", "scale": 2}) is None
    assert "scale" in tool_argument_error(tool, {"path": "a.png", "scale": 9})
    assert "mode" in tool_argument_error(tool, {"path": "a.png", "mode": "slow"})
    # The backend's own context keys are not arguments.
    assert tool_argument_error(tool, {"path": "a.png", "_agent_context": {"x": 1}}) is None


def test_strict_mode_refuses_with_a_message_the_model_can_act_on(registry, monkeypatch):
    reg, tool = registry
    monkeypatch.setenv(STRICT_TOOL_ARGS_ENV, "1")
    result = reg.execute_tool("resize_thing", path="a.png", scale=9)
    assert result.success is False
    assert "invalid arguments" in result.error and "scale" in result.error
    assert "scale (int, optional, >= 1, <= 4)" in result.error
    assert tool.calls == []


def test_strict_mode_runs_a_valid_call(registry, monkeypatch):
    reg, tool = registry
    monkeypatch.setenv(STRICT_TOOL_ARGS_ENV, "1")
    assert reg.execute_tool("resize_thing", path="a.png", scale="2").success is True
    assert tool.calls[0]["scale"] == 2  # coerced first, then checked


def test_off_by_default_the_call_still_runs(registry, monkeypatch):
    reg, tool = registry
    monkeypatch.delenv(STRICT_TOOL_ARGS_ENV, raising=False)
    assert reg.execute_tool("resize_thing", path="a.png", scale=9).success is True
    assert len(tool.calls) == 1
