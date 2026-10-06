"""`guaardvark video-editor` — the editor's jobs, catalog and render submitter.

Scope (CLI_PLAN 3.4 and the §3 contract): the CLI drives the editor's *operations*; it
does not author a timeline. Editing — dragging clips, nudging captions, trimming on a
waveform — is what the browser is for, and a terminal re-implementation would be worse
at it. So this group inspects projects, jobs and the filter/transition catalog, opens a
project in Shotcut, imports/exports captions, and submits a beat-sync render from a song
plus a pool of clips (or from a full payload with --from-file).

The render spends GPU. It is not a bypass: it posts to the backend, which owns the job
queue and the GPU session, exactly like `videos generate`. Nothing in this package talks
to a plugin port directly, which `test_fork_gpu_gate.py` enforces.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, read_json_payload, resolve_server, success

COMMAND_NAME = "video-editor"
app = typer.Typer(help="Video editor — projects, jobs, catalog, renders.", no_args_is_help=True)

BASE = "/api/video-editor"


@app.command("health")
def ve_health(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Is the editor plugin up?"""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/health")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("projects")
def ve_projects(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Named editor projects."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/projects")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "projects")
    if json_out or output.is_pipe():
        success({"projects": rows})
        return
    table = [
        {
            "id": (p.get("id") or "")[:12],
            "name": (p.get("name") or "")[:40],
            "dirty": p.get("isDirty", ""),
        }
        for p in rows
    ]
    output.print_table(table, columns=["id", "name", "dirty"], title=f"Editor projects ({len(table)})")


@app.command("project")
def ve_project(
    project_id: str = typer.Argument(..., help="Project id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One editor project."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/projects/{project_id}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("project-new")
def ve_project_new(
    name: str = typer.Argument(..., help="Project name"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a named editor project."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/projects", json={"name": name})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Created project {name!r}.")


@app.command("project-delete")
def ve_project_delete(
    project_id: str = typer.Argument(..., help="Project id"),
    yes: bool = typer.Option(False, "--yes", help="Confirm the deletion"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Delete an editor project. Destructive: needs --yes."""
    json_out = json_mode(json_out)
    if not yes:
        output.print_error(
            f"Refusing to delete project {project_id} without --yes.", code="CONFIRMATION_REQUIRED"
        )
        raise typer.Exit(2)
    try:
        data = get_client(resolve_server(server)).delete(f"{BASE}/projects/{project_id}")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Deleted project {project_id}.")


@app.command("jobs")
def ve_jobs(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Editor jobs (analyze, render, trim)."""
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
            "id": (j.get("id") or j.get("job_id") or "")[:14],
            "kind": j.get("kind") or j.get("type", ""),
            "status": j.get("status", ""),
        }
        for j in rows
    ]
    output.print_table(table, columns=["id", "kind", "status"], title=f"Editor jobs ({len(table)})")


@app.command("job")
def ve_job(
    job_id: str = typer.Argument(..., help="Job id from `video-editor jobs`"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One editor job."""
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


@app.command("filters")
def ve_filters(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Filter presets the style recipes can name."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/catalog/filters")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data) or {"filters": pick_list(data, "filters")}
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("transitions")
def ve_transitions(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Transition presets."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/catalog/transitions")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data) or {"transitions": pick_list(data, "transitions")}
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("render")
def ve_render(
    audio: str = typer.Option(None, "--audio", "-a", help="Soundtrack path on the server"),
    video: list[str] = typer.Option(None, "--video", "-v", help="Source clip path (repeatable)"),
    from_file: str = typer.Option(None, "--from-file", help="Full render payload as JSON"),
    render_mp4: bool = typer.Option(True, "--mp4/--no-mp4", help="Also encode the final MP4"),
    subdivision: int = typer.Option(2, "--subdivision"),
    min_clip_seconds: float = typer.Option(1.2, "--min-clip-seconds"),
    tightness: int = typer.Option(100, "--tightness"),
    seed: int = typer.Option(None, "--seed"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the request and resolved settings; send nothing"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Beat-sync a song against a pool of clips and render.

    GPU work, queued through the backend like any other generation. Give --audio and
    one or more --video, or hand it a whole payload with --from-file.
    """
    json_out = json_mode(json_out)
    try:
        if from_file:
            body = read_json_payload(from_file)
            body.setdefault("render_mp4", render_mp4)
        else:
            if not audio or not video:
                output.print_error(
                    "Need --audio and at least one --video, or --from-file.",
                    code="MISSING_INPUT",
                )
                raise typer.Exit(2)
            body = {
                "audio_path": audio,
                "video_paths": list(video),
                "render_mp4": render_mp4,
                "subdivision": subdivision,
                "min_clip_seconds": min_clip_seconds,
                "tightness": tightness,
            }
            if seed is not None:
                body["seed"] = seed
        if dry_run:
            from .dry_run import preview

            preview("video-editor render",
                    lambda: get_client(resolve_server(server)).post(f"{BASE}/beat-sync/render", json=body),
                    inputs=("audio_path", "video_paths"),
                    explicit={"audio_path", "video_paths"}, json_out=json_out)
            return
        data = get_client(resolve_server(server)).post(f"{BASE}/beat-sync/render", json=body)
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    job = payload.get("job_id") or payload.get("id") or ""
    output.print_success(f"Render queued{': ' + str(job) if job else '.'} Track it with `video-editor job`.")


@app.command("analyze")
def ve_analyze(
    audio: str = typer.Option(None, "--audio", "-a", help="Song path on the server"),
    from_file: str = typer.Option(None, "--from-file", help="Full analyze payload as JSON"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the request and resolved settings; send nothing"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Analyse a song: sections, beats, cut plan."""
    json_out = json_mode(json_out)
    try:
        if from_file:
            body = read_json_payload(from_file)
        elif audio:
            body = {"audio_path": audio}
        else:
            output.print_error("Need --audio or --from-file.", code="MISSING_INPUT")
            raise typer.Exit(2)
        if dry_run:
            from .dry_run import preview

            preview("video-editor analyze",
                    lambda: get_client(resolve_server(server)).post(f"{BASE}/analyze", json=body),
                    inputs=("audio_path",), explicit={"audio_path"}, json_out=json_out)
            return
        data = get_client(resolve_server(server)).post(f"{BASE}/analyze", json=body)
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success("Analyzed.")


@app.command("captions-export")
def ve_captions_export(
    from_file: str = typer.Option(None, "--from-file", help="Arrangement payload as JSON"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Write an arrangement's captions to an SRT Document."""
    json_out = json_mode(json_out)
    try:
        body = read_json_payload(from_file) if from_file else {}
        data = get_client(resolve_server(server)).post(f"{BASE}/captions/export", json=body)
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_success(f"Captions written{': doc ' + str(payload.get('document_id')) if payload.get('document_id') else '.'}")


@app.command("captions-import")
def ve_captions_import(
    document_id: int = typer.Option(None, "--document-id", help="SRT Document id"),
    path: str = typer.Option(None, "--path", help="Or an .srt path on the server"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Read captions back from an SRT file."""
    json_out = json_mode(json_out)
    if not document_id and not path:
        output.print_error("Need --document-id or --path.", code="MISSING_INPUT")
        raise typer.Exit(2)
    body = {"document_id": document_id} if document_id else {"path": path}
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/captions/import", json=body)
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success("Captions imported.")


@app.command("shotcut")
def ve_shotcut(
    mlt_path: str = typer.Argument(..., help=".mlt under the editor's mlt-projects dir"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Open a project in Shotcut on the machine running the backend."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/open-in-shotcut", json={"mlt_path": mlt_path})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success("Shotcut launched.")
