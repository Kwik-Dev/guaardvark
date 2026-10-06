"""`<group> reproduce <id>` — rebuild a generation from its recorded inputs (issue #8, part 4).

Default: print the exact request the recorded run would replay, a copy-pasteable command
for it, and which fields the *named* command cannot express — then send nothing. `--yes`
re-runs that request through the same audited write gate as `guaardvark api request`
(`_api_guard`), so both routes behave identically. Nothing here releases a render: a music
video still stops at `awaiting_approval` and a Film Crew production at casting/storyboards,
because reproduce replays a `create`, never an `approve`.

The body is the record, not a summary: every key of the stored `retry_data.params` /
`settings_json` is carried through. Where the named command can express the whole record
(`music-video create`, `film-crew create` — after the flags added in issue #8) that command
is preferred; where it cannot (`images generate`, `videos generate`, whose named flags are
a subset of the backend's), the generic `api request` line is emitted instead and the
inexpressible fields are named. Keys that look like credentials are redacted and named.
"""
from __future__ import annotations

import json
import shlex
from typing import Any

import typer

from llx import output
from llx.client import LlxError, get_client
from llx.commands.film_crew import film_crew_app
from llx.commands.images import images_app
from llx.commands.music_video import music_video_app
from llx.commands.videos import videos_app
from llx.global_opts import get_global_json

from . import _api_guard as guard
from ._common import fail, resolve_server

EXTENDS = {
    "film-crew": film_crew_app,
    "images": images_app,
    "music-video": music_video_app,
    "videos": videos_app,
}

_YES = typer.Option(False, "--yes", "-y",
                    help="Re-run the recorded request (spends GPU / creates a project)")
_DRY = typer.Option(False, "--dry-run", help="Emit only; send nothing (the default)")

_SECRET_PARTS = ("key", "token", "secret", "password", "credential", "authorization")

# Fields each named command can express, for the groups that rebuild a body the named
# command cannot fully hold. music-video and film-crew build their own named line below.
_EXPRESSIBLE = {
    "images": {"prompts", "batch_size", "model"},
    "videos": {"prompts", "image_paths", "model", "duration_frames", "fps", "width", "height",
               "num_inference_steps", "guidance_scale", "motion_strength", "seed",
               "generate_frames_only"},
}


def _fetch(endpoint: str, server) -> dict:
    try:
        data = get_client(resolve_server(server)).get(endpoint)
    except LlxError as exc:
        fail(exc)
    return (data.get("data", data) if isinstance(data, dict) else data) or {}


def _scrub(value: Any, redacted: list) -> Any:
    """Drop credential-looking keys anywhere in the body, recording what was dropped."""
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if any(part in str(key).lower() for part in _SECRET_PARTS):
                redacted.append(str(key))
                continue
            clean[key] = _scrub(item, redacted)
        return clean
    if isinstance(value, list):
        return [_scrub(item, redacted) for item in value]
    return value


def _api_command_line(method: str, path: str, body: Any) -> str:
    line = f"guaardvark api request {method} {path} --yes"
    if body is not None:
        line += " --data " + shlex.quote(json.dumps(body, separators=(",", ":"), default=str))
    return line


def _named_command(group: str, body: Any):
    """The named-command line when it can carry the whole record, else (None, why)."""
    if not isinstance(body, dict):
        return None, []
    if group == "music-video":
        return _music_video_command(body)
    if group == "film-crew":
        return _film_crew_command(body)
    return None, sorted(set(body) - _EXPRESSIBLE[group])


def _music_video_command(body: dict):
    settings = body.get("settings") or {}
    parts = ["guaardvark", "music-video", "create",
             "--song", shlex.quote(str(body.get("song_document_id"))),
             "--style", shlex.quote(str(body.get("style_prompt") or ""))]
    if body.get("name"):
        parts += ["--name", shlex.quote(str(body["name"]))]
    if body.get("user_treatment"):
        parts += ["--treatment", shlex.quote(str(body["user_treatment"]))]
    if settings.get("i2v_model"):
        parts += ["--model", shlex.quote(str(settings["i2v_model"]))]
    if settings.get("use_lora_consistency"):
        parts += ["--lora-consistency"]
    for sid in settings.get("subject_ids") or []:
        parts += ["--cast", str(int(sid))]
    if settings.get("keyframe_model"):
        parts += ["--keyframe-model", shlex.quote(str(settings["keyframe_model"]))]
    if settings.get("planning_mode"):
        parts += ["--planning-mode", shlex.quote(str(settings["planning_mode"]))]
    if settings.get("fill_method"):
        parts += ["--fill-method", shlex.quote(str(settings["fill_method"]))]
    if settings.get("max_stretch") is not None:
        parts += ["--max-stretch", str(settings["max_stretch"])]
    if settings.get("interpolation_multiplier") is not None:
        parts += ["--interp", str(settings["interpolation_multiplier"])]
    known = {"i2v_model", "use_lora_consistency", "subject_ids", "keyframe_model",
             "planning_mode", "fill_method", "max_stretch", "interpolation_multiplier"}
    inexpressible = sorted(set(settings) - known)
    if body.get("project_id") is not None:
        inexpressible.append("project_id")
    return (" ".join(parts) if not inexpressible else None), inexpressible


def _film_crew_command(body: dict):
    settings = dict(body.get("settings") or {})
    parts = ["guaardvark", "film-crew", "create",
             "--script", shlex.quote(str(body.get("script_text") or ""))]
    if body.get("name"):
        parts += ["--name", shlex.quote(str(body["name"]))]
    video_model = settings.pop("video_model", None)
    if video_model:
        parts += ["--model", shlex.quote(str(video_model))]
    inexpressible = ["project_id"] if body.get("project_id") is not None else []
    if settings:
        parts += ["--settings", shlex.quote(json.dumps(settings, separators=(",", ":"), default=str))]
    return (" ".join(parts) if not inexpressible else None), inexpressible


def _emit(group: str, source: str, method: str, path: str, body: Any, *,
          dry_run: bool, yes: bool, server, json_out: bool, notes=()) -> None:
    as_json = bool(json_out or get_global_json() or output.is_pipe())
    output.set_json_mode(as_json)
    redacted: list = []
    clean = _scrub(body, redacted)
    named, inexpressible = _named_command(group, clean)
    # Prefer the curated command; the generic one is the lossless fallback and is always
    # shown so a caller can see both without re-deriving anything.
    command_line = named or _api_command_line(method, path, clean)
    api_line = _api_command_line(method, path, clean)

    if not yes or dry_run:
        payload = {
            "status": "reproduce",
            "command": f"{group} reproduce",
            "source": source,
            "method": method,
            "path": path,
            "body": clean,
            "command_line": command_line,
            "named_command_line": named,
            "api_command_line": api_line,
            "inexpressible": inexpressible,
            "sent": False,
        }
        if redacted:
            payload["redacted"] = sorted(set(redacted))
        if as_json:
            output.print_json(payload)
            return
        output.print_kv({"command": f"{group} reproduce", "source": source,
                         "request": f"{method} {path}"})
        if inexpressible:
            output.print_warning(
                "the named command cannot express: " + ", ".join(inexpressible)
                + " -- use the `api request` line below, which keeps them"
            )
        if redacted:
            output.print_warning("redacted credential-like keys: " + ", ".join(sorted(set(redacted))))
        output.print_panel("Recorded request body", json.dumps(clean, indent=2, default=str))
        output.print_panel("Reproduce with", command_line)
        for note in notes:
            from llx.theme import make_console

            make_console().print(f"[llx.dim]{note}[/llx.dim]")
        return

    # --yes: same gate and audit as `guaardvark api request --yes`.
    try:
        verdict = guard.classify(method, path)
    except LlxError as exc:
        fail(exc)
    base = resolve_server(server) or "the configured server"
    client = get_client(resolve_server(server))
    try:
        result = client._request(verdict.method, path, json=clean)
    except LlxError as exc:
        guard.record(verdict, server=base, outcome="error", error=str(exc), body=clean, approved=True)
        fail(exc)
    guard.record(verdict, server=base, outcome="ok", body=clean, approved=True)

    if as_json:
        output.print_json({"status": "reproduce", "sent": True, "command": f"{group} reproduce",
                           "source": source, "method": method, "path": path,
                           "body": clean, "result": result})
        return
    output.print_success(f"Re-ran {group} {source} ({method} {path})")


@images_app.command("reproduce")
def images_reproduce(
    batch_id: str = typer.Argument(..., help="Batch ID from `images list`"),
    dry_run: bool = _DRY,
    yes: bool = _YES,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Rebuild the request that produced an image batch."""
    row = _fetch(f"/api/batch-image/status/{batch_id}", server)
    retry = row.get("retry_data") or {}
    prompts = retry.get("prompts") or ([retry["prompt"]] if retry.get("prompt") else [])
    if not prompts:
        output.print_error(f"batch {batch_id} has no recorded prompts -- cannot reproduce",
                           code="NO_RECORD")
        raise typer.Exit(2)
    body = {"prompts": prompts, "batch_size": len(prompts), **(retry.get("params") or {})}
    _emit("images", batch_id, "POST", "/api/batch-image/generate/prompts", body,
          dry_run=dry_run, yes=yes, server=server, json_out=json_out)


@videos_app.command("reproduce")
def videos_reproduce(
    batch_id: str = typer.Argument(..., help="Batch ID from `videos list`"),
    dry_run: bool = _DRY,
    yes: bool = _YES,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Rebuild the request that produced a video batch."""
    row = _fetch(f"/api/batch-video/status/{batch_id}", server)
    retry = row.get("retry_data") or {}
    mode = retry.get("mode") or "text"
    params = retry.get("params") or {}
    if mode == "image":
        paths = retry.get("image_paths") or []
        if not paths:
            output.print_error(f"batch {batch_id} recorded images but none survived; cannot reproduce",
                               code="NO_RECORD")
            raise typer.Exit(2)
        # The image-mode record keeps the prompt at the top level next to image_paths --
        # `retry_data` is written as {mode, image_paths, prompt, params} and `params` does
        # NOT carry it -- and the image endpoint reads `data["prompt"]`. Dropping it re-ran
        # the batch with an empty prompt.
        body = {"image_paths": paths, "prompt": retry.get("prompt") or "", **params}
        endpoint = "/api/batch-video/generate/image"
    else:
        prompts = retry.get("prompts") or []
        if not prompts:
            output.print_error(f"batch {batch_id} has no recorded prompts -- cannot reproduce",
                               code="NO_RECORD")
            raise typer.Exit(2)
        body = {"prompts": prompts, **params}
        endpoint = "/api/batch-video/generate/text"
    _emit("videos", batch_id, "POST", endpoint, body,
          dry_run=dry_run, yes=yes, server=server, json_out=json_out)


@music_video_app.command("reproduce")
def music_video_reproduce(
    mv_id: int = typer.Argument(..., help="Music-video id"),
    dry_run: bool = _DRY,
    yes: bool = _YES,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Rebuild the `create` that produced a music-video project."""
    row = _fetch(f"/api/music-video/{mv_id}", server)
    if not row.get("style_prompt"):
        output.print_error(f"music video {mv_id} has no recorded style/inputs -- cannot reproduce",
                           code="NO_RECORD")
        raise typer.Exit(2)
    if row.get("song_document_id") is None:
        # A null link is not an empty field: the song may still be on disk, but the record
        # does not say which one, and a replay with no song would silently make a different
        # video. Fail loudly rather than emit `--song None`.
        output.print_error(
            f"music video {mv_id} has no linked song document (song_document_id is null); "
            "pass --song explicitly to a new `music-video create`",
            code="NO_RECORD",
        )
        raise typer.Exit(2)
    body: dict = {"name": row.get("name"), "song_document_id": row.get("song_document_id"),
                  "style_prompt": row.get("style_prompt")}
    if row.get("user_treatment"):
        body["user_treatment"] = row["user_treatment"]
    if row.get("settings"):
        body["settings"] = row["settings"]
    _emit("music-video", str(mv_id), "POST", "/api/music-video", body,
          dry_run=dry_run, yes=yes, server=server, json_out=json_out,
          notes=("this replays create, not approve: the new project stops at awaiting_approval",))


@film_crew_app.command("reproduce")
def film_crew_reproduce(
    prod_id: int = typer.Argument(..., help="Production id"),
    dry_run: bool = _DRY,
    yes: bool = _YES,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Rebuild the `create` that produced a Film Crew production."""
    row = _fetch(f"/api/production/{prod_id}", server)
    if not row.get("script_text"):
        output.print_error(f"production {prod_id} has no recorded screenplay -- cannot reproduce",
                           code="NO_RECORD")
        raise typer.Exit(2)
    body: dict = {"name": row.get("name"), "script_text": row.get("script_text"),
                  "project_id": row.get("project_id")}
    if row.get("settings_json"):
        body["settings"] = row["settings_json"]
    _emit("film-crew", str(prod_id), "POST", "/api/production", body,
          dry_run=dry_run, yes=yes, server=server, json_out=json_out,
          notes=("this replays create: the new production stops at casting / storyboards",))
