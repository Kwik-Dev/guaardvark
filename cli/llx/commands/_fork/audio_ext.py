"""Adds `guaardvark audio transcribe` — speech-to-text, which the CLI never had.

This module is an *extension* of an upstream command group, and it is worth knowing how
it works, because it is the reason no upstream file had to be edited.

`llx/commands/audio.py` is upstream-owned. A second Typer app named "audio" cannot be
mounted (Click would silently drop one, which the registry now reports), and editing the
file would be another conflict on every sync. Instead this module has **no**
`COMMAND_NAME` and **no** `app`, so the registry imports it and mounts nothing: importing
it registers one more command on the upstream `audio_app` object. Adding a command to an
existing group therefore costs one new file and zero upstream lines.

Ordering matters and is safe: `main.py` imports every upstream command module at the top
(before the fork loop runs), so `audio_app` exists by the time this is imported. The
command appears in `guaardvark --help` under `audio`.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client
from llx.commands.audio import audio_app

from ._common import fail, json_mode, resolve_server, success, upload_files

_BASE = "/api/voice"


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
            f"{_BASE}/speech-to-text",
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
