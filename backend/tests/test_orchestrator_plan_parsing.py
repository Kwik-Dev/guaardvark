import json
import os
import sys
import tempfile
import shutil

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.orchestrator_service import (
    OrchestratorService,
    OrchestrationPlan,
    SubTask,
)


VALID_PLAN_JSON = {
    "steps": [
        {
            "id": 1,
            "description": "Scan the project directory",
            "assigned_agent": "code_assistant",
            "dependencies": [],
        },
        {
            "id": 2,
            "description": "Summarize findings",
            "assigned_agent": "general_assistant",
            "dependencies": [1],
        },
    ]
}


class TestParsePlanResponse:
    def test_parses_clean_json(self):
        content = json.dumps(VALID_PLAN_JSON)
        result = OrchestratorService._parse_plan_response(content)
        assert result == VALID_PLAN_JSON

    def test_parses_fenced_json(self):
        content = f"```json\n{json.dumps(VALID_PLAN_JSON)}\n```"
        result = OrchestratorService._parse_plan_response(content)
        assert result == VALID_PLAN_JSON

    def test_parses_json_with_preamble(self):
        content = f"Here is the plan:\n{json.dumps(VALID_PLAN_JSON)}"
        result = OrchestratorService._parse_plan_response(content)
        assert result == VALID_PLAN_JSON

    def test_returns_none_for_invalid_json(self):
        result = OrchestratorService._parse_plan_response('{"steps": [')
        assert result is None

    def test_extract_json_object_balances_nested_braces(self):
        text = 'prefix {"steps": [{"id": 1, "nested": {"a": 1}}]} suffix'
        extracted = OrchestratorService._extract_json_object(text)
        assert extracted is not None
        data = json.loads(extracted)
        assert "steps" in data


class TestNormalizeSteps:
    def test_normalizes_valid_steps(self):
        service = OrchestratorService.__new__(OrchestratorService)
        enabled = {"code_assistant", "general_assistant"}
        subtasks = service._normalize_steps(VALID_PLAN_JSON["steps"], enabled)
        assert len(subtasks) == 2
        assert subtasks[0].id == 1
        assert subtasks[0].assigned_agent == "code_assistant"
        assert subtasks[1].dependencies == [1]

    def test_remaps_unknown_agent(self):
        service = OrchestratorService.__new__(OrchestratorService)
        enabled = {"general_assistant"}
        steps = [
            {
                "id": 1,
                "description": "Do security review",
                "assigned_agent": "security_analyst",
                "dependencies": [],
            }
        ]
        subtasks = service._normalize_steps(steps, enabled)
        assert len(subtasks) == 1
        assert subtasks[0].assigned_agent == "general_assistant"

    def test_skips_incomplete_steps(self):
        service = OrchestratorService.__new__(OrchestratorService)
        enabled = {"general_assistant"}
        steps = [{"id": 1, "description": "missing agent"}]
        subtasks = service._normalize_steps(steps, enabled)
        assert subtasks == []


class TestExecutePlanGuards:
    def test_empty_plan_fails_fast(self):
        temp_dir = tempfile.mkdtemp()
        import backend.config
        old_system_dir = backend.config.SYSTEM_DIR
        backend.config.SYSTEM_DIR = temp_dir
        try:
            service = OrchestratorService()
            plan = OrchestrationPlan(original_request="test", subtasks=[])
            result = service._execute_plan(plan)
            assert result["success"] is False
            assert result["error"] == "Plan has no steps"
            assert plan.status == "failed"
        finally:
            backend.config.SYSTEM_DIR = old_system_dir
            shutil.rmtree(temp_dir)


class TestCreatePlanWithMockLLM:
    @staticmethod
    def _mock_enabled_agents(monkeypatch):
        from backend.services.agent_config import AgentConfig, AgentType

        agents = [
            AgentConfig(
                id="code_assistant",
                name="Code Assistant",
                description="Coding agent",
                agent_type=AgentType.CODE_ASSISTANT,
                tools=["read_code", "search_code"],
                system_prompt="code",
            ),
            AgentConfig(
                id="general_assistant",
                name="General Assistant",
                description="General agent",
                agent_type=AgentType.GENERAL_ASSISTANT,
                tools=["generate_file"],
                system_prompt="general",
            ),
        ]

        class FakeManager:
            def get_enabled_agents(self):
                return agents

        monkeypatch.setattr(
            "backend.services.orchestrator_service.get_agent_config_manager",
            lambda: FakeManager(),
        )

    def test_create_plan_uses_parsed_steps(self, monkeypatch):
        self._mock_enabled_agents(monkeypatch)
        temp_dir = tempfile.mkdtemp()
        import backend.config
        old_system_dir = backend.config.SYSTEM_DIR
        backend.config.SYSTEM_DIR = temp_dir
        try:
            class FakeLLM:
                def __init__(self):
                    self.calls = 0

                def chat(self, messages, format=None):
                    self.calls += 1
                    from backend.utils.llm_service import ChatMessage, MessageRole

                    class FakeMessage:
                        content = json.dumps(VALID_PLAN_JSON)

                    class FakeResponse:
                        message = FakeMessage()

                    return FakeResponse()

            fake_llm = FakeLLM()
            monkeypatch.setattr(
                "backend.services.orchestrator_service.get_default_llm",
                lambda: fake_llm,
            )

            service = OrchestratorService()
            plan = service._create_plan("analyze project")

            assert len(plan.subtasks) == 2
            assert plan.subtasks[0].assigned_agent == "code_assistant"
            assert fake_llm.calls == 1
        finally:
            backend.config.SYSTEM_DIR = old_system_dir
            shutil.rmtree(temp_dir)

    def test_create_plan_retries_on_invalid_json(self, monkeypatch):
        self._mock_enabled_agents(monkeypatch)
        temp_dir = tempfile.mkdtemp()
        import backend.config
        old_system_dir = backend.config.SYSTEM_DIR
        backend.config.SYSTEM_DIR = temp_dir
        try:
            class FakeLLM:
                def __init__(self):
                    self.calls = 0

                def chat(self, messages, format=None):
                    self.calls += 1

                    class FakeMessage:
                        content = (
                            json.dumps(VALID_PLAN_JSON)
                            if self.calls >= 2
                            else '{"steps": ['
                        )

                    class FakeResponse:
                        message = FakeMessage()

                    return FakeResponse()

            fake_llm = FakeLLM()
            monkeypatch.setattr(
                "backend.services.orchestrator_service.get_default_llm",
                lambda: fake_llm,
            )

            service = OrchestratorService()
            plan = service._create_plan("analyze project")

            assert len(plan.subtasks) == 2
            assert fake_llm.calls == 2
        finally:
            backend.config.SYSTEM_DIR = old_system_dir
            shutil.rmtree(temp_dir)


try:
    from flask import Flask
    from backend.api.orchestrator_api import orchestrator_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def orchestrator_client(monkeypatch):
    app = Flask(__name__)
    app.config.update({"TESTING": True})
    app.register_blueprint(orchestrator_bp)

    empty_plan = OrchestrationPlan(original_request="test request", subtasks=[])
    service = OrchestratorService.__new__(OrchestratorService)
    service._active_plans = {}
    service._session_to_plan_id = {}
    service._all_tools = None
    service._save_plans = lambda: None
    service._create_plan = lambda request: empty_plan
    service._serialize_plan = OrchestratorService._serialize_plan.__get__(service, OrchestratorService)
    service._execute_plan = lambda plan, context=None: {
        "success": False,
        "error": "Plan has no steps",
        "plan": service._serialize_plan(plan),
    }

    monkeypatch.setattr(
        "backend.api.orchestrator_api.get_orchestrator",
        lambda: service,
    )
    return app.test_client(), service


def test_create_plan_api_returns_422_for_empty_plan(orchestrator_client):
    client, service = orchestrator_client
    resp = client.post(
        "/api/orchestrator/plan",
        json={"request": "analyze something"},
    )
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["success"] is False
    assert "Failed to generate a valid plan" in data["error"]
    assert service._active_plans == {}


def test_execute_plan_api_returns_422_for_empty_plan(orchestrator_client):
    client, service = orchestrator_client
    plan = OrchestrationPlan(original_request="test", subtasks=[])
    service._active_plans["plan_empty"] = plan

    resp = client.post(
        "/api/orchestrator/execute",
        json={"plan_id": "plan_empty"},
    )
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["success"] is False
    assert data["error"] == "Plan has no steps"


def test_save_orchestrator_plan_message_persists_extra_data(monkeypatch):
    captured = {}

    class FakeMessage:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeSession:
        def add(self, _obj):
            captured["added"] = True

    fake_db = type("DB", (), {"session": FakeSession()})()

    monkeypatch.setattr("backend.models.LLMMessage", FakeMessage)
    monkeypatch.setattr("backend.models.db", fake_db)
    monkeypatch.setattr("backend.utils.db_utils.safe_db_commit", lambda _label: True)
    monkeypatch.setattr("backend.utils.db_utils.safe_db_rollback", lambda _label: None)

    from backend.api.orchestrator_api import _save_orchestrator_plan_message

    _save_orchestrator_plan_message(
        session_id="session_123",
        project_id=7,
        plan_id="plan_abc",
        serialized_plan={"status": "planning", "steps": [{"id": 1}]},
    )

    assert captured["session_id"] == "session_123"
    assert captured["extra_data"]["orchestratorPlanId"] == "plan_abc"
    assert captured["extra_data"]["messageType"] == "orchestrator_plan"
    assert captured.get("added") is True

