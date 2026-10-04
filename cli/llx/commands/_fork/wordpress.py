"""`guaardvark wordpress` — WordPress sites, page pull and the processing queue.

Read-only except the pull/process actions, which fetch from a site or act on queued
pages. Those are the workflow this group exists for; the destructive site routes
(`POST /sites`, `PUT`, `DELETE`) are left to the Studio, where the credentials and the
site list live side by side.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "wordpress"
app = typer.Typer(help="WordPress sites, page pull and the processing queue.", no_args_is_help=True)

BASE = "/api/wordpress"


@app.command("sites")
def wp_sites(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Configured WordPress sites."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/sites")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "sites")
    if json_out or output.is_pipe():
        success({"sites": rows})
        return
    table = [
        {
            "id": s.get("id", ""),
            "name": (s.get("name") or "")[:32],
            "url": (s.get("url") or "")[:44],
            "connected": s.get("is_connected", s.get("connected", "")),
        }
        for s in rows
    ]
    output.print_table(table, columns=["id", "name", "url", "connected"],
                       title=f"WordPress sites ({len(table)})")


@app.command("site")
def wp_site(
    site_id: int = typer.Argument(..., help="Site id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One site."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/sites/{site_id}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data, "site")
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("site-test")
def wp_site_test(
    site_id: int = typer.Argument(..., help="Site id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Check the stored credentials against the site."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/sites/{site_id}/test")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data) if isinstance(data, dict) else {"ok": True})
        return
    output.print_success("Connection OK.")


@app.command("pages")
def wp_pages(
    limit: int = typer.Option(50, "--limit", "-n"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Local WordPress page rows."""
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
            "title": (p.get("title") or p.get("slug") or "")[:44],
            "status": p.get("status", ""),
            "site": p.get("site_id", ""),
        }
        for p in rows
    ]
    output.print_table(table, columns=["id", "title", "status", "site"],
                       title=f"WordPress pages ({len(table)})")


@app.command("pull-sitemap")
def wp_pull_sitemap(
    site_id: int = typer.Option(..., "--site", "-s", help="Site id"),
    server: str = typer.Option(None, "--server"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Read a site's sitemap and stage its pages."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/pull/sitemap", json={"site_id": site_id})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Sitemap pulled for site {site_id}.")


@app.command("pull-list")
def wp_pull_list(
    site_id: int = typer.Option(..., "--site", help="Site id"),
    server: str = typer.Option(None, "--server"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Fetch the site's post list."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/pull/list", json={"site_id": site_id})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Post list pulled for site {site_id}.")


@app.command("pull-page")
def wp_pull_page(
    site_id: int = typer.Argument(..., help="Site id"),
    post_id: int = typer.Argument(..., help="Remote post id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Fetch one page from the site."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/pull/page/{site_id}/{post_id}")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data) if isinstance(data, dict) else {})
        return
    output.print_success(f"Pulled post {post_id} from site {site_id}.")


@app.command("pull-bulk")
def wp_pull_bulk(
    site_id: int = typer.Option(..., "--site", help="Site id"),
    server: str = typer.Option(None, "--server"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Bulk-pull the site's pages."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/pull/bulk", json={"site_id": site_id})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Bulk pull started for site {site_id}.")


@app.command("pull-status")
def wp_pull_status(
    site_id: int = typer.Argument(..., help="Site id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Progress of a bulk pull."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/pull/status/{site_id}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("process-queue")
def wp_process_queue(
    from_file: str = typer.Option(None, "--from-file", help="Queue payload as JSON"),
    server: str = typer.Option(None, "--server"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Stage pages for processing."""
    json_out = json_mode(json_out)
    from ._common import read_json_payload

    try:
        body = read_json_payload(from_file) if from_file else {}
        data = get_client(resolve_server(server)).post(f"{BASE}/process/queue", json=body)
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success("Queued.")


@app.command("process-run")
def wp_process_run(
    yes: bool = typer.Option(False, "--yes", help="Confirm: this sends the queued pages to WordPress"),
    server: str = typer.Option(None, "--server"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Execute the processing queue. Outbound: needs --yes."""
    json_out = json_mode(json_out)
    if not yes:
        output.print_error(
            "Executing the queue publishes the staged pages to WordPress. Re-run with --yes.",
            code="CONFIRMATION_REQUIRED",
        )
        raise typer.Exit(2)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/process/queue/execute")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data) if isinstance(data, dict) else {})
        return
    output.print_success("Queue executed.")
