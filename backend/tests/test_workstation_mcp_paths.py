"""self_improvement_status and swarm_status on the MCP path: the precheck comes
from the backend, which has the database, and a swarm id stays an id.

No database, network or GPU: the backend HTTP client and the self-improvement
service are replaced with stand-ins.
"""

from __future__ import annotations

import pytest

from backend.tools import workstation_tools as wt
from backend.utils.backend_http import BackendError, BackendResponse


def _mcp(tool):
    tool.set_context({"transport": "mcp"})
    return tool


# ---- self_improvement_status ----------------------------------------------------

class _Backend:
    """Answers request_json from a path -> data (or BackendError) table."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path))
        answer = self.answers.get(path)
        if isinstance(answer, BackendError):
            raise answer
        if answer is None:
            raise BackendError("http", f"no route {path}", status=404)
        return BackendResponse(status=200, body={"data": answer}, data=answer)


def test_si_status_over_mcp_reports_the_backends_precheck(monkeypatch):
    backend = _Backend({
        "/api/self-improvement/precheck": {"ok": True, "reason": "ready"},
        "/api/self-improvement/runs": {"runs": [{"id": 7, "trigger": "directed", "status": "running", "timestamp": "t"}]},
        "/api/self-improvement/pending-fixes": [],
    })
    monkeypatch.setattr(wt, "request_json", backend)

    def local_precheck():
        raise AssertionError("the MCP process must not compute the precheck itself")

    monkeypatch.setattr(
        "backend.services.self_improvement_service.get_self_improvement_service", local_precheck)

    result = _mcp(wt.SelfImprovementStatusTool()).execute()
    assert result.success, result.error
    assert result.output["precheck"] == {"ok": True, "reason": "ready"}
    assert result.output["runs"][0]["status"] == "running"
    assert ("GET", "/api/self-improvement/precheck") in backend.calls


def test_si_status_over_mcp_passes_on_a_running_run(monkeypatch):
    backend = _Backend({
        "/api/self-improvement/precheck": {"ok": False, "reason": "A self-improvement run is already in progress."},
        "/api/self-improvement/runs": {"runs": []},
        "/api/self-improvement/pending-fixes": [],
    })
    monkeypatch.setattr(wt, "request_json", backend)
    result = _mcp(wt.SelfImprovementStatusTool()).execute()
    assert result.output["precheck"]["reason"] == "A self-improvement run is already in progress."


def test_si_status_over_mcp_fails_when_the_backend_is_not_answering(monkeypatch):
    down = BackendError("unreachable", "The Guaardvark backend is not answering at http://127.0.0.1:5000.")
    monkeypatch.setattr(wt, "request_json", _Backend({"/api/self-improvement/precheck": down}))
    result = _mcp(wt.SelfImprovementStatusTool()).execute()
    assert not result.success
    assert "not answering" in result.error
    assert "disabled" not in result.error.lower()


def test_si_status_over_mcp_marks_an_unknown_precheck_unknown(monkeypatch):
    """A backend without the precheck route leaves ok unknown instead of guessing."""
    monkeypatch.setattr(wt, "request_json", _Backend({
        "/api/self-improvement/runs": {"runs": []},
        "/api/self-improvement/pending-fixes": [],
    }))
    result = _mcp(wt.SelfImprovementStatusTool()).execute()
    assert result.success
    assert result.output["precheck"]["ok"] is None
    assert "did not report" in result.output["precheck"]["reason"]


def test_precheck_route_returns_the_service_answer(monkeypatch):
    from flask import Flask

    from backend.api.self_improvement_api import self_improvement_bp

    class Svc:
        def dispatch_precheck(self):
            return {"ok": False, "reason": "Codebase is locked"}

    monkeypatch.setattr(
        "backend.services.self_improvement_service.get_self_improvement_service", lambda: Svc())
    app = Flask(__name__)
    app.register_blueprint(self_improvement_bp)
    res = app.test_client().get("/api/self-improvement/precheck")
    assert res.status_code == 200
    assert res.get_json()["data"] == {"ok": False, "reason": "Codebase is locked"}


# ---- swarm_status ----------------------------------------------------------------

@pytest.mark.parametrize("swarm_id", ["../../gpu/status", "a/b", "x?y=1", "x#y", "..", "a b", "x" * 65])
def test_swarm_status_refuses_ids_that_are_not_ids(monkeypatch, swarm_id):
    backend = _Backend({})
    monkeypatch.setattr(wt, "request_json", backend)
    result = _mcp(wt.SwarmStatusTool()).execute(swarm_id=swarm_id)
    assert not result.success
    assert "swarm id" in result.error
    assert backend.calls == []


def test_swarm_status_refuses_bad_ids_on_the_chat_path_too(monkeypatch):
    from backend.api import swarm_api

    sent = []
    monkeypatch.setattr(swarm_api, "_proxy_get", lambda path: sent.append(path) or ({}, 200))
    result = wt.SwarmStatusTool().execute(swarm_id="../../x")
    assert not result.success
    assert sent == []


def test_swarm_status_sends_a_real_id(monkeypatch):
    backend = _Backend({"/api/swarm/status/swarm-20260930-120000-a1b2c3": {"status": "running"}})
    monkeypatch.setattr(wt, "request_json", backend)
    result = _mcp(wt.SwarmStatusTool()).execute(swarm_id=" swarm-20260930-120000-a1b2c3 ")
    assert result.success, result.error
    assert backend.calls == [("GET", "/api/swarm/status/swarm-20260930-120000-a1b2c3")]
