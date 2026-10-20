"""Structured tool calls on the XML chat path."""

import os

os.environ.setdefault("GUAARDVARK_MODE", "test")

from backend.services import unified_chat_engine as uce
from backend.utils.agent_output_parser import ToolCall


def test_structured_calls_show_in_the_next_prompt():
    calls = [ToolCall(tool_name="search_knowledge_base", parameters={"query": "VNC port"}, reasoning="")]
    assert uce._assistant_turn_text("", calls) == (
        "<tool_call>\n<tool>search_knowledge_base</tool>\n<query>VNC port</query>\n</tool_call>")
    assert uce._assistant_turn_text("I'll search.", calls) == "I'll search."
    assert uce._assistant_turn_text("", []) == ""
