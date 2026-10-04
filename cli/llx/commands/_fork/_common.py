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
