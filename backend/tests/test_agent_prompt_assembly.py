"""How an agent run's prompt is put together: each part appears once.

The agent's instructions sit in the system prompt only, the tool list is the
run's own registry, the budget is one line per message, memories come from one
place, and whether a run is a screen task depends on the task text or the
agent's tools, never on the agent's instructions.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.agent_config import AgentConfig, AgentType
from backend.services.agent_executor import AgentExecutor, ExtractedFact, FactsRegistry
from backend.services.agent_tools import BaseTool, ToolParameter, ToolRegistry, ToolResult
from backend.services.brain_state import BrainState
from backend.services.step_budget import StepBudget

MARKER = "Unique agent marker 7731."
FINAL = json.dumps({"thoughts": "done", "tool_calls": [], "final_answer": "ok"})


def _tool(name):
    cls = type(
        f"Tool_{name}",
        (BaseTool,),
        {
            "name": name,
            "description": f"{name} does one thing.",
            "parameters": {"q": ToolParameter(name="q", type="string", required=False, description="input")},
            "execute": lambda self, **kw: ToolResult(success=True, output=f"{name} ran"),
        },
    )
    return cls()


def _registry(*names):
    registry = ToolRegistry()
    for name in names:
        registry.register(_tool(name))
    return registry


def _agent(tools, prompt=f"Open pages, take a screenshot of the screen, use the browser. {MARKER}"):
    return AgentConfig(
        id="test_agent", name="Test Agent", description="d",
        agent_type=AgentType.GENERAL_ASSISTANT, tools=list(tools), system_prompt=prompt,
    )


class _LLM:
    """Plays back replies and keeps every message list it was sent."""
    model = ""

    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append([m.content for m in messages])
        return SimpleNamespace(message=SimpleNamespace(content=self._replies.pop(0)))


def _run(registry, llm, query, agent=None, **kwargs):
    executor = AgentExecutor(registry, llm, max_iterations=3, agent=agent)
    executor.coordinator = None
    executor.execute(query, **kwargs)
    return executor


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    BrainState.reset()
    recalled = []

    def search_memories(**kwargs):
        recalled.append(kwargs)
        return [{"content": "The notes live on the shared drive."}]

    monkeypatch.setattr("backend.api.memory_api.search_memories", search_memories)
    monkeypatch.setattr("backend.api.memory_api.get_memories_for_context", lambda **kw: "")
    monkeypatch.setattr("backend.utils.platform.screen_agent_available", lambda: False)
    yield recalled
    BrainState.reset()


@pytest.fixture
def brain():
    """An initialized BrainState whose registry holds tools no agent below owns."""
    state = BrainState.get_instance()
    state.tool_registry = _registry("media_play", "generate_image", "web_search")
    state.system_prompts = {role: "PREFIX\n\n{MEMORY_BLOCK}{DESKTOP_STATE}" for role in ("chat", "agent", "vision")}
    state._app = None
    state._initialized = True
    return state


class TestAgentInstructions:
    def test_once_in_the_system_prompt_and_never_in_the_user_message(self):
        llm = _LLM(FINAL)
        _run(_registry("browser_navigate", "web_search"), llm, "get the title of example.com",
             agent=_agent(["browser_navigate", "web_search"]), session_context='User context: {"site": 1}')

        system, user = llm.calls[0][0], llm.calls[0][-1]
        assert system.count(MARKER) == 1
        assert "AGENT: Test Agent" in system
        assert MARKER not in user
        assert "User context" in user and "User context" not in system

    def test_brain_state_path_carries_them_once(self, brain):
        llm = _LLM(FINAL)
        _run(_registry("browser_navigate"), llm, "get the title of example.com",
             agent=_agent(["browser_navigate"]))

        system, user = llm.calls[0][0], llm.calls[0][-1]
        assert system.startswith("PREFIX")
        assert system.count(MARKER) == 1
        assert MARKER not in user


class TestToolList:
    def test_is_the_runs_own_registry(self, brain):
        llm = _LLM(FINAL)
        _run(_registry("browser_navigate", "web_search"), llm, "look this up",
             agent=_agent(["browser_navigate", "web_search"]))

        system = llm.calls[0][0]
        assert 'Tool: "browser_navigate"' in system
        assert 'Tool: "web_search"' in system
        assert 'Tool: "media_play"' not in system
        assert 'Tool: "generate_image"' not in system

    def test_subset_keeps_the_order_given_and_names_what_is_missing(self):
        full = _registry("a", "b", "c")
        sub, missing = full.subset(["c", "x", "a"])
        assert sub.list_tools() == ["c", "a"]
        assert missing == ["x"]
        assert len(full) == 3


class TestBudget:
    def test_one_budget_line_per_message(self, brain):
        call = json.dumps({"thoughts": "look", "tool_calls": [{"tool_name": "web_search", "parameters": {"q": "x"}}],
                           "final_answer": None})
        llm = _LLM(call, FINAL)
        _run(_registry("web_search"), llm, "find x", budget=StepBudget.from_total(20))

        first_system, first_user = llm.calls[0][0], llm.calls[0][-1]
        second_user = llm.calls[1][-1]
        assert first_system.count("[BUDGET:") == 1
        assert "[BUDGET:" not in first_user
        assert second_user.count("[BUDGET:") == 1

    def test_local_prompt_carries_it_once_without_brain_state(self):
        llm = _LLM(FINAL)
        _run(_registry("web_search"), llm, "find x", budget=StepBudget.from_total(20))
        assert llm.calls[0][0].count("[BUDGET:") == 1
        assert "[BUDGET:" not in llm.calls[0][-1]


class TestFactsAndMemories:
    def test_no_empty_facts_line(self, brain):
        llm = _LLM(FINAL)
        _run(_registry("web_search"), llm, "find x")
        assert "No facts extracted yet" not in llm.calls[0][0]

    def test_facts_block_when_there_are_facts(self, brain):
        facts = FactsRegistry()
        facts.facts.append(ExtractedFact(content="Paris is the capital", source_tool="web_search",
                                         confidence=0.8, iteration=1, raw_evidence="Paris", fact_id=1))
        out = brain.get_system_prompt(role="agent", facts_registry=facts)
        assert "[Fact 1] Paris is the capital" in out
        assert "No facts extracted yet" not in brain.get_system_prompt(role="agent", facts_registry=FactsRegistry())

    def test_without_brain_state_memories_go_in_the_user_message_only(self, _isolated):
        llm = _LLM(FINAL)
        _run(_registry("web_search"), llm, "where are my notes")
        system, user = llm.calls[0][0], llm.calls[0][-1]
        assert len(_isolated) == 1
        assert "The notes live on the shared drive." in user
        assert "The notes live on the shared drive." not in system

    def test_with_brain_state_the_executor_does_not_look_them_up_again(self, brain, _isolated):
        llm = _LLM(FINAL)
        _run(_registry("web_search"), llm, "where are my notes")
        assert _isolated == []
        assert "Saved memories that match this task" not in llm.calls[0][-1]


class TestScreenTask:
    def test_agent_without_the_screen_tool_keeps_its_tools(self, brain):
        # The instructions talk about screenshots and browsers; that must not
        # turn the run into a screen task that hides the agent's own tools.
        llm = _LLM(FINAL)
        executor = _run(_registry("browser_screenshot", "agent_task_execute"), llm, "screenshot example.com",
                        agent=_agent(["browser_screenshot"]))
        system = llm.calls[0][0]
        assert executor._screen is False
        assert 'Tool: "browser_screenshot"' in system
        assert "SCREEN:" not in system

    def test_agent_with_the_screen_tool_is_a_screen_task(self, brain):
        llm = _LLM(FINAL)
        executor = _run(_registry("agent_task_execute", "agent_screen_capture"), llm, "what time is it",
                        agent=_agent(["agent_task_execute", "agent_screen_capture"], prompt="Work the screen."))
        system = llm.calls[0][0]
        assert executor._screen is True
        assert "SCREEN:" in system
        assert "RESPONSE FORMAT" in system
        assert "agent_mode_start first" not in system

    def test_without_an_agent_only_the_task_text_counts(self, brain):
        llm = _LLM(FINAL)
        executor = _run(_registry("agent_task_execute", "web_search", "media_play"), llm, "what is 2 + 2",
                        session_context="Previous attempt (single-shot): use the screen to open firefox")
        assert executor._screen is False
        assert 'Tool: "media_play"' in llm.calls[0][0]

    def test_without_an_agent_a_screen_request_narrows_the_tools(self, brain):
        llm = _LLM(FINAL)
        executor = _run(_registry("agent_task_execute", "web_search", "media_play"), llm,
                        "open firefox and go to youtube")
        system = llm.calls[0][0]
        assert executor._screen is True
        assert 'Tool: "agent_task_execute"' in system
        assert 'Tool: "media_play"' not in system

    def test_tier_2_vision_role_is_not_the_agent_loop(self, brain):
        # The chat engine asks for role="vision" with its XML tool list and
        # parses XML tool calls, so that prompt must not ask for JSON.
        out = brain.get_system_prompt(role="vision", query="what is on the screen",
                                      tool_list="<tool name='agent_task_execute'/>")
        assert "You are controlling a virtual screen" in out
        assert "RESPONSE FORMAT" not in out
        assert "<tool name=" not in out

    def test_static_signature_still_reads_session_context(self):
        assert AgentExecutor._is_vision_task("hello", "use the screen") is True
        assert AgentExecutor._is_vision_task("hello") is False
