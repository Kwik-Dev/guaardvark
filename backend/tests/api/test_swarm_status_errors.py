"""A swarm sidecar that fails is reported as a fault, not as a status.

"Not running" (nothing listens on the port) and "running but not answering
properly" (timeout, 5xx, a body that is not JSON, a rejected internal token)
are different faults with different fixes, for the routes and for the
swarm_status tool on both paths. The sidecar call is replaced per test; the
tool's call to the backend is answered by the Flask test client.
"""

from __future__ import annotations

import pytest
import requests
from flask import Flask

from backend.api import swarm_api
from backend.tools import workstation_tools as wt
from backend.utils import backend_http


class _SidecarResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


def _answers(response):
    def get(url, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response
    return get


FAULTS = {
    "timeout": (requests.exceptions.ReadTimeout("Read timed out. (read timeout=10)"), 504, "did not answer within"),
    "sidecar_500": (_SidecarResponse(500, {"error": "boom"}), 502, "boom"),
    "rejected_token": (_SidecarResponse(401, {"detail": "Invalid internal token"}), 502, "internal token"),
    "not_json": (_SidecarResponse(502, None), 502, "not JSON"),
}


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(swarm_api.swarm_bp)
    return app.test_client()


@pytest.fixture
def sidecar(monkeypatch):
    def use(response):
        monkeypatch.setattr(swarm_api.requests, "get", _answers(response))
    return use


@pytest.fixture
def mcp_tool(client, monkeypatch):
    """swarm_status on the MCP path, its backend call answered by the routes."""
    def request(method, path, **kwargs):
        resp = client.open(path, method=method)

        class Reply:
            status_code = resp.status_code
            content = resp.data
            text = resp.get_data(as_text=True)

            @staticmethod
            def json():
                body = resp.get_json()
                if body is None:
                    raise ValueError("not JSON")
                return body

        monkeypatch.setattr("requests.request", lambda *a, **k: Reply)
        return backend_http.request_json(method, path, **kwargs)

    monkeypatch.setattr(wt, "request_json", request)
    tool = wt.SwarmStatusTool()
    tool.set_context({"transport": "mcp"})
    return tool


@pytest.mark.parametrize("path", ["/api/swarm/status", "/api/swarm/status/swarm-1", "/api/swarm/health"])
@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_routes_answer_a_sidecar_fault_as_an_error(client, sidecar, path, fault):
    response, http_status, needle = FAULTS[fault]
    sidecar(response)

    resp = client.get(path)

    assert resp.status_code == http_status
    body = resp.get_json()
    assert body["success"] is False
    assert needle in body["message"]


def test_a_rejected_internal_token_is_not_passed_on_as_the_backends_401(client, sidecar):
    sidecar(_SidecarResponse(401, {"detail": "Invalid internal token"}))

    assert client.get("/api/swarm/status").status_code == 502


def test_offline_keeps_its_own_answers(client, sidecar):
    sidecar(requests.exceptions.ConnectionError("refused"))

    listing = client.get("/api/swarm/status")
    one = client.get("/api/swarm/status/swarm-1")

    assert listing.status_code == 200
    assert listing.get_json()["message"] == "Swarm service offline"
    assert one.status_code == 503


def test_a_healthy_sidecar_is_passed_through(client, sidecar):
    sidecar(_SidecarResponse(200, {"swarms": [{"swarm_id": "swarm-1"}], "count": 1}))

    resp = client.get("/api/swarm/status")

    assert resp.status_code == 200
    assert resp.get_json()["data"]["count"] == 1


@pytest.mark.parametrize("arguments", [{}, {"swarm_id": "swarm-1"}])
@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_tool_over_mcp_reports_a_fault_not_a_status(mcp_tool, sidecar, fault, arguments):
    response, http_status, needle = FAULTS[fault]
    sidecar(response)

    result = mcp_tool.execute(**arguments)

    assert not result.success
    assert "running but did not return its status" in result.error
    assert needle in result.error
    assert result.error != wt._SWARM_OFFLINE_ERROR
    assert result.metadata["http_status"] == http_status


@pytest.mark.parametrize("arguments", [{}, {"swarm_id": "swarm-1"}])
def test_tool_over_mcp_still_says_offline_when_nothing_is_running(mcp_tool, sidecar, arguments):
    sidecar(requests.exceptions.ConnectionError("refused"))

    result = mcp_tool.execute(**arguments)

    assert not result.success
    assert result.error == wt._SWARM_OFFLINE_ERROR


def test_tool_over_mcp_passes_a_real_status_through(mcp_tool, sidecar):
    sidecar(_SidecarResponse(200, {"swarms": [{"swarm_id": "swarm-1"}], "count": 1}))

    result = mcp_tool.execute()

    assert result.success
    assert result.output["count"] == 1


@pytest.mark.parametrize("data", [{"error": "Read timed out."}, {"detail": "Invalid internal token"}])
def test_tool_over_mcp_does_not_accept_an_error_wrapped_as_success(monkeypatch, data):
    wrapped = backend_http.BackendResponse(status=200, body={"success": True, "data": data}, data=data)
    monkeypatch.setattr(wt, "request_json", lambda *a, **k: wrapped)
    tool = wt.SwarmStatusTool()
    tool.set_context({"transport": "mcp"})

    result = tool.execute()

    assert not result.success
    assert "running but did not return its status" in result.error


def test_tool_over_mcp_keeps_a_swarm_that_carries_an_error_field(monkeypatch):
    swarm = {"swarm_id": "swarm-1", "status": "failed", "error": "agent crashed"}
    answer = backend_http.BackendResponse(status=200, body={"success": True, "data": swarm}, data=swarm)
    monkeypatch.setattr(wt, "request_json", lambda *a, **k: answer)
    tool = wt.SwarmStatusTool()
    tool.set_context({"transport": "mcp"})

    result = tool.execute(swarm_id="swarm-1")

    assert result.success
    assert result.output == swarm


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_tool_in_the_backend_reports_a_fault_not_offline(sidecar, fault):
    response, http_status, _ = FAULTS[fault]
    sidecar(response)

    result = wt.SwarmStatusTool().execute()

    assert not result.success
    assert "running but did not return its status" in result.error
    assert result.error != wt._SWARM_OFFLINE_ERROR
