"""`guaardvark connections` — connected accounts and their environment.

Read-only in Phase 1: list, show, providers, environment, and `test` (which only asks
the provider whether the stored credential works — it changes nothing). `oauth start`
opens a browser and is deliberately absent: a login flow belongs where the user can
see the page it opens.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "connections"
app = typer.Typer(help="Connected accounts — list, inspect, test, providers.", no_args_is_help=True)

BASE = "/api/connections"


@app.command("list")
def connections_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Stored connections."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(BASE)
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "connections")
    if json_out or output.is_pipe():
        success({"connections": rows})
        return
    table = [
        {
            "id": c.get("id", ""),
            "provider": c.get("provider", ""),
            "name": (c.get("name") or c.get("label") or "")[:40],
            "status": c.get("status", ""),
        }
        for c in rows
    ]
    output.print_table(table, columns=["id", "provider", "name", "status"],
                       title=f"Connections ({len(table)})")


@app.command("show")
def connections_show(
    connection_id: int = typer.Argument(..., help="Connection id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One connection. Secrets are not returned by the backend, and not printed here."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/{connection_id}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data, "connection")
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("test")
def connections_test(
    connection_id: int = typer.Argument(..., help="Connection id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Ask the provider whether the stored credential still works."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/{connection_id}/test")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    ok = payload.get("ok", payload.get("success"))
    if ok is False:
        output.print_error("Connection test failed.", code="CONNECTION_TEST_FAILED")
        raise typer.Exit(1)
    output.print_success("Connection OK.")


@app.command("providers")
def connections_providers(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """The provider catalogue the backend can connect to."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/providers")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "providers")
    if json_out or output.is_pipe():
        success({"providers": rows})
        return
    table = []
    for p in rows:
        if isinstance(p, dict):
            table.append({
                "provider": str(p.get("id") or p.get("provider") or "")[:40],
                "auth": p.get("auth", ""),
            })
        else:
            table.append({"provider": str(p)[:40], "auth": ""})
    output.print_table(table, columns=["provider", "auth"], title=f"Providers ({len(table)})")


@app.command("environment")
def connections_environment(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Environment / credential-store posture for outbound connections."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/environment")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)
