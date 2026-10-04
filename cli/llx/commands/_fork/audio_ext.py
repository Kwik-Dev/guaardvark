"""Adds `guaardvark audio transcribe` and `audio models` — two things the CLI never had.

This module is an *extension* of an upstream command group, and it is worth knowing how
it works, because it is the reason no upstream file had to be edited.

`llx/commands/audio.py` is upstream-owned. A second Typer app named "audio" cannot be
mounted (Click would silently drop one, which the registry now reports), and editing the
file would be another conflict on every sync. Instead this module has **no**
`COMMAND_NAME` and **no** `app`, so the registry imports it and mounts nothing: importing
it registers extra commands on the upstream `audio_app` object. Adding to an existing
group therefore costs one new file and zero upstream lines.

Ordering matters and is safe: `main.py` imports every upstream command module at the top
(before the fork loop runs), so `audio_app` exists by the time this is imported. The
commands appear in `guaardvark --help` under `audio`.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client
from llx.commands.audio import audio_app

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success, upload_files

_VOICE_BASE = "/api/voice"
_FOUNDRY_BASE = "/api/audio-foundry"


@audio_app.command("transcribe")
def audio_transcribe(
    file: str = typer.Argument(..., help="Audio file to transcribe"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Speech to text, locally (whisper.cpp).

    The backend route reads the part named "audio", which the shared client's `upload`
    does not send — hence the multipart helper.
    """
    json_out = json_mode(json_out)
    try:
        data = upload_files(
            get_client(resolve_server(server)),
            f"{_VOICE_BASE}/speech-to-text",
            [file],
            field="audio",
        )
    except LlxError as exc:
        fail(exc)

    if not isinstance(data, dict):
        data = {}
    text = data.get("text") or data.get("transcribed_text") or ""
    if json_out or output.is_pipe():
        success({"file": file, "text": text})
        return
    if not text:
        output.print_warning("No speech detected.")
        return
    output.print_markdown(text)


@audio_app.command("models")
def audio_models(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Audio Foundry models (music, SFX, TTS) and whether they are installed.

    Weight downloads for *image* and *video* models have no backend route at all —
    `/api/model` exposes list/loaded/unload/status/resources and no download. These do,
    so they are reachable here.
    """
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{_FOUNDRY_BASE}/models")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "models")
    if json_out or output.is_pipe():
        success({"models": rows})
        return
    table = [
        {
            "model": (m.get("id") or m.get("name") or str(m))[:38],
            "installed": m.get("installed", ""),
        }
        for m in rows if isinstance(m, dict)
    ]
    output.print_table(table, columns=["model", "installed"], title=f"Audio Foundry models ({len(table)})")


@audio_app.command("model-download")
def audio_model_download(
    model: str = typer.Argument(..., help="Model id to fetch"),
    yes: bool = typer.Option(False, "--yes", help="Confirm: these weights are gigabytes"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Download an Audio Foundry model's weights (GBs; ACE-Step is ~10 GB)."""
    json_out = json_mode(json_out)
    if not yes:
        output.print_error(
            f"Downloading {model} pulls gigabytes of weights. Re-run with --yes.",
            code="CONFIRMATION_REQUIRED",
        )
        raise typer.Exit(2)
    try:
        data = get_client(resolve_server(server)).post(f"{_FOUNDRY_BASE}/models/download", json={"model": model})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Downloading {model}.")
