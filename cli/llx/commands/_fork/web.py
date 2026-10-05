"""`guaardvark websearch` — web research: search, quick search, sitemap, status.

Distinct from the top-level `search`, which searches *your indexed documents*. This
group asks the internet, through the backend's web-search routes (and therefore
through its outbound policy — a disabled web switch refuses here too, which is the
whole point of routing it through the backend rather than fetching directly).

The shell group is named `websearch`, not `web`: upstream's REPL already has a
`/web` command, and that one opens the Guaardvark web UI. Two different things
under one word is a trap, so the shell name spells out which one it is.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "websearch"
app = typer.Typer(help="Web research — search, quick search, sitemap.", no_args_is_help=True)

BASE = "/api/web-search"


@app.command("status")
def web_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Whether web access is enabled, and by which source."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/status")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("search")
def web_search(
    query: str = typer.Argument(..., help="What to search for"),
    max_results: int = typer.Option(5, "--max", "-m", help="How many results"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Search the web, with snippets."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(
            f"{BASE}/search", json={"query": query, "max_results": max_results}
        )
    except LlxError as exc:
        fail(exc)
    results = pick_list(data, "results")
    if json_out or output.is_pipe():
        success({"query": query, "results": results})
        return
    table = [
        {
            "title": (r.get("title") or "")[:52],
            "url": (r.get("url") or r.get("link") or "")[:60],
        }
        for r in results
    ]
    output.print_table(table, columns=["title", "url"], title=f"Results ({len(table)})")


@app.command("quick-search")
def web_quick_search(
    query: str = typer.Argument(..., help="What to search for"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """A single best-effort answer, without the full result list."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/quick-search", json={"query": query})
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success({"query": query, **payload})
        return
    output.print_json({"query": query, **payload})


@app.command("sitemap")
def web_sitemap(
    url: str = typer.Argument(..., help="Sitemap URL"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Summarise a sitemap: how many URLs, what shape."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/sitemap", json={"url": url})
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)
