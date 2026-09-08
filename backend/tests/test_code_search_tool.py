"""search_codebase: root comes from the request, hybrid when zvec-grep answers, regex otherwise."""
from unittest.mock import patch

import pytest

from backend.tools import code_search_tools as cst
from backend.services import mcp_native_proxy


@pytest.fixture
def tool():
    return cst.SearchCodebaseTool()


def _mcp_ok(text):
    return {"success": True, "result": {"content": [{"type": "text", "text": text}]}}


def _mcp_err(text):
    return {"success": True, "result": {"isError": True, "content": [{"type": "text", "text": text}]}}


def test_root_defaults_to_the_request_project_root(tool, tmp_path):
    seen = {}

    def hybrid(root, query, limit):
        seen.update(root=root, query=query, limit=limit)
        return "#1 backend/x.py:1-2\nsource:\n1\tdef x(): ..."

    with patch.object(cst, "_hybrid_search", hybrid):
        res = tool.execute(query="where is x", _agent_context={"project_root": str(tmp_path)})
    assert res.success and res.metadata["engine"] == "hybrid"
    assert seen["root"] == str(tmp_path.resolve())
    assert seen["limit"] == cst.DEFAULT_LIMIT


def test_explicit_root_wins_and_missing_root_is_refused(tool, tmp_path):
    with patch.object(cst, "_hybrid_search", lambda root, q, l: "hit"):
        ok = tool.execute(query="q", root=str(tmp_path), _agent_context={"project_root": "/nope"})
        bad = tool.execute(query="q", root=str(tmp_path / "missing"))
    assert ok.success and ok.metadata["root"] == str(tmp_path.resolve())
    assert not bad.success and "not a directory" in bad.error


def test_falls_back_to_regex_when_the_server_is_absent(tool, tmp_path):
    with patch.object(cst, "_hybrid_search", lambda root, q, l: None), \
            patch.object(cst, "_regex_search", lambda q: "backend/a.py:3: def q(): ..."):
        res = tool.execute(query="q", root=str(tmp_path))
    assert res.success and res.metadata["engine"] == "regex"


def test_hybrid_error_result_falls_back(tmp_path):
    class Svc:
        async def call_tool(self, server, name, args):
            return _mcp_err("Invalid arguments: root must be an absolute path")

    with patch("backend.services.mcp_client_service.get_mcp_service", lambda: Svc()), \
            patch("backend.services.mcp_client_service.run_mcp_async", lambda coro: __import__("asyncio").run(coro)), \
            patch("backend.services.mcp_client_service.MCP_ENABLED", True):
        assert cst._hybrid_search(str(tmp_path), "q", 5) is None


def test_hybrid_returns_the_server_text(tmp_path):
    class Svc:
        async def call_tool(self, server, name, args):
            assert args == {"root": str(tmp_path), "query": "q", "limit": 5}
            return _mcp_ok("#1 backend/x.py")

    with patch("backend.services.mcp_client_service.get_mcp_service", lambda: Svc()), \
            patch("backend.services.mcp_client_service.run_mcp_async", lambda coro: __import__("asyncio").run(coro)), \
            patch("backend.services.mcp_client_service.MCP_ENABLED", True):
        assert cst._hybrid_search(str(tmp_path), "q", 5) == "#1 backend/x.py"


def test_output_is_capped_on_a_line_boundary(tool, tmp_path):
    big = "\n".join(f"line {i} " + "x" * 80 for i in range(400))
    with patch.object(cst, "_hybrid_search", lambda root, q, l: big):
        res = tool.execute(query="q", root=str(tmp_path))
    assert len(res.output) <= cst.MAX_OUTPUT_CHARS + 40
    assert res.output.endswith("[more hits omitted]")


def test_observation_budget_is_declared_on_the_tool(tool):
    assert tool.observation_chars == cst.MAX_OUTPUT_CHARS
    assert cst.BaseTool.observation_chars == 500


def test_proxy_reports_mcp_error_as_failure():
    cls = mcp_native_proxy._make_proxy_class("zvec_grep", {
        "name": "zvec_grep_search", "description": "d",
        "inputSchema": {"properties": {"query": {"type": "string"}}, "required": ["query"]},
    })
    proxy = cls()

    class Svc:
        async def call_tool(self, server, name, args):
            return _mcp_err("root: Invalid input: expected string, received undefined")

    with patch("backend.services.mcp_client_service.get_mcp_service", lambda: Svc()), \
            patch("backend.services.mcp_client_service.run_mcp_async", lambda coro: __import__("asyncio").run(coro)), \
            patch("backend.services.mcp_client_service.MCP_ENABLED", True):
        res = proxy.execute(query="q")
    assert not res.success
    assert "root" in res.error


def test_relative_root_narrows_to_the_project_root(tool, tmp_path):
    (tmp_path / "frontend").mkdir()
    seen = {}
    with patch.object(cst, "_hybrid_search", lambda root, q, l: seen.setdefault("root", root) and "hit"):
        res = tool.execute(query="q", root="frontend/src/pages/", _agent_context={"project_root": str(tmp_path)})
    assert res.success
    assert seen["root"] == str(tmp_path.resolve())
