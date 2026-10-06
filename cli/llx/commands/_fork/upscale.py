"""`guaardvark upscale` — Real-ESRGAN / HAT-L / SwinIR upscaling, stills and video.

The routes disagree about how a file arrives, so the commands do too:

* stills go through `/upscale/images`, which reads `request.files.getlist("files")` —
  multipart, and it answers with a queued-job envelope, not with the image;
* video goes through `/upscale/video`, which takes a JSON `input_path` and runs as a job.

Both end in the same job queue, so `upscale jobs` / `upscale status` work for either.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success, upload_files

COMMAND_NAME = "upscale"
app = typer.Typer(help="Upscaling — stills, video, models, jobs.", no_args_is_help=True)

BASE = "/api/upscaling"


@app.command("image")
def upscale_image(
    files: list[str] = typer.Argument(..., help="Image file(s) to upscale"),
    model: str = typer.Option(None, "--model", "-m"),
    scale: float = typer.Option(None, "--scale", help="e.g. 2 or 4"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the request and resolved settings; send nothing"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Queue one or more stills. GPU work, gated by the backend."""
    json_out = json_mode(json_out)
    fields = {k: v for k, v in (("model", model), ("scale", scale)) if v is not None}
    if dry_run:
        from .dry_run import render_request

        # `upload_files` posts multipart through `client.http`, which the write capture in
        # `dry_run` cannot see, so the request is rendered directly instead of intercepted.
        render_request("upscale image", "POST", f"{BASE}/upscale/images",
                       upload={"files": [("files", f) for f in files], "fields": fields},
                       json_out=json_out)
        return
    try:
        data = upload_files(
            get_client(resolve_server(server)),
            f"{BASE}/upscale/images",
            files,
            field="files",
            fields={"model": model, "scale": scale},
        )
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success({**payload, "files": list(files)})
        return
    queued = payload.get("queued", len(files))
    output.print_success(f"Queued {queued} image(s) for upscaling.")


@app.command("video")
def upscale_video(
    input_path: str = typer.Argument(..., help="Path to the video on the server"),
    model: str = typer.Option(None, "--model", "-m"),
    scale: float = typer.Option(None, "--scale"),
    two_pass: bool = typer.Option(False, "--two-pass"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the request and resolved settings; send nothing"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Queue a video. Runs as a job; watch it with `upscale status`."""
    json_out = json_mode(json_out)
    body = {
        "input_path": input_path,
        "model": model,
        "scale": scale,
        "two_pass": two_pass,
    }
    if dry_run:
        from .dry_run import preview

        preview("upscale video",
                lambda: get_client(resolve_server(server)).post(f"{BASE}/upscale/video", json=body),
                inputs=("input_path",), explicit={"input_path"}, json_out=json_out)
        return
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/upscale/video", json=body)
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    job = payload.get("job_id") or payload.get("id") or ""
    output.print_success(f"Queued video upscale{': ' + str(job) if job else '.'}")


@app.command("models")
def upscale_models(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Available upscaling models and whether they are installed."""
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
    output.print_table(table, columns=["model", "installed"], title=f"Upscaling models ({len(table)})")


@app.command("model-download")
def upscale_model_download(
    model: str = typer.Argument(..., help="Model id to fetch"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Download an upscaling model's weights."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/models/download", json={"model": model})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Downloading {model}.")


@app.command("jobs")
def upscale_jobs(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Upscaling jobs, newest first."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/jobs")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "jobs")
    if json_out or output.is_pipe():
        success({"jobs": rows})
        return
    table = [
        {
            "id": j.get("id", "") or j.get("job_id", ""),
            "status": j.get("status", ""),
            "kind": j.get("kind") or j.get("type", ""),
        }
        for j in rows
    ]
    output.print_table(table, columns=["id", "status", "kind"], title=f"Upscaling jobs ({len(table)})")


@app.command("status")
def upscale_status(
    job_id: str = typer.Argument(..., help="Job id from `upscale jobs`"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One job."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/jobs/{job_id}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data, "job")
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("cancel")
def upscale_cancel(
    job_id: str = typer.Argument(..., help="Job id from `upscale jobs`"),
    yes: bool = typer.Option(False, "--yes", help="Confirm"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Cancel a queued or running upscale."""
    json_out = json_mode(json_out)
    if not yes:
        output.print_error(f"Refusing to cancel {job_id} without --yes.", code="CONFIRMATION_REQUIRED")
        raise typer.Exit(2)
    try:
        data = get_client(resolve_server(server)).delete(f"{BASE}/jobs/{job_id}")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Cancelled {job_id}.")
