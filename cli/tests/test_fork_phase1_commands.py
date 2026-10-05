"""Phase 1 command groups: guard, improve, system-map, content, websearch, connections, approvals.

Each group gets a smoke test through the real CLI against the fake backend: it proves
the command reaches the route it claims to, that `--json` emits the documented envelope,
and that the read-only groups really do not write.
"""
from __future__ import annotations

import json

import pytest

from llx.main import app


def _run(cli_runner, args):
    result = cli_runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# --- guard -----------------------------------------------------------------


def test_guard_scans_reads_the_inbound_guard_subtree(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/settings/inbound_guard/scans", json={
        "success": True,
        "data": {"scans": [{"id": 3, "decision": "held", "kind": "code", "by": "guard"}], "git": []},
    })

    payload = _run(cli_runner, ["guard", "scans", "--json"])

    assert [s["id"] for s in payload["data"]["scans"]] == [3]
    assert len(fake_backend.calls_for("GET", "/api/settings/inbound_guard/scans")) == 1


def test_guard_never_decides(fake_backend, cli_runner, isolated_home):
    """`guard sweep` is a scan. Approving or rejecting lives in the Studio."""
    fake_backend.route("POST", "/api/settings/inbound_guard/sweep", json={"success": True, "data": {}})

    _run(cli_runner, ["guard", "sweep", "--json"])

    assert fake_backend.posted_paths() == ["/api/settings/inbound_guard/sweep"]


# --- improve ---------------------------------------------------------------


def test_improve_pending_lists_the_fix_queue(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/self-improvement/pending-fixes", json={
        "success": True,
        "data": [{"id": 9, "status": "pending", "file": "backend/x.py", "description": "tighten"}],
    })

    payload = _run(cli_runner, ["improve", "pending", "--json"])

    assert payload["data"]["fixes"][0]["id"] == 9


def test_improve_exposes_no_apply_or_approve(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/self-improvement/trigger", json={"success": True, "data": {}})

    _run(cli_runner, ["improve", "trigger", "--json"])

    assert fake_backend.posted_paths() == ["/api/self-improvement/trigger"]


# --- system-map ------------------------------------------------------------


def test_system_map_findings_reads_the_raw_jsonify_shape(fake_backend, cli_runner, isolated_home):
    """This blueprint jsonify()s a bare dict rather than success_response()."""
    fake_backend.route("GET", "/api/system-map/findings", json={
        "findings": [{"id": "f1", "severity": "high", "kind": "cycle", "label": "import loop"}],
    })

    payload = _run(cli_runner, ["system-map", "findings", "--json"])

    assert payload["data"]["findings"][0]["id"] == "f1"


# --- content ---------------------------------------------------------------


def test_content_pages_lists_pages(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/content/pages", json={
        "pages": [{"id": 4, "title": "Release notes", "status": "draft"}],
    })

    payload = _run(cli_runner, ["content", "pages", "--json"])

    assert payload["data"]["pages"][0]["title"] == "Release notes"


def test_content_page_delete_requires_confirmation(fake_backend, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["content", "page-delete", "4", "--json"])

    assert result.exit_code == 2
    assert fake_backend.calls == []  # refused before touching the backend


def test_content_page_delete_with_yes_deletes(fake_backend, cli_runner, isolated_home):
    fake_backend.route("DELETE", "/api/content/pages/4", json={"ok": True})

    _run(cli_runner, ["content", "page-delete", "4", "--yes", "--json"])

    assert ("DELETE", "/api/content/pages/4", b"") in fake_backend.calls


# --- websearch -------------------------------------------------------------


def test_web_search_posts_the_query(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/web-search/search", json={
        "success": True, "data": {"results": [{"title": "Docs", "url": "https://example.test"}]},
    })

    payload = _run(cli_runner, ["websearch", "search", "aardvark docs", "--json"])

    assert payload["data"]["results"][0]["url"] == "https://example.test"
    body = json.loads(fake_backend.calls_for("POST", "/api/web-search/search")[0][2])
    assert body == {"query": "aardvark docs", "max_results": 5}


# --- connections -----------------------------------------------------------


def test_connections_list(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/connections", json={
        "connections": [{"id": 2, "provider": "youtube", "name": "Main channel"}],
    })

    payload = _run(cli_runner, ["connections", "list", "--json"])

    assert payload["data"]["connections"][0]["provider"] == "youtube"


# --- approvals (aggregate) -------------------------------------------------


def test_approvals_list_merges_all_three_sources(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/connections/publishes", json={"publishes": [{"id": 1, "title": "Post"}]})
    fake_backend.route("GET", "/api/settings/inbound_guard/scans", json={
        "success": True, "data": {"scans": [{"id": 7, "decision": "held", "kind": "code"}]},
    })
    fake_backend.route("GET", "/api/social-outreach/queue", json={"queue": [{"id": 5, "platform": "reddit"}]})

    payload = _run(cli_runner, ["approvals", "list", "--json"])

    data = payload["data"]
    assert [p["id"] for p in data["publishes"]] == [1]
    assert [s["id"] for s in data["held"]] == [7]
    assert [d["id"] for d in data["drafts"]] == [5]
    assert data["errors"] == {}


def test_approvals_list_reports_a_dead_source_without_hiding_the_others(
    fake_backend, cli_runner, isolated_home
):
    """'Nothing is waiting' and 'I could not ask' are different answers."""
    fake_backend.route("GET", "/api/connections/publishes", status=500, json={"error": "boom"})
    fake_backend.route("GET", "/api/settings/inbound_guard/scans", json={"success": True, "data": {"scans": []}})
    fake_backend.route("GET", "/api/social-outreach/queue", json={"queue": []})

    payload = _run(cli_runner, ["approvals", "list", "--json"])

    assert payload["data"]["errors"].get("publishes")
    assert payload["data"]["held"] == []


# --- human-readable path ----------------------------------------------------
# Every command returns JSON when stdout is a pipe, and CliRunner's stdout is a pipe,
# so the table path is invisible to every other test here. It is also the path a person
# actually sees, and it was dead code until this test exercised it.


def test_human_readable_output_renders_a_table(fake_backend, cli_runner, isolated_home, monkeypatch):
    from llx import output as output_mod

    fake_backend.route("GET", "/api/system-map/findings", json={
        "findings": [{"id": "f1", "severity": "high", "kind": "cycle", "label": "import loop"}],
    })
    captured = {}
    monkeypatch.setattr(output_mod, "is_pipe", lambda: False)
    monkeypatch.setattr(
        output_mod, "print_table",
        lambda rows, columns=None, title=None: captured.update(rows=rows, columns=columns, title=title),
    )

    result = cli_runner.invoke(app, ["system-map", "findings"])

    assert result.exit_code == 0, result.output
    assert captured["rows"][0]["id"] == "f1"
    assert captured["columns"] == ["id", "severity", "kind", "label"]
    assert "Findings (1)" == captured["title"]


def test_human_readable_approvals_merges_into_one_table(fake_backend, cli_runner, isolated_home, monkeypatch):
    from llx import output as output_mod

    fake_backend.route("GET", "/api/connections/publishes", json={"publishes": [{"id": 1, "title": "Post"}]})
    fake_backend.route("GET", "/api/settings/inbound_guard/scans", json={"success": True, "data": {"scans": []}})
    fake_backend.route("GET", "/api/social-outreach/queue", json={"queue": [{"id": 5, "platform": "reddit"}]})
    captured = {}
    monkeypatch.setattr(output_mod, "is_pipe", lambda: False)
    monkeypatch.setattr(
        output_mod, "print_table",
        lambda rows, columns=None, title=None: captured.update(rows=rows, title=title),
    )

    result = cli_runner.invoke(app, ["approvals", "list"])

    assert result.exit_code == 0, result.output
    assert [r["source"] for r in captured["rows"]] == ["publish", "draft"]
    assert captured["title"] == "Awaiting approval (2)"


# --- error path ------------------------------------------------------------


def test_a_backend_error_exits_nonzero_without_a_traceback(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/system-map/findings", status=500, json={"error": "kaboom"})

    result = cli_runner.invoke(app, ["system-map", "findings", "--json"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
