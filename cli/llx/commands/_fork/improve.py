"""`guaardvark improve` — self-improvement runs, findings and pending fixes.

Read-only by design (CLI_PLAN D2): `pending-fixes/<id>/approve|reject|apply` are the
review gate — they change the codebase — and stay in the Studio where the diff is
visible. This group shows state and can *start* a scan (`trigger`), which is the same
class of action as `guard sweep`: it produces findings, it does not accept them.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "improve"
app = typer.Typer(help="Self-improvement — runs, findings, pending fixes (read-only).", no_args_is_help=True)

BASE = "/api/self-improvement"


@app.command("status")
def improve_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Enabled state, current activity and backlog counts."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/status")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_json(pick_dict(data))


@app.command("precheck")
def improve_precheck(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Whether self-improvement can run right now, and what blocks it."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/precheck")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("runs")
def improve_runs(
    limit: int = typer.Option(20, "--limit", "-n"),
    offset: int = typer.Option(0, "--offset"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Scan history."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/runs", limit=limit, offset=offset)
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "runs", "scans")
    if json_out or output.is_pipe():
        success({"runs": rows})
        return
    table = [
        {
            "id": r.get("id", ""),
            "status": r.get("status", ""),
            "model": r.get("model_name", ""),
            "started": str(r.get("created_at", ""))[:19],
        }
        for r in rows
    ]
    output.print_table(table, columns=["id", "status", "model", "started"], title=f"Runs ({len(table)})")


@app.command("metrics")
def improve_metrics(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Aggregate metrics for the self-improvement loop."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/metrics")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("pending")
def improve_pending(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Fixes waiting for a person. Approving them is a Studio action."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/pending-fixes")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "fixes", "pending_fixes")
    if json_out or output.is_pipe():
        success({"fixes": rows})
        return
    table = [
        {
            "id": f.get("id", ""),
            "status": f.get("status", ""),
            "file": (f.get("file") or f.get("path") or "")[-48:],
            "summary": (f.get("description") or f.get("summary") or "")[:60],
        }
        for f in rows
    ]
    output.print_table(table, columns=["id", "status", "file", "summary"],
                       title=f"Pending fixes ({len(table)})")


@app.command("trigger")
def improve_trigger(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Start a scan. It produces findings; it accepts none."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/trigger")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success("Scan started.")


@app.command("toggle")
def improve_toggle(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Enable or disable the self-improvement loop."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/toggle")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success("Toggled.")
