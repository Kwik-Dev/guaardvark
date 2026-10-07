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

The module-level `EXTENDS` names the upstream group these commands were added to, so the
REPL catalog (`_fork/registry.py`) offers them to completion and `/help` as well.
"""
from __future__ import annotations

import shlex

import typer

from llx import output
from llx.client import LlxError, get_client
from llx.commands.audio import audio_app

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success, upload_files
from .reproduce import _emit

# The upstream group this module extends, so the REPL catalog can pick up the
# subcommands it adds; see llx/commands/_fork/registry.py.
EXTENDS = {"audio": audio_app}

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


# ---------- reproducibility (issue #8) ---------------------------------------
#
# Every accepted audio generation is recorded in the main DB (see the
# AudioGeneration model), because the sidecar's job history is ephemeral. These
# commands read that record; `reproduce` replays it through the same gate/audit
# as `guaardvark api request --yes`.

_ENDPOINTS = {
    "music": f"{_FOUNDRY_BASE}/generate/music",
    "sfx": f"{_FOUNDRY_BASE}/generate/fx",
    "voice": f"{_FOUNDRY_BASE}/generate/voice",
}

# Transport/lifecycle keys the named audio commands set themselves or the CLI never
# exposed; their presence in a record is not an inexpressible *generation* field.
_TRANSPORT_KEYS = {"async", "queue", "progress_cb", "cancel_event"}


def _audio_named_command(kind: str, body: dict):
    """The `audio music|sfx|tts` line when it can carry the record, else (None, why).

    The upstream named commands are narrow: `audio music` takes style/lyrics/seconds/
    instrumental, `audio sfx` a prompt, `audio tts` text. A record that used a voice id,
    backend, model or seed cannot be rebuilt by them, so those fields are named and the
    generic `api request` line (which keeps them) is used instead.
    """
    if kind == "music":
        parts = ["guaardvark", "audio", "music",
                 shlex.quote(str(body.get("style_prompt") or ""))]
        if body.get("lyrics"):
            parts += ["--lyrics", shlex.quote(str(body["lyrics"]))]
        if body.get("duration_s") is not None:
            parts += ["--seconds", str(body["duration_s"])]
        if body.get("instrumental_only"):
            parts += ["--instrumental"]
        known = {"style_prompt", "lyrics", "duration_s", "instrumental_only"}
    elif kind == "sfx":
        parts = ["guaardvark", "audio", "sfx", shlex.quote(str(body.get("prompt") or ""))]
        known = {"prompt"}
    else:
        parts = ["guaardvark", "audio", "tts", shlex.quote(str(body.get("text") or ""))]
        known = {"text"}
    inexpressible = sorted(set(body) - known - _TRANSPORT_KEYS)
    return (" ".join(parts) if not inexpressible else None), inexpressible


@audio_app.command("jobs")
def audio_jobs(
    generation_id: int = typer.Argument(None, help="Generation id from `audio jobs`"),
    kind: str = typer.Option(None, "--kind", help="Filter: music | sfx | voice"),
    limit: int = typer.Option(50, "--limit", help="Maximum rows to list"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Recorded audio generations (the inputs that produced each song/SFX/voice)."""
    json_out = json_mode(json_out)
    client = get_client(resolve_server(server))
    try:
        if generation_id is not None:
            data = client.get(f"{_FOUNDRY_BASE}/generations/{generation_id}")
            row = pick_dict(data, "generation")
            rows = [row] if row else []
        else:
            params = {"limit": limit}
            if kind:
                params["kind"] = kind
            data = client.get(f"{_FOUNDRY_BASE}/generations", **params)
            rows = pick_list(data, "generations")
    except LlxError as exc:
        fail(exc)

    if json_out or output.is_pipe():
        success({"generations": rows})
        return
    if not rows:
        output.print_warning("No recorded audio generations yet.")
        return
    table = [
        {
            "id": r.get("id"),
            "kind": r.get("kind", ""),
            "status": r.get("status", ""),
            "model": r.get("model") or "",
            "created": (r.get("created_at") or "")[:19],
            "output": (r.get("output_path") or "").rsplit("/", 1)[-1],
        }
        for r in rows if isinstance(r, dict)
    ]
    output.print_table(table, columns=["id", "kind", "status", "model", "created", "output"],
                       title=f"Audio generations ({len(table)})")


@audio_app.command("reproduce")
def audio_reproduce(
    generation_id: int = typer.Argument(..., help="Audio generation id from `audio jobs`"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Emit only; send nothing (the default)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Re-run the recorded request"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Rebuild the request that produced an audio generation."""
    client = get_client(resolve_server(server))
    try:
        data = client.get(f"{_FOUNDRY_BASE}/generations/{generation_id}")
    except LlxError as exc:
        fail(exc)
    row = pick_dict(data, "generation")
    kind = str(row.get("kind") or "")
    body = dict(row.get("inputs") or {})
    if not body or kind not in _ENDPOINTS:
        output.print_error(
            f"audio generation {generation_id} has no recorded inputs to reproduce",
            code="NO_RECORD",
        )
        raise typer.Exit(2)
    named, inexpressible = _audio_named_command(kind, body)
    _emit("audio", str(generation_id), "POST", _ENDPOINTS[kind], body,
          dry_run=dry_run, yes=yes, server=server, json_out=json_out,
          named=named, inexpressible=inexpressible,
          notes=(f"this replays the {kind} request; a voice clone still needs its consent "
                 "record (the gate is never bypassed)",))
