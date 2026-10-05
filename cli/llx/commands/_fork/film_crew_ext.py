"""Adds `guaardvark film-crew subjects|shots|shot|templates` — what the pipeline produced.

This module is an *extension* of an upstream command group. `llx/commands/film_crew.py`
is upstream-owned; a second Typer app named "film-crew" cannot be mounted (Click would
silently drop one, which the registry reports), and editing the file would be another
conflict on every sync. So this module has **no** `COMMAND_NAME` and **no** `app`: the
registry imports it and mounts nothing, and the import registers the commands below on the
upstream `film_crew_app` object. One new file, zero upstream lines — the `audio_ext.py`
pattern.

What was missing: `film-crew status` printed a shot *count*. A production is a pipeline of
five agents, and none of what they produced was reachable — which subjects the screenwriter
extracted and whether they are cast, what each shot says and whether its clip exists, which
storyboard frame belongs to which shot, which script templates exist. These four commands
are read-only, so they cannot regress anything, and they need no D2 exception.

The three commands that *start* a render live in `render_gates.py` (CLI_PLAN D6), kept
separate so this module stays clean to the D2 static scan.
"""
from __future__ import annotations

from pathlib import Path

import typer

from llx import output
from llx.client import LlxError, get_client
from llx.commands.film_crew import film_crew_app

from ._common import fail, json_mode, pick_list, resolve_server, success

_BASE = "/api/production"


def _client(server):
    return get_client(resolve_server(server))


@film_crew_app.command("subjects")
def fc_subjects(
    prod_id: int = typer.Argument(..., help="Production id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """The subjects the screenwriter extracted, and their casting state.

    `cast_required` is the resolved requirement: True means identity-locked, so casting
    cannot be confirmed until it has a LoRA. That is the question `film-crew status`
    could not answer, and the one that blocks the first render gate.
    """
    as_json = json_mode(json_out)
    try:
        data = _client(server).get(f"{_BASE}/{prod_id}/subjects")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "subjects")
    if as_json:
        success(rows)
        return
    output.print_table(
        [
            {
                "id": s.get("id", ""),
                "name": s.get("name", ""),
                "kind": s.get("kind", ""),
                "training": s.get("training_status") or "-",
                "lora": "yes" if s.get("lora_path") else "no",
                "needs LoRA": "yes" if s.get("cast_required") else "no",
            }
            for s in rows
        ],
        columns=["id", "name", "kind", "training", "lora", "needs LoRA"],
        title=f"Subjects ({len(rows)})",
    )


@film_crew_app.command("shots")
def fc_shots(
    prod_id: int = typer.Argument(..., help="Production id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Every shot in a production: what it says, and what exists for it.

    The clip column is the resumable-render state — a shot with a storyboard but no clip
    is one the editor has not rendered yet.
    """
    as_json = json_mode(json_out)
    try:
        data = _client(server).get(f"{_BASE}/{prod_id}")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "shots")
    if as_json:
        success(rows)
        return
    output.print_table(
        [
            {
                "shot": s.get("shot_number", s.get("id", "")),
                "scene": s.get("scene_number", ""),
                "shot_id": s.get("id", ""),
                "approved": "yes" if s.get("approved") else "no",
                "storyboard": "yes" if s.get("storyboard_image_path") else "no",
                "clip": "yes" if s.get("video_clip_path") else "no",
                "regen": s.get("regen_count", 0),
                "description": (s.get("description") or "")[:60],
            }
            for s in rows
        ],
        columns=["shot", "scene", "shot_id", "approved", "storyboard", "clip", "regen", "description"],
        title=f"Shots ({len(rows)})",
    )


@film_crew_app.command("shot")
def fc_shot(
    prod_id: int = typer.Argument(..., help="Production id"),
    shot_id: int = typer.Argument(..., help="Shot id (from `film-crew shots`)"),
    image: str = typer.Option(None, "--image", help="Download the storyboard frame to this path"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One shot's full record, and optionally its storyboard frame as a PNG."""
    as_json = json_mode(json_out)
    client = _client(server)
    try:
        data = client.get(f"{_BASE}/{prod_id}")
        rows = pick_list(data, "shots")
        shot = next((s for s in rows if int(s.get("id", -1)) == int(shot_id)), None)
        if shot is None:
            raise LlxError(f"production {prod_id} has no shot {shot_id}")
        if image:
            dest = Path(image).expanduser()
            if not shot.get("storyboard_image_path"):
                raise LlxError(
                    f"shot {shot_id} has no storyboard image yet — generate storyboards first"
                )
            client.download(f"{_BASE}/{prod_id}/storyboard/shot/{shot_id}/image", dest)
    except LlxError as exc:
        fail(exc)
    if image:
        if as_json:
            success({"shot": shot, "image": str(dest)})
            return
        output.print_success(f"Storyboard frame written to {dest}")
        return
    if as_json:
        success(shot)
        return
    output.print_kv(
        {
            "Shot id": shot.get("id", ""),
            "Scene / shot": f"{shot.get('scene_number', '')} / {shot.get('shot_number', '')}",
            "Approved": "yes" if shot.get("approved") else "no",
            "Regenerated": shot.get("regen_count", 0),
            "Storyboard": shot.get("storyboard_image_path") or "(none)",
            "Clip": shot.get("video_clip_path") or "(none)",
        },
        title="Shot",
    )
    if shot.get("description"):
        output.print_markdown(shot["description"])


@film_crew_app.command("templates")
def fc_templates(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Script templates the screenwriter can be pointed at (`--file` takes their text)."""
    as_json = json_mode(json_out)
    try:
        data = _client(server).get(f"{_BASE}/script-templates")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "templates")
    if as_json:
        success(rows)
        return
    output.print_table(
        [
            {"filename": t.get("filename", ""), "name": t.get("name", ""), "bytes": t.get("size_bytes", 0)}
            for t in rows
        ],
        columns=["filename", "name", "bytes"],
        title=f"Script templates ({len(rows)})",
    )
    # There is no create-time template flag upstream; a template is read and passed as the
    # script. Saying so beats letting someone hunt for an option that does not exist.
    if rows:
        output.print_warning(
            "templates are text: fetch one and pass it with "
            "`film-crew create --file <path>`"
        )
