"""`guaardvark status` must survive a celery leg that answers 503.

A stopped, busy or wrong-queue worker answers ``GET /api/health/celery`` with 503 — the
one situation a person runs `status` to diagnose. Before this file that raised `LlxError`,
which the command's outer handler turned into a bare "exit 1", so the panel showing every
*other* leg never printed. The regression is silent because every other `status` test feeds
a healthy celery leg.
"""

import json

from llx.main import app

# The real 503 body from `backend/app.py::_celery_health_diagnosis` (the no-worker case).
_CELERY_503 = {
    "status": "down",
    "error": (
        "No Celery worker is consuming the 'health' queue — the registered worker(s) do "
        "not consume it, or the one that does is stopped."
    ),
    "suggestion": "Start the Celery workers: ./restart_celery.sh. A stopped worker is not "
                  "restarted automatically.",
}


def _healthy_legs(fake_backend):
    """Every leg of `status` except celery, so a failure can only come from celery."""
    fake_backend.route("GET", "/api/health", json={"status": "ok", "version": "9.9.9"})
    fake_backend.route("GET", "/api/model/status", json={"data": {"text_model": "gemma4:e4b"}})
    fake_backend.route("GET", "/api/meta/metrics", json={"data": {"gpu_mem": 12.0, "cpu_percent": 18.0}})
    fake_backend.route("GET", "/api/automation/mcp/status", json={"mcp_enabled": False})


def test_status_human_panel_keeps_the_worker_count_when_celery_is_busy(
    fake_backend, cli_runner, isolated_home, monkeypatch
):
    """A merely busy worker answers 200 with BOTH a count and an error; keep the count.

    The backend words a backlog as `status: busy` plus an `error` (the Studio reads the
    error). That body is not the guard's degraded record, so the human path must not
    treat it as "no count is known" and drop the `N workers` it just received.
    """
    from llx import output as output_mod

    _healthy_legs(fake_backend)
    fake_backend.route(
        "GET",
        "/api/health/celery",
        json={
            "status": "busy",
            "workers": [{"name": "w1"}],
            "active_tasks": 1,
            "queued_tasks": 0,
            "message": "Worker backlog — health ping queued behind long task(s)",
            "error": "Celery worker busy — the health ping queued behind a long task.",
        },
    )
    monkeypatch.setattr(output_mod, "is_pipe", lambda: False)

    result = cli_runner.invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "1 workers" in text
    assert "Celery worker busy" in text


def test_status_json_survives_a_503_celery_leg(fake_backend, cli_runner, isolated_home):
    _healthy_legs(fake_backend)
    fake_backend.route("GET", "/api/health/celery", status=503, json=_CELERY_503)

    result = cli_runner.invoke(app, ["--json", "status"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    # The other legs are still reported: that is the whole point of not aborting.
    assert payload["data"]["health"]["status"] == "ok"
    assert payload["data"]["model"]["data"]["text_model"] == "gemma4:e4b"
    # The failure is represented distinctly, so a script can tell "no worker" from
    # "the leg was fine but reported down", and is never silently an empty dict.
    celery = payload["data"]["celery"]
    assert celery["status"] == "down"
    assert "No Celery worker is consuming" in celery["error"]


def test_status_human_panel_still_renders_a_503_celery_leg(
    fake_backend, cli_runner, isolated_home, monkeypatch
):
    from llx import output as output_mod

    _healthy_legs(fake_backend)
    fake_backend.route("GET", "/api/health/celery", status=503, json=_CELERY_503)
    # CliRunner's stdout is not a tty, so `status` would take the JSON branch; force the
    # branch a person actually sees. Rich wraps at 80 columns, so compare on collapsed
    # whitespace rather than raw lines.
    monkeypatch.setattr(output_mod, "is_pipe", lambda: False)

    result = cli_runner.invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "System Status" in text
    assert "Server:" in text
    # The backend's own wording reaches the operator — a generic "down" would not say
    # which of "stopped", "busy" or "wrong queue" they are looking at.
    assert "No Celery worker is consuming" in text


def test_status_still_exits_one_when_the_celery_leg_cannot_connect(
    cli_runner, isolated_home, monkeypatch
):
    """A lost connection is not a failed leg: the server itself may be gone, so abort.

    Only celery raises here. Every leg of `status` runs before celery is reached, so if
    the guard swallowed this as a degraded record (i.e. its `except LlxConnectionError`
    clause were dropped, letting the `LlxError` clause below it catch a subclass) the
    command would print a panel and exit 0, and this test would fail.
    """
    from llx.client import LlxConnectionError
    from llx.commands import system as system_mod

    class _CeleryLegGone:
        server_url = "http://localhost:5002"

        def get(self, endpoint, **params):
            if endpoint == "/api/health/celery":
                raise LlxConnectionError("Cannot connect to Guaardvark at http://localhost:5002.")
            if endpoint == "/api/health":
                return {"status": "ok", "version": "9.9.9"}
            if endpoint == "/api/model/status":
                return {"data": {"text_model": "gemma4:e4b"}}
            return {}

    monkeypatch.setattr(system_mod, "get_client", lambda server=None: _CeleryLegGone())

    result = cli_runner.invoke(app, ["--json", "status"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "CONNECTION_ERROR"
    # No panel, not even a partial one: the server is presumed gone, and a degraded
    # record would be worse than useless.
    assert "data" not in payload
