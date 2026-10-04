"""`guaardvark guard` — the inbound guard: what it is holding, and why.

Read-only by design (CLI_PLAN D2). The guard holds code changes for a person to
release; `approve`/`reject`/`decide` are that person's decision and stay in the
Studio's Approvals page, where the diff is visible. This group shows the queue and
the posture so a script can report on it, and nothing more.

The blueprint is mounted under /api/settings/inbound_guard (it is a settings
sub-tree), which is why the base path looks like a settings route.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "guard"
app = typer.Typer(help="Inbound guard — held changes, scanned inbound, posture.", no_args_is_help=True)

BASE = "/api/settings/inbound_guard"


@app.command("status")
def guard_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Guard mode and posture."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(BASE)
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    state = pick_dict(data)
    if not state:
        output.print_warning("No guard state returned.")
        return
    output.print_kv(
        {k: state[k] for k in ("mode", "enabled", "scanned", "held", "last_sweep") if k in state},
        title="Inbound guard",
    )


@app.command("scans")
def guard_scans(
    limit: int = typer.Option(25, "--limit", "-n", help="How many scans to show"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Recent inbound scans, newest first."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/scans", limit=limit)
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "scans")
    if json_out or output.is_pipe():
        success({"scans": rows, "git": pick_list(data, "git")})
        return
    table_rows = [
        {
            "id": s.get("id", ""),
            "decision": s.get("decision", ""),
            "kind": s.get("kind", ""),
            "by": s.get("by", ""),
            "created": str(s.get("created_at", ""))[:19],
        }
        for s in rows
    ]
    output.print_table(table_rows, columns=["id", "decision", "kind", "by", "created"],
                       title=f"Inbound scans ({len(table_rows)})")


@app.command("show")
def guard_show(
    scan_id: int = typer.Argument(..., help="Scan id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One scan in full, including what the guard decided and why."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/scans/{scan_id}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("git")
def guard_git(
    digest: str = typer.Argument(..., help="Git verdict digest, as listed by `guard scans`"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """A held git operation and its verdict."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/git/{digest}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("sweep")
def guard_sweep(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Re-scan the workspace and refresh what the guard is holding.

    A scan, not a decision: it changes no verdict and releases nothing.
    """
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/sweep")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success("Sweep complete.")
