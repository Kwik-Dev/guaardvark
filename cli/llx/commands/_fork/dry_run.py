"""No-spend preview for generation commands (issue #8).

Every generation command should be able to show the request it *would* send, with the
resolved inputs and settings and their provenance, before anything is spent. This module
is that preview, shared by the fork overrides in `dry_run_gen.py` and by the fork-owned
command modules that gained `--dry-run` directly.

Two entry points:

``preview(command, build, ...)``
    Run the real upstream command with its write seams intercepted, and render whatever
    request it tried to send. This is the default, and the reason it is a capture and not a
    second body-builder: two copies drift the first time upstream adds a field, and the
    preview silently starts lying.

``render_request(command, method, path, body, ...)``
    Render an already-built request. For commands whose transport the capture cannot see --
    a multipart upload that posts through `client.http` rather than `LlxClient.upload` --
    the body is built in the command and handed here.

Reads are allowed, writes are not
---------------------------------
Only writes are intercepted. A command may issue a read-only GET before its write to
resolve the active model; blocking it would make the resolved body unknowable. Reads are
free, take no GPU lock and carry no request body, so the preview still sends nothing that
changes state. Say so in the output rather than pretend otherwise.

Provenance
----------
The caller passes the keys it received explicitly on the command line; every other key is
labelled ``command default``. Server-side clamps are not knowable without a send, so the
renderer says so instead of inventing a source.
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
    """Split a request body into inputs and settings, each carrying its source.

    A non-dict body (an upload, or no body at all) has no keys to attribute: return two
    empty maps so the caller renders the upload/body section and never iterates a shape
    that is not key->entry. Getting this wrong crashed the human TTY branch -- the one
    branch the pipe-based tests never reach -- and emitted
    ``{"value": null, "source": "explicit"}`` under ``--json``.
    """
    if not isinstance(body, dict):
        return {}, {}
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
    render_request(command, method, captured.get("path", ""), captured.get("body"),
                   captured.get("upload"), inputs=inputs, explicit=explicit,
                   json_out=json_out, notes=notes)


def render_request(
    command: str,
    method: str,
    path: str,
    body: Any = None,
    upload: Any = None,
    *,
    inputs: Iterable[str] = (),
    explicit: Iterable[str] = (),
    json_out: bool = False,
    notes: Iterable[str] = (),
) -> None:
    """Render an already-built request as a dry run. Sends nothing."""
    as_json = bool(json_out or get_global_json())
    output.set_json_mode(as_json)
    in_map, set_map = _split(body, inputs, explicit)

    if as_json or output.is_pipe():
        output.print_json(
            {
                "status": "dry-run",
                "command": command,
                "method": method,
                "path": path,
                "upload": upload,
                "inputs": in_map,
                "settings": set_map,
                "body": body,
                "notes": list(notes),
            }
        )
        return

    console.print("[llx.warn]DRY RUN[/llx.warn] — nothing sent")
    output.print_kv({"command": command, "request": f"{method} {path}"})
    if upload:
        rows = [{"field": k, "value": _fmt(v)} for k, v in (upload.get("fields") or {}).items()]
        for name, value in (upload.get("files") or []):
            rows.append({"field": name, "value": value})
        if upload.get("file"):
            rows.insert(0, {"field": "file", "value": upload["file"]})
        output.print_table(rows, columns=["field", "value"], title="Upload (multipart)" if not upload.get("file") else "Upload")
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
