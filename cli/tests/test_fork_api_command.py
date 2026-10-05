"""`guaardvark api` — the generic escape hatch must be reachable and gated (CLI_PLAN D5).

The point of this file is that "it can reach anything" and "it cannot be used by
accident" are both proven, not asserted in prose. Two halves:

* the reach — every route, a body, a query string, a discovery listing, an audit read;
* the gate — read-free / write-and-decision need `--yes`, a refusal sends nothing *and*
  is logged, and an absolute URL or a path outside `/api/` never leaves the process.
"""
from __future__ import annotations

import json

import pytest

from llx.main import app

_DECISION_EXAMPLE = "GET", "/api/music-video/1/approve"


def _run(cli_runner, args, *, expect: int = 0):
    result = cli_runner.invoke(app, args)
    assert result.exit_code == expect, result.output
    return result


def _audit_entries():
    from llx.commands._fork import _api_guard as guard

    path = guard.audit_path()
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- the reach -------------------------------------------------------------

def test_a_read_is_free_and_returns_the_backends_own_shape(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/production", json={"productions": [{"id": 3, "name": "The Last Spark"}]})

    result = _run(cli_runner, ["api", "request", "GET", "/api/production", "--json"])

    # Unchanged, unwrapped: the escape hatch must not re-shape what it did not define.
    assert json.loads(result.output) == {"productions": [{"id": 3, "name": "The Last Spark"}]}
    assert len(fake_backend.calls_for("GET", "/api/production")) == 1


def test_a_body_and_a_query_string_reach_the_request(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/system-map/findings", json={"ok": True})

    _run(
        cli_runner,
        [
            "api", "request", "POST", "/api/system-map/findings",
            "--query", "limit=5", "--query", "sort=recent",
            "--data", '{"severity": "high"}',
            "--yes", "--json",
        ],
    )

    calls = fake_backend.calls_for("POST", "/api/system-map/findings")
    assert len(calls) == 1
    assert json.loads(calls[0][2]) == {"severity": "high"}
    # The query string rides on the path, which is where the client puts it.
    assert fake_backend.full_urls[0].endswith("/api/system-map/findings?limit=5&sort=recent")


def test_a_data_file_body_is_json_parsed(fake_backend, cli_runner, isolated_home, tmp_path):
    payload = tmp_path / "body.json"
    payload.write_text('{"a": 1}', encoding="utf-8")
    fake_backend.route("POST", "/api/system-map/findings", json={"ok": True})

    _run(cli_runner, ["api", "request", "POST", "/api/system-map/findings",
                      "--data-file", str(payload), "--yes", "--json"])

    assert json.loads(fake_backend.calls_for("POST", "/api/system-map/findings")[0][2]) == {"a": 1}


def test_routes_lists_and_filters_what_the_backend_serves(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/routes", json={"routes": [
        {"rule": "/api/production/<int:prod_id>/storyboard/approve", "methods": "POST"},
        {"rule": "/api/routes", "methods": "GET"},
    ]})

    payload = json.loads(_run(cli_runner, ["api", "routes", "--search", "storyboard", "--json"]).output)
    assert [r["rule"] for r in payload] == ["/api/production/<int:prod_id>/storyboard/approve"]

    payload = json.loads(_run(cli_runner, ["api", "routes", "--method", "GET", "--json"]).output)
    assert [r["rule"] for r in payload] == ["/api/routes"]


def test_audit_reads_the_log_back(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/version", json={"version": "1.2.3"})
    _run(cli_runner, ["api", "request", "GET", "/api/version", "--json"])

    entries = json.loads(_run(cli_runner, ["api", "audit", "--json"]).output)
    assert entries[-1]["path"] == "/api/version"
    assert entries[-1]["outcome"] == "ok"
    assert entries[-1]["kind"] == "read"


# --- the gate --------------------------------------------------------------

def test_a_write_without_yes_is_refused_and_never_sent(fake_backend, cli_runner, isolated_home):
    fake_backend.default(json={"ok": True})

    result = _run(cli_runner, ["api", "request", "POST", "/api/files/folder", "--data", '{"name": "x"}'],
                  expect=2)

    assert fake_backend.calls == [], "a refused write must not reach the backend"
    assert "--yes" in result.output
    assert _audit_entries()[-1]["outcome"] == "refused"


def test_a_write_with_yes_is_sent_and_marked_approved(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/files/folder", json={"id": 7})

    _run(cli_runner, ["api", "request", "POST", "/api/files/folder",
                      "--data", '{"name": "x"}', "--yes", "--json"])

    assert json.loads(fake_backend.calls_for("POST", "/api/files/folder")[0][2]) == {"name": "x"}
    entry = _audit_entries()[-1]
    assert (entry["outcome"], entry["kind"], entry["approved"]) == ("ok", "write", True)


def test_a_decision_route_needs_yes_even_as_a_read(fake_backend, cli_runner, isolated_home):
    """The load-bearing case: a GET-shaped decision must not slip through the read path."""
    fake_backend.default(json={"ok": True})

    _run(cli_runner, ["api", "request", *_DECISION_EXAMPLE], expect=2)

    assert fake_backend.calls == []
    entry = _audit_entries()[-1]
    assert (entry["outcome"], entry["kind"]) == ("refused", "decision")


def test_a_decision_route_sent_with_yes_is_logged_as_a_decision(fake_backend, cli_runner, isolated_home):
    fake_backend.default(json={"ok": True})

    _run(cli_runner, ["api", "request", *_DECISION_EXAMPLE, "--yes", "--json"])

    entry = _audit_entries()[-1]
    assert (entry["outcome"], entry["kind"], entry["needs_yes"]) == ("ok", "decision", True)


def test_dry_run_previews_a_gated_route_without_yes_and_sends_nothing(fake_backend, cli_runner, isolated_home):
    """Previewing must not require `--yes` -- seeing what would be sent is how a person
    decides whether to add it. This is the only path that reports the gate."""
    fake_backend.default(json={"ok": True})

    result = _run(
        cli_runner,
        ["api", "request", *_DECISION_EXAMPLE, "--dry-run", "--json"],
        expect=0,
    )

    payload = json.loads(result.output)
    assert payload["status"] == "dry-run"
    assert payload["gate"] == "--yes required; this would be refused"
    assert fake_backend.calls == []
    entry = _audit_entries()[-1]
    assert (entry["outcome"], entry["approved"]) == ("dry-run", False)


def test_dry_run_records_the_yes_it_was_given(fake_backend, cli_runner, isolated_home):
    fake_backend.default(json={"ok": True})

    result = _run(
        cli_runner,
        ["api", "request", "POST", "/api/files/folder", "--data", '{"name": "x"}',
         "--yes", "--dry-run", "--json"],
    )

    assert json.loads(result.output)["gate"] == "--yes given"
    assert _audit_entries()[-1]["approved"] is True


@pytest.mark.parametrize(
    "path",
    ["http://localhost:8207/anything", "https://example.com/api/x", "/plugin/8207", "/health"],
)
def test_a_path_outside_the_api_prefix_is_refused(fake_backend, cli_runner, isolated_home, path):
    fake_backend.default(json={"ok": True})

    _run(cli_runner, ["api", "request", "GET", path], expect=1)

    assert fake_backend.calls == [], f"{path!r} must not be sent"
    assert _audit_entries() == [], "a rejected request shape is not an attempt worth logging"


def test_a_read_refuses_a_body_and_an_unknown_method(fake_backend, cli_runner, isolated_home):
    fake_backend.default(json={"ok": True})

    _run(cli_runner, ["api", "request", "GET", "/api/version", "--data", "{}"], expect=1)
    _run(cli_runner, ["api", "request", "FETCH", "/api/version"], expect=1)

    assert fake_backend.calls == []


def test_a_backend_error_is_audited_with_its_status(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/nope", status=404,
                       json={"error": {"code": "NOT_FOUND", "message": "no such thing"}})

    result = _run(cli_runner, ["api", "request", "GET", "/api/nope", "--json"], expect=1)

    # The dict-shaped error is unwrapped into a sentence, not repr'd (see client.error_message).
    assert "no such thing" in result.output
    entry = _audit_entries()[-1]
    assert (entry["outcome"], entry["status"]) == ("error", 404)


# --- the guard's classification -------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/api/production/3/storyboard/approve",   # film-crew: the render gate
        "/api/production/3/casting/confirm",       # film-crew: the casting gate
        "/api/music-video/1/approve",              # music-video: the cost gate
        "/api/settings/inbound_guard/scans/2/reject",
        "/api/self-improvement/pending-fixes/9/apply",
        "/api/system-map/dispatch",
        "/api/wordpress/process/queue/execute",    # publishes
    ],
)
def test_the_guard_calls_every_known_gate_a_decision(path):
    from llx.commands._fork import _api_guard as guard

    assert guard.classify("POST", path).is_decision
    assert guard.classify("POST", path).needs_yes


@pytest.mark.parametrize(
    "path",
    [
        "/api/connections/publishes",   # a read whose path merely contains "publish"
        "/api/production",
        "/api/files/document/1",
        "/api/self-improvement/pending-fixes",
    ],
)
def test_the_guard_does_not_mistake_a_lookalike_for_a_decision(path):
    """A false positive would put `--yes` in front of a read, so matching is on whole
    path segments, not substrings."""
    from llx.commands._fork import _api_guard as guard

    assert not guard.classify("GET", path).is_decision
    assert guard.classify("GET", path).kind == "read"
