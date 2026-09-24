"""Tool-call parsing and schema-driven parameter coercion."""

import os

os.environ.setdefault("GUAARDVARK_MODE", "test")

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult, coerce_params_to_schema
from backend.utils.agent_output_parser import parse_tool_calls_json


class _Typed(BaseTool):
    name = "typed_tool"
    description = "Typed parameters."
    parameters = {
        "query": ToolParameter(name="query", type="string", required=True, description=""),
        "level": ToolParameter(name="level", type="int", required=False, description=""),
        "ratio": ToolParameter(name="ratio", type="float", required=False, description=""),
        "flag": ToolParameter(name="flag", type="bool", required=False, description=""),
        "opts": ToolParameter(name="opts", type="dict", required=False, description=""),
        "items": ToolParameter(name="items", type="list", required=False, description=""),
    }

    def execute(self, **kwargs):
        return ToolResult(success=True, output=kwargs)


def test_strings_stay_strings():
    out = coerce_params_to_schema({"query": "2024", "level": "50"}, _Typed())
    assert out == {"query": "2024", "level": 50}


def test_json_decoding_for_dict_and_list():
    out = coerce_params_to_schema({"opts": '{"a": 1}', "items": '["x", "y"]', "flag": "yes",
                                   "ratio": "0.5"}, _Typed())
    assert out == {"opts": {"a": 1}, "items": ["x", "y"], "flag": True, "ratio": 0.5}


def test_bare_string_list_becomes_single_item():
    assert coerce_params_to_schema({"items": "*.pdf"}, _Typed()) == {"items": ["*.pdf"]}


def test_unknown_params_untouched_for_typed_tools():
    assert coerce_params_to_schema({"extra": "42"}, _Typed()) == {"extra": "42"}


def test_heuristics_without_schema():
    assert coerce_params_to_schema({"n": "3", "b": "false", "s": "hi"}, None) == {"n": 3, "b": False, "s": "hi"}


def test_parser_accepts_arguments_key_and_json_string():
    resp = parse_tool_calls_json(
        '{"tool_calls": [{"name": "a", "arguments": "{\\"x\\": 1}"},'
        ' {"function": {"name": "b", "arguments": {"y": 2}}}]}'
    )
    assert [(tc.tool_name, tc.parameters) for tc in resp.tool_calls] == [("a", {"x": 1}), ("b", {"y": 2})]
