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


class TestUpdateValidation:
    @pytest.mark.parametrize("updates,message", [
        ({"max_iterations": 0}, "from 1 to 50"),
        ({"max_iterations": 51}, "from 1 to 50"),
        ({"max_iterations": True}, "from 1 to 50"),
        ({"max_iterations": "10"}, "from 1 to 50"),
        ({"enabled": "yes"}, "true or false"),
        ({"system_prompt": "   "}, "Reset to default"),
        ({"system_prompt": "x" * 20001}, "20,000"),
        ({"model": "m" * 201}, "200"),
        ({"tools": ["execute_python"]}, "'tools' cannot be changed"),
        ({"trigger_patterns": [".*"]}, "editable: enabled, max_iterations, system_prompt, model"),
    ])
    def test_rejects(self, manager, state_file, updates, message):
        before = manager.get_agent("research_agent").to_dict()
        with pytest.raises(ValueError, match=re.escape(message)):
            manager.update_agent("research_agent", updates)
        assert manager.get_agent("research_agent").to_dict() == before
        assert not state_file.exists()

    def test_a_bad_field_changes_nothing_else_in_the_request(self, manager):
        with pytest.raises(ValueError):
            manager.update_agent("research_agent", {"max_iterations": 4, "tools": []})
        assert manager.get_agent("research_agent").max_iterations == DEFAULT_AGENTS["research_agent"].max_iterations

    def test_accepts_and_saves(self, manager, state_file):
        assert manager.update_agent("research_agent", {"max_iterations": 50, "model": "  small:latest ",
                                                       "system_prompt": "Search well."}) is True
        agent = manager.get_agent("research_agent")
        assert (agent.max_iterations, agent.model, agent.system_prompt) == (50, "small:latest", "Search well.")
        saved = json.loads(state_file.read_text())["research_agent"]
        assert saved == {"max_iterations": 50, "system_prompt": "Search well.", "model": "small:latest"}

    def test_empty_model_means_the_active_model(self, manager):
        manager.update_agent("research_agent", {"model": "small:latest"})
        manager.update_agent("research_agent", {"model": ""})
        assert manager.get_agent("research_agent").model is None

    def test_orchestrator_only_switches(self, manager):
        with pytest.raises(ValueError, match="editable: enabled"):
            manager.update_agent("orchestrator_agent", {"system_prompt": "plan harder"})
        assert manager.update_agent("orchestrator_agent", {"enabled": False}) is True

    def test_unknown_agent(self, manager):
        assert manager.update_agent("no_such_agent", {"enabled": False}) is False


class TestReset:
    def test_default_fields_keep_the_switch(self, manager, state_file):
        manager.update_agent("research_agent", {"enabled": False, "max_iterations": 3,
                                                "system_prompt": "edited", "model": "small:latest"})
        assert manager.reset_agent("research_agent") is True
        agent, default = manager.get_agent("research_agent"), DEFAULT_AGENTS["research_agent"]
        assert agent.system_prompt == default.system_prompt
        assert agent.max_iterations == default.max_iterations
        assert agent.model is None
        assert agent.enabled is False
        assert json.loads(state_file.read_text()) == {"research_agent": {"enabled": False}}

    def test_named_fields_only(self, manager):
        manager.update_agent("research_agent", {"max_iterations": 3, "system_prompt": "edited"})
        manager.reset_agent("research_agent", ["max_iterations"])
        agent = manager.get_agent("research_agent")
        assert agent.max_iterations == DEFAULT_AGENTS["research_agent"].max_iterations
        assert agent.system_prompt == "edited"

    def test_rejects_fields_that_are_not_editable(self, manager):
        with pytest.raises(ValueError, match="Cannot reset system_prompt"):
            manager.reset_agent("orchestrator_agent", ["system_prompt"])

    def test_orchestrator_default_reset_touches_nothing(self, manager):
        manager.update_agent("orchestrator_agent", {"enabled": False})
        assert manager.reset_agent("orchestrator_agent") is True
        assert manager.get_agent("orchestrator_agent").enabled is False


class TestDescribe:
    def test_list_fields(self, manager, monkeypatch):
        from backend.services.agent_tools import ToolRegistry
        monkeypatch.setattr("backend.utils.settings_utils.get_web_access", lambda: True)
        agent = manager.get_agent("research_agent")
        manager.update_agent("research_agent", {"max_iterations": 3})
        data = manager.describe(agent, ToolRegistry())
        assert data["group"] == "web"
        assert data["summary"] == "Searches and reads the web"
        assert data["overridden"] == ["max_iterations"]
        assert data["editable"] == ["enabled", "max_iterations", "system_prompt", "model"]
        assert data["tools_missing"] == ["web_search", "fetch_url", "analyze_website"]
        assert data["unavailable_reason"] == "None of its tools are available"
        assert "tools_detail" not in data

    def test_orchestrator_tool_is_not_missing(self, manager):
        from backend.services.agent_tools import ToolRegistry
        data = manager.describe(manager.get_agent("orchestrator_agent"), ToolRegistry(), detail=True)
        assert data["tools_missing"] == []
        assert data["unavailable_reason"] is None
        assert data["tools_detail"][0]["installed"] is True
