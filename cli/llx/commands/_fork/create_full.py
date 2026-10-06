"""`create` with the full inputs, and `--dry-run` (issue #8, part 3).

Upstream `music-video create` and `film-crew create` forward a handful of fields and drop
the rest, so `settings_json` could never be filled from the CLI and a stored run could not
be reproduced. These overrides accept the missing inputs, build the whole request, and keep
the no-spend `--dry-run` from `dry_run.py`. Real runs post exactly the body the dry run
showed.

Nothing here releases a render: a music video still stops at `awaiting_approval`, a Film
Crew production still stops at casting/storyboards.
"""
from __future__ import annotations

import json
from pathlib import Path

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError
from llx.commands import film_crew as _film_crew
from llx.commands import music_video as _music_video
from llx.commands.film_crew import film_crew_app
from llx.commands.music_video import music_video_app
from llx.global_opts import get_global_json
from llx.theme import make_console

from .dry_run import preview

console = make_console()

EXTENDS = {"film-crew": film_crew_app, "music-video": music_video_app}

_DRY = typer.Option(False, "--dry-run", help="Show the request and resolved settings; send nothing")

_MV_INPUTS = ("song_document_id", "style_prompt", "name")


def _mv_client(server):
    # Reuse the upstream module's symbols so fixtures that patch `llx.commands.music_video`
    # still drive this command (same rationale as the `list` override).
    return _music_video.get_client(server or _music_video.get_global_server())


def _fc_client(server):
    return _film_crew.get_client(server or _film_crew.get_global_server())


def _title(name: str | None, song: str) -> str:
    return (name or "").strip() or Path(song).expanduser().stem or "Music video"


def _mv_settings(model, cast, lora_consistency, keyframe_model, planning_mode,
                 fill_method, max_stretch, interp) -> dict:
    settings: dict = {}
    if model:
        settings["i2v_model"] = model.strip()
    if lora_consistency:
        settings["use_lora_consistency"] = True
    if cast:
        settings["subject_ids"] = [int(s) for s in cast]
    # The Studio forces `from-lora` when consistency is ticked; mirror that so the body the
    # CLI writes matches what the UI writes.
    kf = (keyframe_model or "").strip() or ("from-lora" if lora_consistency else "")
    if kf:
        settings["keyframe_model"] = kf
    if planning_mode:
        settings["planning_mode"] = planning_mode.strip()
    if fill_method:
        settings["fill_method"] = fill_method.strip()
    if max_stretch is not None:
        settings["max_stretch"] = float(max_stretch)
    if interp is not None:
        settings["interpolation_multiplier"] = int(interp)
    return settings


def _mv_body(song_id: int, style: str, title: str, settings: dict, treatment: str | None) -> dict:
    body = {"name": title, "song_document_id": song_id, "style_prompt": style.strip()}
    if settings:
        body["settings"] = settings
    if treatment:
        body["user_treatment"] = treatment
    return body


def _mv_explicit(model, cast, lora_consistency, keyframe_model, planning_mode,
                 fill_method, max_stretch, interp, treatment) -> set:
    keys = set()
    if treatment:
        keys.add("user_treatment")
    if any((model, cast, lora_consistency, keyframe_model, planning_mode, fill_method,
            max_stretch is not None, interp is not None)):
        keys.add("settings")
    return keys


@music_video_app.command("create")
def music_video_create(
    song: str = typer.Option(..., "--song", help="Document id or path to an audio file"),
    style: str = typer.Option(..., "--style", help="Visual style for the Director"),
    name: str = typer.Option(None, "--name", "-n", help="Project name (default: song stem)"),
    model: str = typer.Option(None, "--model", "-m", help="I2V model id (default: active video model)"),
    treatment: str = typer.Option(None, "--treatment", help="Your treatment / short story; seeds the Director"),
    cast: list[int] = typer.Option(None, "--cast", help="Cast subject id to lock (repeatable)"),
    lora_consistency: bool = typer.Option(False, "--lora-consistency",
                                          help="Lock the cast identity into every keyframe"),
    keyframe_model: str = typer.Option(None, "--keyframe-model",
                                       help="Keyframe model (default: from-lora when consistency is on)"),
    planning_mode: str = typer.Option(None, "--planning-mode", help="Director planning mode (e.g. narrative)"),
    fill_method: str = typer.Option(None, "--fill-method", help="Retiming fill (e.g. forward)"),
    max_stretch: float = typer.Option(None, "--max-stretch", help="Max retime stretch"),
    interp: int = typer.Option(None, "--interp", help="Interpolation multiplier"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a music-video project and start analysis. Does not render clips."""
    settings = _mv_settings(model, cast, lora_consistency, keyframe_model, planning_mode,
                            fill_method, max_stretch, interp)
    explicit = {"song_document_id", "style_prompt"} | ({"name"} if name else set())
    explicit |= _mv_explicit(model, cast, lora_consistency, keyframe_model, planning_mode,
                             fill_method, max_stretch, interp, treatment)

    if dry_run:
        if song.strip().isdigit():
            body = _mv_body(int(song.strip()), style, _title(name, song), settings, treatment)
            preview("music-video create",
                    lambda: _mv_client(server).post("/api/music-video", json=body),
                    inputs=_MV_INPUTS, explicit=explicit, json_out=json_out)
            return

        def build():
            api = _mv_client(server)
            song_id = _music_video._song_document_id(api, song)
            api.post("/api/music-video", json=_mv_body(song_id, style, _title(name, song),
                                                       settings, treatment))

        preview("music-video create", build, inputs=_MV_INPUTS, explicit=explicit,
                json_out=json_out,
                notes=("--song is a file path: the preview stops at the upload, because the "
                       "create body needs the document id the upload would return",))
        return

    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        api = _mv_client(server)
        song_id = _music_video._song_document_id(api, song)
        body = _mv_body(song_id, style, _title(name, song), settings, treatment)
        result = _music_video._unwrap(api.post("/api/music-video", json=body))
    except LlxConnectionError as exc:
        output.print_error(str(exc), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as exc:
        output.print_error(str(exc), code="API_ERROR")
        raise typer.Exit(1)

    if json_out:
        output.print_json(result)
        return
    output.print_success(
        f"Music video '{result.get('name', _title(name, song))}' created "
        f"(id {result.get('id', '')}, stage: {result.get('current_stage', '')})"
    )
    console.print("[llx.dim]Analysis is running. Approve the cut plan in Studio before any clip renders.[/llx.dim]")


def _parse_settings(raw: str) -> dict:
    """A JSON object, or @path/to/file.json. The point is to lose nothing, so a bad shape
    is an error rather than a silently ignored flag."""
    text = raw[1:] if raw.startswith("@") else raw
    if raw.startswith("@"):
        path = Path(text).expanduser()
        if not path.is_file():
            raise typer.BadParameter(f"no such settings file: {text}")
        text = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--settings is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--settings must be a JSON object")
    return parsed


@film_crew_app.command("create")
def film_crew_create(
    script: str = typer.Option(None, "--script", help="Screenplay text"),
    file: str = typer.Option(None, "--file", help="Path to a screenplay file"),
    name: str = typer.Option(None, "--name", "-n", help="Production name (default: first line)"),
    model: str = typer.Option(None, "--model", "-m", help="Scene / I2V model id (default: active video model)"),
    settings: str = typer.Option(None, "--settings", help="Extra settings as a JSON object, or @path/to/file.json"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a Film Crew production and start the screenwriter. Does not render shots."""
    script_text = _film_crew._script_text(script, file)
    first = next((ln.strip() for ln in script_text.splitlines() if ln.strip()), "Film Crew")
    title = (name or "").strip() or first[:80]
    resolved: dict = {}
    if (model or "").strip():
        resolved["video_model"] = model.strip()
    if settings:
        resolved.update(_parse_settings(settings))

    body: dict = {"name": title, "script_text": script_text, "project_id": None}
    if resolved:
        body["settings"] = resolved
    explicit = {"script_text"} | ({"name"} if name else set()) | ({"settings"} if resolved else set())

    if dry_run:
        preview("film-crew create",
                lambda: _fc_client(server).post("/api/production", json=body),
                inputs=("name", "script_text"), explicit=explicit, json_out=json_out)
        return

    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        result = _film_crew._unwrap(_fc_client(server).post("/api/production", json=body))
    except LlxConnectionError as exc:
        output.print_error(str(exc), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as exc:
        output.print_error(str(exc), code="API_ERROR")
        raise typer.Exit(1)

    if json_out:
        output.print_json(result)
        return
    output.print_success(
        f"Film Crew '{result.get('name', title)}' created "
        f"(id {result.get('id', '')}, stage: {result.get('current_stage', '')})"
    )
    console.print("[llx.dim]Screenwriter is running. Casting and storyboards wait in Studio.[/llx.dim]")
