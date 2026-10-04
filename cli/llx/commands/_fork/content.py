"""`guaardvark content` — the content library: web pages, generations, stats.

Read-only except `page-delete`, which destroys a row and therefore requires `--yes`
(the same contract the plan puts on paid/irreversible actions, CLI_PLAN D1). The
approve / mark-uploaded transitions are review-adjacent and stay in the Studio.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "content"
app = typer.Typer(help="Content library — pages, generations, stats.", no_args_is_help=True)

BASE = "/api/content"


@app.command("pages")
def content_pages(
    limit: int = typer.Option(50, "--limit", "-n"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generated/queued content pages."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/pages", limit=limit)
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "pages")
    if json_out or output.is_pipe():
        success({"pages": rows})
        return
    table = [
        {
            "id": p.get("id", ""),
            "title": (p.get("title") or p.get("slug") or "")[:48],
            "status": p.get("status", ""),
            "uploaded": p.get("uploaded_at", "") or p.get("is_uploaded", ""),
        }
        for p in rows
    ]
    output.print_table(table, columns=["id", "title", "status", "uploaded"], title=f"Pages ({len(table)})")


@app.command("page")
def content_page(
    page_id: str = typer.Argument(..., help="Page id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One content page in full."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/pages/{page_id}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data, "page")
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("stats")
def content_stats(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Library counts."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/stats")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data, "stats")
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("generations")
def content_generations(
    limit: int = typer.Option(50, "--limit", "-n"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Recorded generations."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/generations", limit=limit)
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "generations")
    if json_out or output.is_pipe():
        success({"generations": rows})
        return
    table = [
        {
            "id": g.get("id", ""),
            "kind": g.get("kind") or g.get("type", ""),
            "created": str(g.get("created_at", ""))[:19],
        }
        for g in rows
    ]
    output.print_table(table, columns=["id", "kind", "created"], title=f"Generations ({len(table)})")


@app.command("duplicates")
def content_duplicates(
    website: str = typer.Option(..., "--website", "-w", help="Website the pages belong to"),
    page_id: str = typer.Option(None, "--page-id", help="Check this page id"),
    slug: str = typer.Option(None, "--slug", help="Check this slug"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Would these pages collide with something already in the library?"""
    json_out = json_mode(json_out)
    body = {
        "website": website,
        "page_ids": [page_id] if page_id else [],
        "slugs": [slug] if slug else [],
    }
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/check-duplicates", json=body)
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_json(pick_dict(data))


@app.command("page-delete")
def content_page_delete(
    page_id: str = typer.Argument(..., help="Page id"),
    yes: bool = typer.Option(False, "--yes", help="Confirm the deletion"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Delete a content page. Destructive: needs --yes."""
    json_out = json_mode(json_out)
    if not yes:
        output.print_error(
            f"Refusing to delete page {page_id} without --yes.", code="CONFIRMATION_REQUIRED"
        )
        raise typer.Exit(2)
    try:
        data = get_client(resolve_server(server)).delete(f"{BASE}/pages/{page_id}")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Deleted page {page_id}.")
