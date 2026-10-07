"""The /api/agents endpoints the Agents page uses: list, detail, edit, reset, toggle, run."""

import os
import sys
from types import SimpleNamespace

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

import backend.services.agent_config as agent_config
from backend.api.agents_api import agents_bp
from backend.services.agent_config import NO_AGENT_MATCHES, AgentConfigManager
from backend.services.agent_tools import BaseTool, ToolParameter, ToolRegistry, ToolResult


def _tool(name, approval=False):
    cls = type(f"Tool_{name}", (BaseTool,), {
        "name": name,
        "description": f"{name} reads one thing. Then it says more.",
        "requires_approval": approval,
        "parameters": {"q": ToolParameter(name="q", type="string", required=False, description="input")},
        "execute": lambda self, **kw: ToolResult(success=True, output="ran"),
    })
    return cls()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_config, "AGENT_STATE_FILE", tmp_path / "agent_state.json")
    monkeypatch.setattr("backend.utils.settings_utils.get_web_access", lambda: True)
    return AgentConfigManager()


@pytest.fixture
def registry():
    registry = ToolRegistry()
    for tool in (_tool("web_search"), _tool("fetch_url"), _tool("edit_code", approval=True), _tool("read_code")):
        registry.register(tool)
    return registry


@pytest.fixture
def client(manager, registry, monkeypatch):
    monkeypatch.setattr("backend.api.agents_api._get_config_manager", lambda: manager)
    monkeypatch.setattr("backend.api.agents_api._get_tool_registry", lambda: registry)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(agents_bp)
    return app.test_client()


def test_list_carries_what_a_tile_shows(client):
    body = client.get("/api/agents").get_json()
    assert body["success"] is True
    research = next(a for a in body["agents"] if a["id"] == "research_agent")
    assert research["group"] == "web"
    assert research["summary"]
    assert research["overridden"] == []
    assert research["tools_missing"] == ["analyze_website"]
    assert research["unavailable_reason"] is None
    assert "tools_detail" not in research


def test_detail_lists_each_tool(client):
    agent = client.get("/api/agents/code_assistant").get_json()["agent"]
    by_name = {t["name"]: t for t in agent["tools_detail"]}
    assert by_name["edit_code"] == {"name": "edit_code", "description": "edit_code reads one thing.",
                                    "requires_approval": True, "installed": True}
    assert by_name["search_codebase"]["installed"] is False


def test_detail_unknown_agent(client):
    assert client.get("/api/agents/nope").status_code == 404


class TestPatch:
    def test_valid_edit_returns_the_described_agent(self, client):
        resp = client.patch("/api/agents/research_agent", json={"max_iterations": 4, "model": "small:latest"})
        assert resp.status_code == 200
        agent = resp.get_json()["agent"]
        assert agent["max_iterations"] == 4
        assert agent["model"] == "small:latest"
        assert agent["overridden"] == ["max_iterations", "model"]
        assert "tools_detail" in agent

    @pytest.mark.parametrize("body", [{"max_iterations": 99}, {"system_prompt": ""}, {"tools": []}])
    def test_invalid_edit_is_400_with_the_editable_fields(self, client, manager, body):
        resp = client.patch("/api/agents/research_agent", json=body)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["editable"] == ["enabled", "max_iterations", "system_prompt", "model"]
        assert manager.get_agent("research_agent").max_iterations == 8

    def test_orchestrator_prompt_is_not_editable(self, client):
        resp = client.patch("/api/agents/orchestrator_agent", json={"system_prompt": "plan"})
        assert resp.status_code == 400
        assert resp.get_json()["editable"] == ["enabled"]

    def test_empty_body(self, client):
        assert client.patch("/api/agents/research_agent", data="", content_type="application/json").status_code == 400


class TestReset:
    def test_reset_puts_the_defaults_back(self, client):
        client.patch("/api/agents/research_agent", json={"max_iterations": 4, "enabled": False})
        resp = client.post("/api/agents/research_agent/reset")
        assert resp.status_code == 200
        agent = resp.get_json()["agent"]
        assert agent["max_iterations"] == 8
        assert agent["enabled"] is False
        assert agent["overridden"] == ["enabled"]

    def test_reset_named_fields(self, client):
        client.patch("/api/agents/research_agent", json={"max_iterations": 4, "model": "small:latest"})
        agent = client.post("/api/agents/research_agent/reset", json={"fields": ["model"]}).get_json()["agent"]
        assert agent["model"] is None
        assert agent["max_iterations"] == 4

    def test_reset_rejects_bad_fields(self, client):
        assert client.post("/api/agents/research_agent/reset", json={"fields": "model"}).status_code == 400
        assert client.post("/api/agents/orchestrator_agent/reset",
                           json={"fields": ["system_prompt"]}).status_code == 400

    def test_reset_unknown_agent(self, client):
        assert client.post("/api/agents/nope/reset").status_code == 404


def test_toggle_returns_the_agent(client):
    body = client.post("/api/agents/research_agent/toggle").get_json()
    assert body["enabled"] is False
    assert body["agent"]["enabled"] is False


def test_match_without_general_assistant(client, manager):
    assert client.post("/api/agents/match", json={"message": "summarize this"}).get_json()["agent_id"] == \
        "general_assistant"
    manager.get_agent("general_assistant").enabled = False
    body = client.post("/api/agents/match", json={"message": "summarize this"}).get_json()
    assert body["agent"] is None
    assert body["message"] == NO_AGENT_MATCHES


class TestExecute:
    @pytest.fixture
    def made(self, monkeypatch):
        made = []

        class FakeExecutor:
            def __init__(self, registry, llm, max_iterations=10, agent=None):
                made.append({"tools": registry.list_tools(), "agent": agent, "llm": llm,
                             "max_iterations": max_iterations})

            def execute(self, message, session_context=""):
                made[-1]["message"] = message
                made[-1]["context"] = session_context
                return SimpleNamespace(final_answer="done", iterations=1, success=True, error=None, steps=[])

        monkeypatch.setattr("backend.services.agent_executor.AgentExecutor", FakeExecutor)
        monkeypatch.setattr("backend.utils.llm_service.get_default_llm", lambda: "active-llm")
        return made

    def test_runs_the_agent_with_its_own_tools(self, client, made):
        resp = client.post("/api/agents/execute", json={"agent_id": "research_agent", "message": "find x"})
        assert resp.status_code == 200
        run = made[0]
        assert run["agent"].id == "research_agent"
        assert run["tools"] == ["web_search", "fetch_url"]
        assert run["max_iterations"] == 8
        assert run["llm"] == "active-llm"
        assert run["context"] == ""

    def test_context_goes_in_as_one_line(self, client, made):
        client.post("/api/agents/execute", json={"agent_id": "research_agent", "message": "find x",
                                                 "context": {"client": "Acme"}})
        assert made[0]["context"] == 'User context: {"client": "Acme"}'


class TestModelPin:
    """An agent with a model set runs on it at every entry point; unset means the active model."""

    @pytest.fixture
    def made(self, monkeypatch):
        made = []

        class FakeExecutor:
            def __init__(self, registry, llm, max_iterations=10, agent=None):
                made.append({"llm": llm, "agent": agent})

            def execute(self, message, session_context=""):
                return SimpleNamespace(final_answer="done", iterations=1, success=True, error=None,
                                       steps=[], verified=None)

        built = []

        def get_llm_instance(model=None, **kwargs):
            built.append(model)
            return None if model == "missing:latest" else f"llm:{model}"

        monkeypatch.setattr("backend.services.agent_executor.AgentExecutor", FakeExecutor)
        monkeypatch.setattr("backend.services.orchestrator_service.AgentExecutor", FakeExecutor)
        monkeypatch.setattr("backend.utils.llm_service.get_default_llm", lambda: "active-llm")
        monkeypatch.setattr("backend.utils.llm_service.get_llm_instance", get_llm_instance)
        return SimpleNamespace(runs=made, built=built)

    def test_execute_endpoint(self, client, manager, made):
        manager.update_agent("research_agent", {"model": "small:latest"})
        client.post("/api/agents/execute", json={"agent_id": "research_agent", "message": "find x"})
        assert made.runs[0]["llm"] == "llm:small:latest"
        assert made.built == ["small:latest"]

    def test_unset_uses_the_active_model(self, client, made):
        client.post("/api/agents/execute", json={"agent_id": "research_agent", "message": "find x"})
        assert made.runs[0]["llm"] == "active-llm"
        assert made.built == []

    def test_a_pinned_model_that_will_not_load_is_an_error_not_a_swap(self, client, manager, made):
        manager.update_agent("research_agent", {"model": "missing:latest"})
        resp = client.post("/api/agents/execute", json={"agent_id": "research_agent", "message": "find x"})
        assert resp.status_code == 500
        assert "missing:latest" in resp.get_json()["error"]
        assert made.runs == []

    def test_router_agent_loop(self, manager, registry, made, monkeypatch):
        from backend.services.agent_router import AgentRouter, RouteDecision, RouteType
        manager.update_agent("research_agent", {"model": "small:latest"})
        monkeypatch.setattr("backend.services.agent_config.get_agent_config_manager", lambda: manager)
        router = AgentRouter()
        router._tool_registry = registry
        router._llm = "active-llm"

        out = router._execute_agent_loop(RouteDecision(route_type=RouteType.AGENT_LOOP), "research llm boxes", {})

        assert out["agent_used"] == "research_agent"
        assert made.runs[0]["llm"] == "llm:small:latest"

    def test_router_without_general_assistant(self, manager, registry, made, monkeypatch):
        from backend.services.agent_router import AgentRouter, RouteDecision, RouteType
        manager.get_agent("general_assistant").enabled = False
        monkeypatch.setattr("backend.services.agent_config.get_agent_config_manager", lambda: manager)
        router = AgentRouter()
        router._tool_registry = registry
        router._llm = "active-llm"

        out = router._execute_agent_loop(RouteDecision(route_type=RouteType.AGENT_LOOP), "summarize this", {})

        assert out == {"type": "error", "error": NO_AGENT_MATCHES}
        assert made.runs == []

    def test_orchestrator_step(self, manager, registry, made):
        from backend.services.orchestrator_service import OrchestratorService
        manager.update_agent("research_agent", {"model": "small:latest"})
        service = OrchestratorService.__new__(OrchestratorService)
        service.agent_config_manager = manager
        service.llm = "active-llm"
        service._all_tools = registry

        service._delegate_to_agent("research_agent", "find x", {})
        service._delegate_to_agent("code_assistant", "read a.py", {})

        assert [run["llm"] for run in made.runs] == ["llm:small:latest", "active-llm"]
