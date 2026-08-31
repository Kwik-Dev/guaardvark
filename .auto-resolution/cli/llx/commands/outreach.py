"""Outreach CLI — status, queue, approve, and natural-language intent."""

from __future__ import annotations

import typer

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console
from llx import output

console = make_console()
outreach_app = typer.Typer(
    name="outreach",
    help="Social outreach — status, queue, approve, or natural-language scout/draft.",
    no_args_is_help=False,
)


@outreach_app.callback(invoke_without_command=True)
def outreach_root(
    ctx: typer.Context,
    text: str = typer.Argument(
        None,
        help='Natural language, e.g. "comment on youtube videos regarding Offline AI or ComfyUI"',
    ),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """With freeform text, run intent. Without args, show status."""
    if ctx.invoked_subcommand is not None:
        return
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    client = get_client(server)

    if not text:
        _print_status(client, json_out)
        return

    try:
        data = client.post(
            "/api/social-outreach/intent",
            json={"text": text, "created_by": "cli"},
        )
    except (LlxError, LlxConnectionError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)

    if json_out:
        output.print_json(data)
        return
    msg = data.get("message") or data.get("error") or str(data)
    if data.get("refused"):
        output.print_warning(msg)
        raise typer.Exit(1)
    if not data.get("ok"):
        output.print_error(data.get("error") or msg or "intent failed")
        raise typer.Exit(1)
    console.print(msg)


@outreach_app.command("status")
def outreach_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show kill switch, supervised mode, and cadence."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    client = get_client(server)
    _print_status(client, json_out)


@outreach_app.command("queue")
def outreach_queue(
    status: str = typer.Option("drafted", "--status", help="drafted|approved|posted|rejected"),
    limit: int = typer.Option(10, "--limit", "-n"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List outreach drafts (defaults to pending drafted)."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    client = get_client(server)
    try:
        if status == "drafted":
            rows = client.get("/api/social-outreach/queue")
        elif status == "approved":
            rows = client.get("/api/social-outreach/approved")
        else:
            rows = client.get("/api/social-outreach/audit", limit=200)
            if isinstance(rows, list):
                rows = [r for r in rows if r.get("status") == status][:limit]
    except (LlxError, LlxConnectionError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)

    if not isinstance(rows, list):
        rows = []
    rows = rows[:limit]
    if json_out:
        output.print_json(rows)
        return
    if not rows:
        output.print_warning(f"No rows with status={status}")
        return
    table = []
    for r in rows:
        table.append({
            "id": r.get("id"),
            "platform": r.get("platform"),
            "status": r.get("status"),
            "grade": r.get("grade_score"),
            "draft": (r.get("draft_text") or "")[:80],
        })
    output.print_table(table, columns=["id", "platform", "status", "grade", "draft"])


@outreach_app.command("approve")
def outreach_approve(
    draft_id: int = typer.Argument(..., help="SocialOutreachLog id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Approve a drafted row for the next process-approved tick."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    client = get_client(server)
    try:
        data = client.post(f"/api/social-outreach/approve/{draft_id}", json={})
    except (LlxError, LlxConnectionError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    if json_out:
        output.print_json(data)
    else:
        console.print(f"Approved draft #{draft_id} (status={data.get('status')})")


def _print_status(client, json_out: bool) -> None:
    try:
        data = client.get("/api/social-outreach/status")
    except (LlxError, LlxConnectionError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    if json_out:
        output.print_json(data)
        return
    enabled = "Enabled" if data.get("enabled") else "Disabled"
    supervised = "supervised" if data.get("supervised") else "unsupervised"
    console.print(f"[bold]Outreach:[/bold] {enabled} ({supervised})")
    for platform, value in (data.get("cadence") or {}).items():
        if value.get("redis") == "unavailable":
            console.print(f"  {platform}: Redis offline")
        else:
            posts = value.get("posts_in_24h") or 0
            cap = value.get("daily_cap") or 0
            console.print(f"  {platform}: {posts}/{cap} today")
