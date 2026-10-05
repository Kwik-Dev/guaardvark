"""Adds `guaardvark video-editor captions-burn` and `captions-status` — put an SRT on a video.

This module is an *extension* of an existing command group, the `audio_ext.py` way: it has
**no** `COMMAND_NAME` and **no** `app`, so the registry imports it and mounts nothing, and
importing it registers the two commands below on the `video-editor` app that
`_fork/video_editor.py` already defines. No existing file is edited.

Import order, since it is the one non-obvious thing here: the registry walks this package in
sorted order, and `captions` sorts before `video_editor`, so this import is what pulls
`_fork/video_editor.py` in first. That is safe — the import is a plain module import with no
cycle, `video_editor` does not import this module, and `main.py` mounts the app exactly once
from the registry's list rather than per module.

What was missing: `video-editor captions-export` and `captions-import` move SRT text in and
out of an arrangement, but nothing put captions *onto* a video from the terminal. The
backend already does it in one hop — `/api/video-overlay/render-timeline` takes timed
`text_elements`, which is exactly what a caption track is — so this is a wrapper over a
route that exists, not new capability.

Three routes, and this is the whole contract:

1. `POST /api/video-editor/captions/import` (backend/api/video_editor_api.py) parses the
   SRT with the backend's own parser and answers a **bare** `{"captions": [{text, start,
   end}]}` — no success/data envelope, unlike the blueprint next door. Reusing that parser
   is deliberate: a second SRT parser in the CLI would drift from this one.
2. `POST /api/video-overlay/render-timeline` (backend/api/video_overlay_api.py) takes
   `video_document_id`, `text_elements` and optional `audio_document_id` / `backend`, and
   answers 202 with a `job_id`. The accepted element keys are exactly `text, fontSize,
   fontColor, x, y, rotation, startSeconds, endSeconds`.
3. `GET /api/video-overlay/render-status/<job_id>` reports progress and, when it finishes,
   the document id of the new video.

The render goes through the backend, which owns the job queue and the GPU session, so this
is the same class of action as `videos generate` — no `--yes` gate (this is not a decision
route; D2 governs approvals, and there is nothing here a person would want to inspect first
beyond the caption file they just wrote). Nothing in this module talks to a plugin port.
"""
from __future__ import annotations

from enum import Enum

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success
from .video_editor import app as video_editor_app


class CaptionEngine(str, Enum):
    """Which renderer burns the captions.

    Three names, three genuinely different renderers, because they are not interchangeable
    on every machine:

    ``ffmpeg``
        The backend's own `filter_complex` render, through the job queue. Needs an ffmpeg
        built with libfreetype (`drawtext`); Homebrew's `ffmpeg` formula deliberately has
        no font stack -- the font libraries live in the separate `ffmpeg-full` formula.
        ``--position`` works here, as a drawtext expression.
    ``mlt``
        The same route with ``backend=mlt``: dispatched to the queue, then the Video
        Editor plugin renders it with MLT. Needs the `default` queue worker AND the plugin.
    ``editor``
        The Video Editor's own compose, which is a SYNCHRONOUS call straight to the plugin
        -- no queue, nothing queued to wait for. This is the path the editor page itself
        uses, and the one that works on a machine without a drawtext-capable ffmpeg.
        ``--x/--y`` only: MLT places text at explicit pixels.
    """

    ffmpeg = "ffmpeg"
    mlt = "mlt"
    editor = "editor"


class CaptionPosition(str, Enum):
    """The backend's named placements (video_timeline_render._POSITION_EXPRS).

    `position` wins over x/y in the ffmpeg backend and is emitted as a drawtext expression,
    so it is correct at any frame size — which is the whole reason it exists here: the CLI
    cannot know the frame size, and the backend's pixel default (320, 240) puts a caption
    left-of-centre, mid-picture, on a 1920x1080 video.
    """

    top_left = "top-left"
    top_center = "top-center"
    top_right = "top-right"
    middle_left = "middle-left"
    center = "center"
    middle_right = "middle-right"
    bottom_left = "bottom-left"
    bottom_center = "bottom-center"
    bottom_right = "bottom-right"

_EDITOR_BASE = "/api/video-editor"
_OVERLAY_BASE = "/api/video-overlay"


@video_editor_app.command("captions-burn")
def ve_captions_burn(
    video_document_id: int = typer.Argument(..., help="Video document id to burn the captions onto"),
    srt: str = typer.Option(None, "--srt", help="Path to an .srt file on the machine running the backend"),
    captions_doc: int = typer.Option(None, "--captions-doc", help="Or the document id of an .srt already registered"),
    font_size: int = typer.Option(None, "--font-size", help="Caption font size (backend default 48 when omitted)"),
    color: str = typer.Option(None, "--color", help="Caption colour, e.g. '#ffffff'"),
    position: CaptionPosition = typer.Option(
        None, "--position",
        help="Where to place the text. Honoured by the ffmpeg backend; the mlt backend "
             "ignores it and places at --x/--y (its own default is the top-left corner).",
    ),
    x: int = typer.Option(None, "--x", help="Raw left pixel, for the backend's default placement"),
    y: int = typer.Option(None, "--y", help="Raw top pixel, for the backend's default placement"),
    audio: int = typer.Option(None, "--audio", help="Audio document id to lay over the video"),
    engine: CaptionEngine = typer.Option(
        CaptionEngine.ffmpeg, "--engine",
        help="Renderer: ffmpeg (needs drawtext), mlt (queue + plugin), or editor "
             "(synchronous plugin call, no queue). See the command's long help.",
    ),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Burn an SRT caption file onto a video.

    GPU work, queued through the backend like any other render. Give exactly one of --srt
    (a path on the backend's machine) or --captions-doc (the id of an .srt Guaardvark
    already holds — `files list` or a previous `captions-export` gives you one).

    The captions are parsed by the backend's own SRT reader, so whatever
    `video-editor captions-export` produced goes straight back on. Place them with
    --position (recommended: it is frame-size independent) or with raw --x/--y pixels.

    `--engine` picks the renderer, and which one works depends on the machine:

    - `ffmpeg` (default) needs an ffmpeg built with libfreetype for `drawtext`. Homebrew's
      regular `ffmpeg` formula has no font stack at all (the fonts live in `ffmpeg-full`),
      so on such a machine this fails inside the queue with `No such filter: 'drawtext'`.
    - `editor` is a synchronous call to the Video Editor plugin: no queue, no drawtext.
      It is the path the editor page uses, and the one to reach for when ffmpeg cannot
      draw text. Placement is `--x/--y` only.
    - `mlt` is the queued variant of that plugin render; it needs the `default` worker too.
    """
    as_json = json_mode(json_out)

    # Usage errors leave the way the rest of this group does (video_editor.py:242, 281, 322):
    # MISSING_INPUT with exit 2, so a script can tell "you called it wrong" from "the server
    # refused". All of these sit before any request is built, so a refused call is silent.
    if bool(srt) == bool(captions_doc):
        detail = "you gave both" if srt else "you gave neither"
        output.print_error(
            f"Give exactly one of --srt (a path on the backend's machine) or "
            f"--captions-doc ({detail}).",
            code="MISSING_INPUT",
        )
        raise typer.Exit(2)
    if engine is CaptionEngine.ffmpeg and position and (x is not None or y is not None):
        output.print_error(
            "Give either --position or --x/--y, not both: on the ffmpeg renderer position "
            "wins, so the pixels would be silently ignored.",
            code="MISSING_INPUT",
        )
        raise typer.Exit(2)
    if position and engine != CaptionEngine.ffmpeg:
        output.print_error(
            f"--position is only honoured by the ffmpeg renderer; --engine {engine.value} "
            "places text at --x/--y (its own default is the top-left corner).",
            code="MISSING_INPUT",
        )
        raise typer.Exit(2)
    if engine == CaptionEngine.editor and position is None and x is None and y is None:
        # The ffmpeg default is mid-picture; MLT's is the top-left corner. Neither is where a
        # caption belongs, and only the caller knows the frame, so say so rather than let a
        # caption land in a corner.
        output.print_error(
            "--engine editor places text at --x/--y, and its default is the top-left "
            "corner, so give --x and --y (for 1920x1080, near the bottom is about "
            "--x 480 --y 980).",
            code="MISSING_INPUT",
        )
        raise typer.Exit(2)
    if font_size is not None and font_size < 1:
        output.print_error(f"--font-size must be at least 1, got {font_size}.", code="MISSING_INPUT")
        raise typer.Exit(2)

    try:
        client = get_client(resolve_server(server))
        import_body = {"path": srt} if srt else {"document_id": captions_doc}
        imported = client.post(f"{_EDITOR_BASE}/captions/import", json=import_body)
        captions = pick_list(imported, "captions")
        if not captions:
            # Defence only: an SRT the backend cannot parse comes back as a 400 with
            # "No captions parsed from the SRT file", so client.post raises first and the
            # user sees that message instead of this one. Kept so a future route that
            # answers an empty list cannot be mistaken for success.
            raise LlxError("That file parsed to no captions.")

        elements = []
        never_visible = 0
        for caption in captions:
            start, end = caption.get("start"), caption.get("end")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end <= start:
                # The backend renders this as enable='between(t,start,end)', which matches no
                # frame — the caption silently never appears. It happens in a queue long after
                # this command has reported success, so say it now.
                never_visible += 1
            element = {
                "text": caption.get("text", ""),
                "startSeconds": start,
                "endSeconds": end,
            }
            # Only send the style keys the caller actually asked for: the backend has its
            # own defaults, and an explicit null would replace them.
            if font_size is not None:
                element["fontSize"] = font_size
            if color:
                element["fontColor"] = color
            if position:
                element["position"] = position.value
            else:
                if x is not None:
                    element["x"] = x
                if y is not None:
                    element["y"] = y
            elements.append(element)

        if engine is CaptionEngine.editor:
            # Straight to the editor's own compose: synchronous, plugin-side, no Celery. The
            # route resolves document_id -> absolute path (it needs a path, and a client
            # cannot build one from the relative path the files API exposes).
            body = {"document_id": video_document_id, "text_elements": elements, "render_mp4": True}
            if audio is not None:
                body["audio_document_id"] = audio
            data = client.post(f"{_EDITOR_BASE}/shotcut/compose", json=body)
        else:
            body = {"video_document_id": video_document_id, "text_elements": elements}
            if audio is not None:
                body["audio_document_id"] = audio
            if engine is CaptionEngine.mlt:
                body["backend"] = "mlt"
            data = client.post(f"{_OVERLAY_BASE}/render-timeline", json=body)
    except LlxError as exc:
        fail(exc)

    payload = pick_dict(data)
    if as_json:
        success(payload)
        return
    if never_visible:
        # stderr, so it cannot corrupt a --json document on stdout.
        output.print_warning(
            f"{never_visible} cue(s) end at or before they start and will never be visible."
        )
    if engine is CaptionEngine.editor:
        # This engine renders inline and names its output directly rather than handing back a
        # tracked job, so there is nothing to poll -- say where the file is.
        rendered = payload.get("rendered_mp4") or payload.get("rendered_path") or ""
        output.print_success(
            f"Burned {len(elements)} caption(s) onto document {video_document_id}"
            f"{': ' + str(rendered) if rendered else '.'}"
        )
        return
    job = payload.get("job_id") or ""
    output.print_success(
        f"Burning {len(elements)} caption(s) onto document {video_document_id}"
        f"{': ' + str(job) if job else '.'} Track it with `video-editor captions-status`."
    )


@video_editor_app.command("captions-status")
def ve_captions_status(
    job_id: str = typer.Argument(..., help="Job id from `video-editor captions-burn`"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Progress of a caption burn, and the new video's document id when it finishes."""
    as_json = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{_OVERLAY_BASE}/render-status/{job_id}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if as_json:
        success(payload)
        return
    output.print_kv(
        {
            "Job": payload.get("job_id", job_id),
            "Status": payload.get("status", ""),
            "Progress": payload.get("progress", ""),
            "Message": payload.get("message", ""),
            "Document": payload.get("document_id") or "(not yet)",
        },
        title="Caption burn",
    )
    if payload.get("document_id"):
        output.print_success(f"Finished: document {payload['document_id']} is the captioned video.")
