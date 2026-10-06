"""`guaardvark infographic` — generate an infographic as a rendered image.

GPU work, queued through the backend like every other generation, so the GPU gate and
the model-availability preflight apply whether the request came from the Studio or here.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "infographic"
app = typer.Typer(help="Infographics — generate, models, status.", no_args_is_help=True)

BASE = "/api/infographic"


@app.command("generate")
def infographic_generate(
    scene: str = typer.Option(None, "--scene", help="What the infographic shows"),
    raw_prompt: str = typer.Option(None, "--prompt", help="Free-form prompt instead of fields"),
    title: str = typer.Option(None, "--title"),
    footer: str = typer.Option(None, "--footer"),
    style: str = typer.Option("editorial", "--style"),
    aspect: str = typer.Option("16:9", "--aspect"),
    hashtag: list[str] = typer.Option(None, "--hashtag", help="Repeatable"),
    callout: list[str] = typer.Option(None, "--callout", help="Repeatable"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the request and resolved settings; send nothing"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate an infographic. Queues GPU work."""
    json_out = json_mode(json_out)
    if not (scene or raw_prompt):
        output.print_error("Give --scene or --prompt.", code="MISSING_INPUT")
        raise typer.Exit(2)
    body = {
        "scene": scene or "",
        "raw_prompt": raw_prompt or "",
        "title": title or "",
        "footer": footer or "",
        "style": style,
        "aspect": aspect,
        "hashtags": hashtag or [],
        "callouts": callout or [],
    }
    if dry_run:
        from .dry_run import preview

        preview(
            "infographic generate",
            lambda: get_client(resolve_server(server)).post(f"{BASE}/generate", json=body),
            inputs=("scene", "raw_prompt", "title", "footer"),
            explicit={"scene", "raw_prompt", "title", "footer"}
            | ({"style"} if style != "editorial" else set())
            | ({"aspect"} if aspect != "16:9" else set())
            | ({"hashtags"} if hashtag else set())
            | ({"callouts"} if callout else set()),
            json_out=json_out,
        )
        return
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/generate", json=body)
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    ident = payload.get("id") or payload.get("job_id") or ""
    output.print_success(f"Infographic queued{': ' + str(ident) if ident else '.'}")


@app.command("models")
def infographic_models(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Infographic models and whether they are installed."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/models")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "models")
    if json_out or output.is_pipe():
        success({"models": rows})
        return
    table = [
        {
            "model": (m.get("id") or m.get("name") or str(m))[:36],
            "installed": m.get("installed", ""),
        }
        for m in rows if isinstance(m, dict)
    ]
    output.print_table(table, columns=["model", "installed"], title=f"Infographic models ({len(table)})")


@app.command("model-download")
def infographic_model_download(
    model: str = typer.Argument(..., help="Model id to fetch"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Download an infographic model's weights."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/models/download", json={"model": model})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Downloading {model}.")


@app.command("download-status")
def infographic_download_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Progress of in-flight model downloads."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/models/download-status")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("status")
def infographic_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Ready state and recent infographic jobs."""
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
