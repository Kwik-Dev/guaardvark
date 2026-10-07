"""The built-in agents: what they say, which messages reach them, what can be changed."""

import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

import backend.services.agent_config as agent_config
from backend.services.agent_config import DEFAULT_AGENTS, AgentConfigManager

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "agent_state.json"
    monkeypatch.setattr(agent_config, "AGENT_STATE_FILE", path)
    return path


@pytest.fixture
def manager(state_file):
    return AgentConfigManager()


def _registered_tool_names():
    """Every tool class name under backend/tools, read from the source."""
    names = set()
    pattern = re.compile(r'^    name\s*(?::\s*str\s*)?=\s*"([a-z_]+)"', re.MULTILINE)
    for path in (REPO / "backend" / "tools").rglob("*.py"):
        names.update(pattern.findall(path.read_text(encoding="utf-8")))
    return names


# (message, agent that takes it). The first block changed on 2026-10-06.
ROUTING = [
    ("write a product description for my shop", "general_assistant"),
    ("summarize this transcript", "general_assistant"),
    ("classify these emails", "general_assistant"),
    ("yoga classes on tuesday", "general_assistant"),
    ("my dishwasher has a malfunction", "general_assistant"),
    ("decode base64 for me", "general_assistant"),
    ("scan this barcode", "general_assistant"),
    ("the program starts at 7pm, remind me", "general_assistant"),
    ("database migration failed with an error", "general_assistant"),
    ("open the sales.xlsx and total column B", "data_analyst"),
    ("click at coordinates 300, 400", "general_assistant"),
    ("first read config.py then fix the bug", "code_assistant"),
    ("first open firefox then search youtube for cats", "general_assistant"),
    ("/desktop take a note", "desktop_automation"),
    ("/browser get the title of example.com", "browser_automation"),
    # unchanged
    ("plan and execute a product launch", "orchestrator_agent"),
    ("coordinate the research and writing agents", "orchestrator_agent"),
    ("fix the bug in app.py", "code_assistant"),
    ("explain this python function", "code_assistant"),
    ("write a bash script", "code_assistant"),
    ("add a feature that exports reports", "code_assistant"),
    ("remove the logout button", "code_assistant"),
    ("/agent open youtube", "agent_vision_control"),
    ("play some music", "media_control"),
    ("research local llm hardware", "research_agent"),
    ("analyze the data in sales.csv", "data_analyst"),
]


@pytest.mark.parametrize("message,expected", ROUTING)
def test_routing_table(manager, message, expected):
    assert manager.get_agent_for_message(message).id == expected


def test_disabled_general_assistant_means_no_agent(manager):
    manager.get_agent("general_assistant").enabled = False
    assert manager.get_agent_for_message("summarize this transcript") is None
    assert manager.get_agent_for_message("fix the bug in app.py").id == "code_assistant"


@pytest.mark.parametrize("agent_id", sorted(DEFAULT_AGENTS))
def test_every_tool_a_prompt_names_is_one_the_agent_has(agent_id):
    agent = DEFAULT_AGENTS[agent_id]
    known = _registered_tool_names()
    named = {word for word in re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", agent.system_prompt) if word in known}
    assert named <= set(agent.tools), f"{agent_id} names tools it does not have: {named - set(agent.tools)}"


@pytest.mark.parametrize("agent_id", sorted(DEFAULT_AGENTS))
def test_every_tool_an_agent_lists_exists(agent_id):
    agent = DEFAULT_AGENTS[agent_id]
    if agent.agent_type == agent_config.AgentType.ORCHESTRATOR:
        return
    assert set(agent.tools) <= _registered_tool_names()


def test_every_agent_has_a_group_and_summary():
    for agent in DEFAULT_AGENTS.values():
        assert agent.metadata["group"] in {"create", "web", "computer", "routing"}
        assert agent.metadata["summary"]
        assert isinstance(agent.metadata["needs"], list)


def test_orchestrator_has_nothing_to_edit_but_its_switch(state_file):
    orchestrator = DEFAULT_AGENTS["orchestrator_agent"]
    assert orchestrator.system_prompt == ""
    assert orchestrator.editable == ("enabled",)
    state_file.write_text(json.dumps({"orchestrator_agent": {"system_prompt": "old text", "enabled": False}}))

    loaded = AgentConfigManager().get_agent("orchestrator_agent")
    assert loaded.system_prompt == ""
    assert loaded.enabled is False


def test_overridden_fields(manager):
    agent = manager.get_agent("research_agent")
    assert agent_config.overridden_fields(agent) == []
    agent.max_iterations = 3
    agent.model = "small:latest"
    assert agent_config.overridden_fields(agent) == ["max_iterations", "model"]


class TestReadiness:
    def test_web_access_off(self, monkeypatch, manager):
        monkeypatch.setattr("backend.utils.settings_utils.get_web_access", lambda: False)
        assert agent_config.agent_readiness(manager.get_agent("research_agent")) == "Web access is off in Settings"

    def test_web_access_on(self, monkeypatch, manager):
        monkeypatch.setattr("backend.utils.settings_utils.get_web_access", lambda: True)
        assert agent_config.agent_readiness(manager.get_agent("research_agent")) is None

    def test_desktop_switch(self, monkeypatch, manager):
        import backend.config
        monkeypatch.setattr(backend.config, "DESKTOP_AUTOMATION_ENABLED", False)
        reason = agent_config.agent_readiness(manager.get_agent("desktop_automation"))
        assert "GUAARDVARK_DESKTOP_AUTOMATION=true" in reason

    def test_screen_needs_linux(self, monkeypatch, manager):
        monkeypatch.setattr("backend.utils.platform.screen_agent_available", lambda: False)
        assert agent_config.agent_readiness(manager.get_agent("agent_vision_control")) == "The agent screen needs Linux"

    def test_no_needs(self, manager):
        assert agent_config.agent_readiness(manager.get_agent("code_assistant")) is None
