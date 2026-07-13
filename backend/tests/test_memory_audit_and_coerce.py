"""Tests for memory audit logging and list param coercion."""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask
from backend.models import db
from backend.api.memory_api import memory_bp
from backend.services.agent_tools import ToolRegistry, ToolParameter, BaseTool, ToolResult, _coerce_list_param


@pytest.fixture(autouse=True)
def _reset_memory_audit_logger():
    from backend.utils import memory_audit_log as mal

    mal._LOGGER = None
    yield
    mal._LOGGER = None


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(memory_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


class _StubTool(BaseTool):
    name = "stub"
    description = "stub"
    parameters = {
        "tags": ToolParameter(name="tags", type="list", required=False, default=[]),
    }

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, output=str(kwargs.get("tags")))


class TestCoerceListParam:
    def test_json_array_string(self):
        assert _coerce_list_param('["a", "b"]') == ["a", "b"]

    def test_comma_separated_string(self):
        assert _coerce_list_param("GUAARDVARK, Architecture, Project Context") == [
            "GUAARDVARK",
            "Architecture",
            "Project Context",
        ]

    def test_already_list(self):
        assert _coerce_list_param(["x"]) == ["x"]


class TestToolRegistryListCoercion:
    def test_coerce_params_comma_separated_tags_without_warning(self, caplog):
        registry = ToolRegistry()
        registry.register(_StubTool())
        caplog.set_level(logging.WARNING, logger="backend.services.agent_tools")

        coerced = registry._coerce_params(registry.get_tool("stub"), {
            "tags": "alpha, beta, gamma",
        })

        assert coerced["tags"] == ["alpha", "beta", "gamma"]
        assert not [r for r in caplog.records if "Failed to coerce" in r.message]


class TestMemoryAuditLog:
    def test_log_memory_saved_emits_info(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
        monkeypatch.setenv("GUAARDVARK_LOG_DIR", str(tmp_path / "logs"))

        from backend.utils import memory_audit_log as mal

        mal._LOGGER = None

        mal.log_memory_saved("abc123", "note", "agent", "Saved fact about GUAARDVARK")

        audit_file = tmp_path / "logs" / "memory_audit.log"
        assert audit_file.is_file()
        text = audit_file.read_text()
        assert "Memory saved: id=abc123" in text
        assert "Saved fact about GUAARDVARK" in text

    def test_add_memory_writes_audit_log(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
        monkeypatch.setenv("GUAARDVARK_LOG_DIR", str(tmp_path / "logs"))

        from backend.api.memory_api import add_memory
        from backend.models import AgentMemory

        with app.app_context():
            memory = add_memory(
                content="Audit log integration test memory",
                memory_type="note",
                source="agent",
                tags="one, two",
            )
            assert memory is not None
            persisted = db.session.get(AgentMemory, memory.id)
            assert persisted is not None

        audit_file = tmp_path / "logs" / "memory_audit.log"
        assert audit_file.is_file(), f"expected audit log at {audit_file}"
        text = audit_file.read_text()
        assert memory.id in text
        assert "Memory saved:" in text
