"""Keep MCP retrieval out of the GPU-owning process boundary."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from backend.services.agent_tools import ToolResult
from backend.utils import reranker


@pytest.fixture(scope="session", autouse=True)
def _isolate_gpu_lock_file():
    """These tests stub GPU access and need no coordinator or lock file."""
    yield


def _unexpected_call(*args, **kwargs):
    pytest.fail("Unexpected retrieval, backend dispatch, or GPU access")


@pytest.fixture
def rag_tools(monkeypatch):
    # Indexing imports probe hardware; load the tool with only that dependency stubbed.
    indexing = ModuleType("backend.services.indexing_service")
    indexing.search_with_llamaindex = _unexpected_call
    monkeypatch.setitem(sys.modules, indexing.__name__, indexing)
    path = Path(__file__).resolve().parents[2] / "tools" / "rag_tools.py"
    spec = importlib.util.spec_from_file_location("_test_rag_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("optional", [
    {},
    {"top_k": 7, "filter_type": "document", "project_id": "project-1"},
])
def test_mcp_search_runs_in_backend(monkeypatch, rag_tools, optional):
    expected = ToolResult(success=True, output="backend result", metadata={"source": "backend"})
    calls = []

    def dispatch(name, arguments):
        calls.append((name, arguments))
        return expected

    monkeypatch.setattr(rag_tools, "is_mcp_transport", lambda tool: True)
    monkeypatch.setattr(rag_tools, "run_tool_in_backend", dispatch)
    monkeypatch.setattr(rag_tools, "search_with_llamaindex", _unexpected_call)

    result = rag_tools.KnowledgeSearchTool().execute("find passages", **optional)

    assert result is expected
    assert calls == [("search_knowledge_base", {"query": "find passages", **optional})]


def test_backend_search_runs_locally(monkeypatch, rag_tools):
    payload = {"results": [{"text": "A source passage"}], "trace": {}}
    calls = []

    def search(query, **kwargs):
        calls.append((query, kwargs))
        return payload

    monkeypatch.setattr(rag_tools, "is_mcp_transport", lambda tool: False)
    monkeypatch.setattr(rag_tools, "run_tool_in_backend", _unexpected_call)
    monkeypatch.setattr(rag_tools, "search_with_llamaindex", search)

    result = rag_tools.KnowledgeSearchTool().execute(
        "find passages", top_k=7, filter_type="document", project_id="project-1"
    )

    assert isinstance(result, ToolResult)
    assert result.success
    assert "[1] unknown source\nA source passage" in result.output
    assert result.metadata == {"retrieval": payload["trace"], "results": payload["results"]}
    assert calls == [("find passages", {
        "max_chunks": 7, "project_id": "project-1",
        "filters": {"content_type": "document"}, "with_trace": True,
    })]


@pytest.mark.parametrize("mcp_process, expected", [(True, "cpu"), (False, "cuda")])
def test_pick_device_respects_process_boundary(monkeypatch, mcp_process, expected):
    probe = ModuleType("backend.services.gpu_resource_coordinator")
    calls = []

    def has_gpu():
        calls.append("has_gpu")
        return True

    def get_available_vram():
        calls.append("get_available_vram")
        return {"success": True, "available_mb": 24000}

    monkeypatch.setattr(probe, "has_gpu", has_gpu, raising=False)
    monkeypatch.setattr(probe, "get_available_vram", get_available_vram, raising=False)
    monkeypatch.setitem(sys.modules, probe.__name__, probe)
    monkeypatch.setenv("GUAARDVARK_RERANK_MIN_VRAM_MB", "3000")
    if mcp_process:
        monkeypatch.setenv("GUAARDVARK_MCP_PROCESS", "1")
    else:
        monkeypatch.delenv("GUAARDVARK_MCP_PROCESS", raising=False)

    assert reranker._pick_device() == expected
    assert calls == ([] if mcp_process else ["has_gpu", "get_available_vram"])
