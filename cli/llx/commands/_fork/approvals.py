"""`guaardvark approvals` — one queue for everything waiting on a person.

The Studio's Approvals page aggregates what `usePendingApprovals.js` reads: publish
records no Task row exists for, code the inbound guard is holding, and outreach drafts
in supervised mode. A script that reports on the backlog had no way to see any of it.

Strictly read-only (CLI_PLAN D2). Every item here ends in a decision — approve a post,
release held code, send a draft — and each of those stays in the Studio where the diff
or the draft is visible. `outreach approve` remains the one approval the CLI has, and
is untouched by this group.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "approvals"
app = typer.Typer(
    help="What is waiting on a person: publishes, held code, outreach drafts (read-only).",
    no_args_is_help=True,
)

PUBLISHES = "/api/connections/publishes"
HELD = "/api/settings/inbound_guard/scans"
DRAFTS = "/api/social-outreach/queue"
SOURCE_TIMEOUT_NOTE = "a source that is down is reported, not raised"


@app.command("list")
def approvals_list(
    limit: int = typer.Option(200, "--limit", "-n"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Everything pending, from all three sources, in one table."""
    json_out = json_mode(json_out)
    client = get_client(resolve_server(server))

    sources: dict[str, list] = {"publishes": [], "held": [], "drafts": []}
    errors: dict[str, str] = {}
    # A source being unavailable must not hide the other two: this is a status
    # command, and "the queue is empty" and "I could not ask" are different answers.
    for key, path in (("publishes", PUBLISHES), ("held", HELD), ("drafts", DRAFTS)):
        try:
            data = client.get(path, limit=limit)
            sources[key] = pick_list(data, key, "publishes", "scans", "drafts", "queue")
        except LlxError as exc:
            errors[key] = str(exc)

    if json_out or output.is_pipe():
        success({**sources, "errors": errors})
        return

    table = []
    for p in sources["publishes"]:
        table.append({"source": "publish", "id": p.get("id", ""),
                      "what": (p.get("title") or p.get("target") or "")[:44],
                      "state": p.get("status", "")})
    for s in sources["held"]:
        table.append({"source": "held-code", "id": s.get("id", ""),
                      "what": (s.get("kind") or s.get("path") or "")[:44],
                      "state": s.get("decision", "")})
    for d in sources["drafts"]:
        table.append({"source": "draft", "id": d.get("id", ""),
                      "what": (d.get("platform") or d.get("channel") or "")[:44],
                      "state": d.get("status", "")})
    output.print_table(table, columns=["source", "id", "what", "state"],
                       title=f"Awaiting approval ({len(table)})")
    for key, message in errors.items():
        output.print_warning(f"{key}: {message}")


@app.command("show")
def approvals_show(
    source: str = typer.Argument(..., help="publish, held-code, or draft"),
    item_id: str = typer.Argument(..., help="Item id, as listed by `approvals list`"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One pending item in full, so a decision can be made in the Studio."""
    json_out = json_mode(json_out)
    paths = {
        "publish": f"{PUBLISHES}/{item_id}",
        "held-code": f"{HELD}/{item_id}",
    }
    path = paths.get(source)
    if path is None:
        output.print_error(
            f"Unknown source {source!r}. Use one of: {', '.join(sorted(paths))} "
            "(drafts are read from the queue; see `approvals list`).",
            code="BAD_SOURCE",
        )
        raise typer.Exit(2)
    try:
        data = get_client(resolve_server(server)).get(path)
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)
