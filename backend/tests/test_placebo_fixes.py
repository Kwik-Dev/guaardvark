"""Regression tests for the 2026-06-03 placebo/fake-state remediation.

Each guard is exercised in its NEGATIVE case (the house "zero placebo" rule):
a guard that can't be observed failing is itself a placebo.
"""
from unittest.mock import MagicMock

import pytest
from flask import Flask


# ---------------------------------------------------------------------------
# #1 — rag_autoresearch _check_prerequisites must FAIL CLOSED.
# The bug: a RuntimeError (no Flask app context in the daemon thread) was
# swallowed and the function returned True, running autoresearch with no corpus.
# ---------------------------------------------------------------------------
def _make_service():
    from backend.services.rag_autoresearch_service import RAGAutoresearchService
    # Bypass the real __init__ (which builds a RAGEvalHarness) — we only need the method.
    svc = RAGAutoresearchService.__new__(RAGAutoresearchService)
    svc.eval_harness = MagicMock()
    return svc


def test_prereq_passes_when_corpus_sufficient():
    svc = _make_service()
    svc.eval_harness.has_sufficient_corpus.return_value = True
    assert svc._check_prerequisites() is True


def test_prereq_fails_closed_when_corpus_insufficient():
    svc = _make_service()
    svc.eval_harness.has_sufficient_corpus.return_value = False
    assert svc._check_prerequisites() is False


def test_prereq_fails_closed_on_runtime_error():
    # This is the exact regression: missing app context -> RuntimeError -> must be False.
    svc = _make_service()
    svc.eval_harness.has_sufficient_corpus.side_effect = RuntimeError(
        "Working outside of application context"
    )
    assert svc._check_prerequisites() is False


def test_prereq_fails_closed_on_unexpected_error():
    svc = _make_service()
    svc.eval_harness.has_sufficient_corpus.side_effect = ValueError("boom")
    assert svc._check_prerequisites() is False


# ---------------------------------------------------------------------------
# #5/#6/#7 — command_api: placeholders must not report fake success.
# ---------------------------------------------------------------------------
@pytest.fixture()
def command_client():
    from backend.routes.command_api import command_bp
    app = Flask(__name__)
    app.register_blueprint(command_bp)
    return app.test_client()


def test_analyze_command_is_honest_501(command_client):
    resp = command_client.post("/api/command/analyze", json={"anything": 1})
    assert resp.status_code == 501
    assert resp.get_json()["success"] is False


def test_codefile_command_is_honest_501(command_client):
    resp = command_client.post("/api/command/codefile", json={})
    assert resp.status_code == 501
    assert resp.get_json()["success"] is False


def test_websearch_no_query_is_400_not_success(command_client):
    resp = command_client.post("/api/command/websearch", json={})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


# ---------------------------------------------------------------------------
# #14 — web_search /status must reflect real policy + probes, not hardcoded True.
# ---------------------------------------------------------------------------
def _client_for(bp):
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


def test_web_search_status_disabled_by_policy(monkeypatch):
    import backend.api.web_search_api as ws
    monkeypatch.setattr(ws, "get_web_access", lambda: False)
    data = _client_for(ws.web_search_bp).get("/api/web-search/status").get_json()["data"]
    assert data["web_search_enabled"] is False
    assert data["service_status"] == "disabled_by_policy"
    assert all(v is False for v in data["capabilities"].values())
    assert all(v == "disabled_by_policy" for v in data["services"].values())


def test_web_search_status_operational_when_enabled(monkeypatch):
    import backend.api.web_search_api as ws
    monkeypatch.setattr(ws, "get_web_access", lambda: True)
    data = _client_for(ws.web_search_bp).get("/api/web-search/status").get_json()["data"]
    assert data["service_status"] == "operational"
    assert all(v is True for v in data["capabilities"].values())


# ---------------------------------------------------------------------------
# #2 — system /health-check must go "degraded" when a component probe fails.
# ---------------------------------------------------------------------------
def test_system_health_degraded_when_progress_unavailable(monkeypatch):
    import backend.utils.unified_progress_system as ups

    def _boom():
        raise RuntimeError("progress system down")

    monkeypatch.setattr(ups, "get_unified_progress", _boom)
    import backend.api.system_api as sysapi
    data = _client_for(sysapi.system_bp).get("/api/system/health-check").get_json()["data"]
    assert data["components"]["progress_system"] == "unavailable"
    assert data["status"] == "degraded"
    # And the timestamp is no longer the frozen 2025-08-02 literal.
    assert not data["timestamp"].startswith("2025-08-02")


# ---------------------------------------------------------------------------
# #4 — bulk_generation /status reports real idle/busy, not hardcoded "available".
# ---------------------------------------------------------------------------
def test_bulk_status_idle_when_no_processes(monkeypatch):
    import backend.api.bulk_generation_api as bg
    fake = MagicMock()
    fake.get_active_processes.return_value = {}
    monkeypatch.setattr(bg, "get_unified_progress", lambda: fake)
    data = _client_for(bg.bulk_gen_bp).get("/api/bulk-generate/status").get_json()
    assert data["status"] == "idle"
    assert data["active_processes"] == 0


# ---------------------------------------------------------------------------
# #3 — code_intelligence /health: llm_available reflects a REAL probe, and status
# is not the hardcoded "healthy" when nothing is configured.
# ---------------------------------------------------------------------------
def test_code_intel_health_llm_unavailable_when_not_configured():
    import backend.api.code_intelligence_api as ci
    # No LLAMA_INDEX_LLM in app config -> real get_llm_instance() returns None.
    app = Flask(__name__)
    app.register_blueprint(ci.code_intelligence_bp)
    data = app.test_client().get("/api/code-intelligence/health").get_json()["data"]
    assert data["llm_available"] is False
    assert data["status"] in ("degraded", "unavailable")
    assert "mode" not in data or data.get("mode") != "fully_functional_offline"
