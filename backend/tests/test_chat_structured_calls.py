"""Structured tool calls on the XML chat path, and think for models that always reason."""

import os

os.environ.setdefault("GUAARDVARK_MODE", "test")

import pytest

from backend.services import unified_chat_engine as uce
from backend.utils.agent_output_parser import ToolCall


def test_structured_calls_show_in_the_next_prompt():
    calls = [ToolCall(tool_name="search_knowledge_base", parameters={"query": "VNC port"}, reasoning="")]
    assert uce._assistant_turn_text("", calls) == (
        "<tool_call>\n<tool>search_knowledge_base</tool>\n<query>VNC port</query>\n</tool_call>")
    assert uce._assistant_turn_text("I'll search.", calls) == "I'll search."
    assert uce._assistant_turn_text("", []) == ""


@pytest.mark.parametrize("arch,expected", [("gptoss", "low"), ("gemma4", False), ("unknown", False)])
def test_think_off_value_follows_the_architecture(monkeypatch, arch, expected):
    from types import SimpleNamespace

    from backend.services import model_capabilities
    from backend.utils import ollama_resource_manager as orm

    monkeypatch.setattr(model_capabilities, "capabilities_for",
                        lambda tag, with_vision=True: SimpleNamespace(architecture=arch))
    assert orm.think_off_value("some-model:tag") == expected
