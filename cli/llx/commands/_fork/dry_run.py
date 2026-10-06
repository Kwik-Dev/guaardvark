"""No-spend preview for generation commands (issue #8).

Every generation command should be able to show the request it *would* send, with the
resolved inputs and settings and their provenance, before anything is spent. This module
is that preview, shared by the fork overrides in ``dry_run_gen.py``.

Why a capture and not a second body-builder
-------------------------------------------
A dry run has to show the request the command *actually* builds. Copying each command's
body-building into a preview branch means two copies that drift the first time upstream
adds a field, and the preview silently starts lying. So the preview runs the real upstream
function with its two outbound write seams -- ``LlxClient._request`` for POST/PUT/PATCH/
DELETE and ``LlxClient.upload`` / ``upload_with_progress`` for multipart -- intercepted for
the duration of the call. The first write is recorded and aborts the command by raising
``_Captured`` (a ``BaseException``, so no ``except Exception`` inside the command can
swallow it). Nothing is sent.

Reads are allowed, writes are not
---------------------------------
Only writes are intercepted. A command may issue a read-only GET before its write to
resolve the active model (``videos generate`` does exactly that); blocking it would make
the resolved body unknowable. Reads are free, take no GPU lock and carry no request body,
so the preview still sends nothing that changes state. Say so in the output rather than
pretend otherwise.

Provenance
----------
The caller passes the keys it received explicitly on the command line; every other key is
labelled ``command default``. Server-side clamps are not knowable without a send, so the
renderer says so instead of inventing a source. Callers that can name a model's declared
floor may pass it in ``notes``.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterable

import typer

from llx import output
from llx.global_opts import get_global_json
from llx.theme import make_console

console = make_console()

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class _Captured(BaseException):
    """Raised in place of an outbound write, to abort the command before it sends."""


@contextmanager
def capture_writes():
    """Intercept the first outbound write from an upstream command; sends nothing.

    Yields a dict that is filled in (``method``/``path``/``body``/``upload``) before the
    ``_Captured`` is raised, so the caller reads it after catching.
    """
    import llx.client as client_mod

    cls = client_mod.LlxClient
    saved = {
        name: getattr(cls, name)
        for name in ("_request", "upload", "upload_with_progress")
        if hasattr(cls, name)
    }
    captured: dict = {}

    def grab(method: str, path: str, body: Any = None, upload: Any = None):
        captured.update(method=method.upper(), path=path, body=body, upload=upload)
        raise _Captured()

    def fake_request(self, method, path, **kw):
        if method.upper() in _READ_METHODS:
            return saved["_request"](self, method, path, **kw)
        return grab(method, path, kw.get("json"))

    def fake_upload(self, path, file_path, **extra):
        return grab("POST", path, None, {"file": str(file_path), "fields": extra})

    def fake_upload_progress(self, path, file_path, **extra):
        return grab("POST", path, None, {"file": str(file_path), "fields": extra})

    cls._request = fake_request
    if "upload" in saved:
        cls.upload = fake_upload
    if "upload_with_progress" in saved:
        cls.upload_with_progress = fake_upload_progress
    try:
        yield captured
    finally:
        for name, fn in saved.items():
            setattr(cls, name, fn)


def _entry(value: Any, source: str) -> dict:
    return {"value": value, "source": source}


def _split(body: Any, inputs: Iterable[str], explicit: Iterable[str]):
    """Split a request body into inputs and settings, each carrying its source."""
    if not isinstance(body, dict):
        return {}, _entry(body, "explicit")
    in_keys, ex_keys = set(inputs), set(explicit)
    in_map, set_map = {}, {}
    for key, value in body.items():
        source = "explicit" if key in ex_keys else "command default"
        (in_map if key in in_keys else set_map)[key] = _entry(value, source)
    return in_map, set_map


def preview(
    command: str,
    build: Callable[[], Any],
    *,
    inputs: Iterable[str] = (),
    explicit: Iterable[str] = (),
    json_out: bool = False,
    notes: Iterable[str] = (),
) -> None:
    """Run ``build`` with writes intercepted and print what it would have sent.

    ``build`` is the upstream command called with the user's arguments. It may raise
    ``_Captured`` (caught here) or a genuine error (propagated, already printed upstream).
    """
    as_json = bool(json_out or get_global_json())
    output.set_json_mode(as_json)
    with capture_writes() as captured:
        try:
            build()
        except _Captured:
            pass
    method = captured.get("method")
    if not method:
        output.print_error(
            "the command stopped before it built a request -- see the message above; "
            "nothing was sent",
            code="DRY_RUN_NO_REQUEST",
        )
        raise typer.Exit(2)

    # The upstream function ran under capture and set json mode from the True we passed it
    # (so it would have rendered its own result). Re-assert our verdict for the preview.
    output.set_json_mode(as_json)

    body = captured.get("body")
    upload = captured.get("upload")
    in_map, set_map = _split(body, inputs, explicit)

    if as_json or output.is_pipe():
        output.print_json(
            {
                "status": "dry-run",
                "command": command,
                "method": method,
                "path": captured.get("path", ""),
                "upload": upload,
                "inputs": in_map,
                "settings": set_map,
                "body": body,
                "notes": list(notes),
            }
        )
        return

    console.print(f"[llx.warn]DRY RUN[/llx.warn] — nothing sent")
    output.print_kv(
        {"command": command, "request": f"{method} {captured.get('path', '')}"}
    )
    if upload:
        rows = [{"field": "file", "value": upload["file"]}]
        rows += [{"field": k, "value": v} for k, v in (upload.get("fields") or {}).items()]
        output.print_table(rows, columns=["field", "value"], title="Upload (multipart)")
    if in_map:
        output.print_table(
            [{"input": k, "value": _fmt(v["value"]), "source": v["source"]} for k, v in in_map.items()],
            columns=["input", "value", "source"],
            title="Inputs",
        )
    if set_map:
        output.print_table(
            [{"setting": k, "value": _fmt(v["value"]), "source": v["source"]} for k, v in set_map.items()],
            columns=["setting", "value", "source"],
            title="Settings",
        )
    if body is not None:
        output.print_panel("Request body", _pretty(body))
    for note in notes:
        console.print(f"[llx.dim]{note}[/llx.dim]")


def _fmt(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "…"


def _pretty(body: Any) -> str:
    import json

    return json.dumps(body, indent=2, default=str)
