"""Tool parameter coercion: what the tool receives on each path.

The ReACT loop coerces with coerce_params_to_schema (via
UnifiedChatEngine._normalize_parameters) and then ToolRegistry.execute_tool
coerces again with _coerce_params. The direct path and MCP calls get only the
second. The two disagree on some inputs; these tests pin today's answer on
both paths so a consolidation cannot change either silently.
"""
import pytest

import backend.services.unified_chat_engine as uce
from backend.services.agent_tools import BaseTool, ToolParameter, ToolRegistry, ToolResult


class _Probe(BaseTool):
    name = "probe"
    description = "Record what arrives."
    parameters = {
        "tags": ToolParameter(name="tags", type="list", required=False),
        "count": ToolParameter(name="count", type="int", required=False),
        "flag": ToolParameter(name="flag", type="bool", required=False),
        "label": ToolParameter(name="label", type="string", required=False),
    }

    def __init__(self):
        super().__init__()
        self.got = None

    def execute(self, **kwargs):
        self.got = {k: v for k, v in kwargs.items() if not k.startswith("_")}
        return ToolResult(success=True, output="ok")


@pytest.fixture
def setup():
    reg = ToolRegistry()
    tool = _Probe()
    reg.register(tool)
    engine = uce.UnifiedChatEngine(reg, object())
    return reg, tool, engine


def _react(setup, params):
    reg, tool, engine = setup
    reg.execute_tool("probe", **engine._normalize_parameters(params, "probe"))
    return tool.got


def _direct(setup, params):
    reg, tool, _engine = setup
    reg.execute_tool("probe", **params)
    return tool.got


CASES = [
    # (params, ReACT loop result, direct/MCP result)
    ({"tags": "a, b"}, {"tags": ["a, b"]}, {"tags": ["a", "b"]}),
    ({"count": "2.5"}, {"count": 2}, {"count": "2.5"}),
    ({"flag": "t"}, {"flag": False}, {"flag": True}),
    ({"tags": ""}, {"tags": None}, {"tags": []}),
    ({"label": 5}, {"label": "5"}, {"label": "5"}),
    # Where they agree
    ({"count": "7"}, {"count": 7}, {"count": 7}),
    ({"tags": '["x","y"]'}, {"tags": ["x", "y"]}, {"tags": ["x", "y"]}),
    ({"flag": "yes"}, {"flag": True}, {"flag": True}),
]


@pytest.mark.parametrize("params,react,direct", CASES)
def test_coercion_per_path(setup, params, react, direct):
    assert _react(setup, dict(params)) == react
    assert _direct(setup, dict(params)) == direct
