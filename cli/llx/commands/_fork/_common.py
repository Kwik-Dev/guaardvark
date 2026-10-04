"""Helpers shared by the fork-owned command modules.

Not a command module: it has no `COMMAND_NAME`, and the registry skips names starting
with `_`, so nothing here is mounted.

These backend endpoints do not agree on an envelope. `success_response(...)` wraps
its payload in `{"success": true, "data": ...}`, while several blueprints `jsonify` a
bare dict. Rather than parse each handler's shape by hand (and break when one changes),
the extractors below accept both and degrade to an empty result instead of raising.
Read-only inspection should be tolerant; the command still prints exactly what it got
under `--json`.
"""
from __future__ import annotations

from typing import Any, NoReturn

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError
from llx.global_opts import get_global_json, get_global_server

_LIST_KEYS = ("items", "results", "rows", "records", "data")


def resolve_server(server: str | None) -> str | None:
    """The `--server` option if given, else the global one."""
    return server or get_global_server()


def json_mode(json_out: bool) -> bool:
    """Fold in the global `--json`, switch the output module, and return the verdict."""
    enabled = bool(json_out or get_global_json())
    output.set_json_mode(enabled)
    return enabled


def fail(exc: Exception) -> NoReturn:
    """Map a client error to the CLI's error output and exit 1."""
    if isinstance(exc, LlxConnectionError):
        output.print_error(str(exc), code="CONNECTION_ERROR")
    else:
        output.print_error(str(exc), code="API_ERROR")
    raise typer.Exit(1)


def success(data: Any) -> None:
    """The `--json` envelope every command in this package emits."""
    output.print_json({"status": "success", "data": data})


def unwrap(data: Any) -> Any:
    """Drop a `{"success"|"status": ..., "data": ...}` envelope if one is present."""
    if (
        isinstance(data, dict)
        and isinstance(data.get("data"), (dict, list))
        and ("success" in data or "status" in data)
    ):
        return data["data"]
    return data


def pick_list(data: Any, *keys: str) -> list:
    """Best-effort list from any of the shapes these endpoints return."""
    inner = unwrap(data)
    if isinstance(inner, list):
        return inner
    if isinstance(inner, dict):
        for key in (*keys, *_LIST_KEYS):
            value = inner.get(key)
            if isinstance(value, list):
                return value
    return []


def pick_dict(data: Any, *keys: str) -> dict:
    """Best-effort dict: the named key's value, else the unwrapped payload itself."""
    inner = unwrap(data)
    if isinstance(inner, dict):
        for key in keys:
            value = inner.get(key)
            if isinstance(value, dict):
                return value
        return inner
    return {}


def upload_files(client, path: str, files: list, *, field: str = "files",
                 fields: dict | None = None) -> Any:
    """Multipart upload whose field name repeats, as these routes expect.

    `LlxClient.upload` hardcodes the part name "file". The upscaling batch route reads
    `request.files.getlist("files")` and the voice route reads `request.files["audio"]`,
    so neither can use it. Posting here reuses the client's own error mapping, so a 4xx
    still arrives as an `LlxError` and lands in `fail()` like everything else.
    """
    from pathlib import Path

    handles = []
    parts = []
    try:
        for raw in files:
            file_path = Path(raw)
            if not file_path.is_file():
                raise LlxError(f"No such file: {raw}")
            handle = open(file_path, "rb")
            handles.append(handle)
            parts.append((field, (file_path.name, handle)))
        data = {k: str(v) for k, v in (fields or {}).items() if v is not None}
        response = client.http.post(path, files=parts, data=data)
    finally:
        for handle in handles:
            handle.close()
    # `_handle_response` is this package's own error mapping; reusing it keeps the
    # error shape identical to every other command rather than inventing a second one.
    return client._handle_response(response)
