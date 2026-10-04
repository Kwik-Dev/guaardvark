"""`guaardvark system-map` — the repository/system map and its findings.

Read-only apart from `dismiss`, which closes a finding the same way the Studio's
dismiss button does. `dispatch` (turn a finding into work) is deliberately absent: it
creates a task, which is the Studio's call.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "system-map"
app = typer.Typer(help="System map — snapshot, findings, health.", no_args_is_help=True)

BASE = "/api/system-map"


@app.command("health")
def system_map_health(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Is the mapper up, and how fresh is its snapshot?"""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/health")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("snapshot")
def system_map_snapshot(
    refresh: bool = typer.Option(False, "--refresh", help="Ask the backend to rebuild it"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """The current system map."""
    json_out = json_mode(json_out)
    params = {"refresh": 1} if refresh else {}
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/snapshot", **params)
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("findings")
def system_map_findings(
    severity: str = typer.Option(None, "--severity", help="Filter by severity"),
    kind: str = typer.Option(None, "--kind", help="Filter by kind"),
    limit: int = typer.Option(100, "--limit", "-n"),
    include_dismissed: bool = typer.Option(False, "--include-dismissed"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Open findings from the last scan."""
    json_out = json_mode(json_out)
    params = {"limit": limit, "include_dismissed": int(include_dismissed)}
    if severity:
        params["severity"] = severity
    if kind:
        params["kind"] = kind
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/findings", **params)
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "findings")
    if json_out or output.is_pipe():
        success({"findings": rows})
        return
    table = [
        {
            "id": f.get("id", ""),
            "severity": f.get("severity", ""),
            "kind": f.get("kind", ""),
            "label": (f.get("label") or f.get("title") or "")[:56],
        }
        for f in rows
    ]
    output.print_table(table, columns=["id", "severity", "kind", "label"],
                       title=f"Findings ({len(table)})")


@app.command("dismiss")
def system_map_dismiss(
    finding_id: str = typer.Argument(..., help="Finding id"),
    reason: str = typer.Option("manual_cleanup", "--reason", "-r"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Close a finding you have dealt with."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(
            f"{BASE}/findings/{finding_id}/dismiss", json={"reason": reason}
        )
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Dismissed {finding_id}.")
