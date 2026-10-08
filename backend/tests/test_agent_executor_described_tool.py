"""A native tool-calling run whose first reply describes a tool instead of
calling it is asked once to call it; the plan is not taken as the answer."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.agent_executor import _ACT_NOW, AgentExecutor
from backend.services.agent_tools import ToolRegistry

PLAN = "To find the title, I should use the **fetch_url** tool.\n\nPlan:\n1. Use fetch_url."


class _NativeLLM:
    """Answers chat_with_tools with plain text and never calls a tool."""

    model = ""

    def __init__(self, *replies):
        self._replies = list(replies)

    def chat_with_tools(self, tools, chat_history=None, user_msg=None, **kwargs):
        return SimpleNamespace(message=SimpleNamespace(content=self._replies.pop(0)))

    def get_tool_calls_from_response(self, response, error_on_no_tool_call=False):
        return []


def _executor(*replies):
    executor = AgentExecutor(ToolRegistry(), _NativeLLM(*replies), max_iterations=3)
    executor._li_tools = [SimpleNamespace(metadata=SimpleNamespace(name=n)) for n in ("fetch_url", "web_search")]
    executor._described_tool_nudged = False
    executor.original_query = "What is the title of example.com?"
    return executor


def test_first_reply_that_describes_a_tool_is_nudged_once():
    executor = _executor(PLAN, PLAN)
    first = executor._execute_iteration_native("q", "sys", 1, None)
    assert first["is_final"] is False
    assert "Call fetch_url now" in first["next_prompt"]
    # A second text reply stands as the answer; the run does not loop.
    second = executor._execute_iteration_native("q", "sys", 2, None)
    assert second["is_final"] is True


def test_plain_answer_without_tool_names_is_final():
    executor = _executor("The title is Example Domain.")
    result = executor._execute_iteration_native("q", "sys", 1, None)
    assert result["is_final"] is True
    assert result["final_answer"] == "The title is Example Domain."


def test_later_answer_that_names_a_used_tool_is_final():
    executor = _executor("fetch_url returned the title Example Domain.")
    result = executor._execute_iteration_native("q", "sys", 2, None)
    assert result["is_final"] is True


def test_first_message_asks_for_action_not_a_plan():
    executor = AgentExecutor(ToolRegistry(), _NativeLLM(), max_iterations=1)
    text = executor._build_initial_prompt("What is the title of example.com?", "")
    assert text.endswith(_ACT_NOW)
    assert "What tools do you need" not in text
