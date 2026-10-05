"""`guaardvark api` -- generic backend REST access, the escape hatch (CLI_PLAN D5).

Every other command in this package is a curated wrapper: a name, typed arguments, a
formatted result. That is the right default, and it is also why coverage is always N of
97 -- a route nobody wrapped is unreachable from the terminal. This group is the
opposite trade: one command, every route, no argument types, raw output, guarded.

    guaardvark api routes --search storyboard
    guaardvark api request GET  /api/routes
    guaardvark api request GET  /api/production?limit=5
    guaardvark api request POST /api/production/3/storyboard/approve --yes
    guaardvark api audit --decisions

Reads are free. Writes need `--yes`. A decision-class route needs `--yes` even when it
is a read, and every attempt is appended to `<GUAARDVARK_DIR>/api-audit.jsonl`. The list
of decision classes, and the reasoning, live in `_api_guard.py` -- not here, so that a
future edit to this file cannot widen the guard by accident.

Nothing in this module names a decision route, which is what keeps the static scan in
`test_fork_readonly_contract.py` passing unchanged: the example in this docstring is a
docstring, and the guard owns every literal.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError, get_client
from llx.commands._fork import _api_guard as guard
from llx.commands._fork._common import fail, json_mode, pick_list, resolve_server, unwrap
from llx.theme import make_console

console = make_console()

COMMAND_NAME = "api"
app = typer.Typer(
    help="Direct backend REST access: reads free, writes and decisions need --yes, all audited.",
    no_args_is_help=True,
)


def _parse_body(data: str | None, data_file: str | None) -> Any:
    """The JSON body, from `--data`, `--data-file` or the shell's `@path` convention."""
    if data and data_file:
        raise LlxError("use either --data or --data-file, not both")
    raw: str | None = None
    if data_file:
        source = Path(data_file).expanduser()
        if not source.is_file():
            raise LlxError(f"No such payload file: {data_file}")
        raw = source.read_text(encoding="utf-8")
    elif data:
        raw = data
    if raw is None or not raw.strip():
        return None
    if raw.lstrip().startswith("@"):
        source = Path(raw.strip()[1:]).expanduser()
        if not source.is_file():
            raise LlxError(f"No such payload file: {source}")
        raw = source.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LlxError(f"payload is not valid JSON: {exc}")
    if not isinstance(parsed, (dict, list)):
        raise LlxError(
            f"payload must be a JSON object or array, found {type(parsed).__name__}"
        )
    return parsed


def _render(data: Any) -> None:
    """Print what came back, without inventing a shape for it."""
    payload = unwrap(data)
    if isinstance(payload, list):
        _render_rows(payload)
        return
    if isinstance(payload, dict):
        # Rich-printed JSON rather than a key/value panel: on an unwrapped route the CLI
        # has no idea which keys matter, and guessing hides the ones that do.
        output.print_json(payload)
        return
    console.print(str(payload))


def _render_rows(rows: list) -> None:
    if not rows:
        console.print("[llx.dim]No results.[/llx.dim]")
        return
    if all(isinstance(row, dict) for row in rows):
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        output.print_table(rows, columns=columns[:8], title=f"{len(rows)} result(s)")
        return
    for row in rows:
        console.print(str(row))


def _warn_if_audit_missing(path: Path | None) -> None:
    if path is None:
        output.print_warning(
            f"could not write the audit log at {guard.audit_path()}; the request itself "
            "was not affected"
        )


@app.command("request")
def api_request(
    method: str = typer.Argument(
        ..., help="HTTP method: GET, HEAD, OPTIONS, POST, PUT, PATCH, DELETE"
    ),
    path: str = typer.Argument(
        ..., help="Backend path starting with /api/ (a query string is allowed)"
    ),
    data: str | None = typer.Option(None, "--data", "-d", help="JSON body, or @path/to/file.json"),
    data_file: str | None = typer.Option(None, "--data-file", help="Read the JSON body from a file"),
    query: list[str] = typer.Option(None, "--query", "-q", help="Query parameter key=value (repeatable)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Required before a write or a decision route is sent"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the verdict and the request; send nothing"),
    server: str | None = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Call any backend route and print what comes back, unchanged.

    Reads are free. A write (POST, PUT, PATCH, DELETE) needs `--yes`. A route that is a
    decision a person normally makes in the Studio needs `--yes` even as a read, so
    nothing irreversible happens because a path was guessed. Every attempt is appended
    to the audit log; `guaardvark api audit` reads it back.

    The output is exactly the backend's response -- no columns, no summary, no
    interpretation. `--dry-run` shows the request and the verdict without sending
    anything, and needs no `--yes`. When nothing wraps a route yet, this is the way to
    reach it. Prefer a named command where one exists: it validates its arguments and
    prints something a person can read. Once you call one route here repeatedly, that is
    the signal to promote it to a named command.
    """
    as_json = json_mode(json_out)
    try:
        verdict = guard.classify(method, path)
        target = guard.with_query(verdict.path, query)
        if (data or data_file) and verdict.method not in guard.WRITE_METHODS:
            raise LlxError(f"{verdict.method} takes no body; drop --data / --data-file")
        body = _parse_body(data, data_file) if verdict.method in guard.WRITE_METHODS else None
    except LlxError as exc:
        fail(exc)

    where = resolve_server(server) or ""
    base = where or "the configured server"

    if dry_run:
        # Dry run sits *before* the gate on purpose: seeing what would be sent is how a
        # person decides whether to add `--yes`, so previewing must not itself require it.
        # `approved` records whether `--yes` was actually given.
        record_path = guard.record(verdict, server=base, outcome="dry-run", body=body, approved=yes)
        _warn_if_audit_missing(record_path)
        if verdict.needs_yes:
            gate = "--yes given" if yes else "--yes required; this would be refused"
        else:
            gate = "none (a read)"
        preview = {
            "status": "dry-run",
            "method": verdict.method,
            "url": f"{base}{target}",
            "kind": verdict.kind,
            "needs_yes": verdict.needs_yes,
            "gate": gate,
            "body": body,
        }
        if as_json or output.is_pipe():
            output.print_json(preview)
            return
        output.print_kv(
            {
                "Method": verdict.method,
                "URL": f"{base}{target}",
                "Kind": verdict.kind,
                "Gate": gate,
            },
            title="Dry run -- nothing was sent",
        )
        return

    if verdict.needs_yes and not yes:
        # Audit the refusal too: "someone aimed an approval at this box without --yes" is
        # exactly the fact worth having later.
        guard.record(verdict, server=base, outcome="refused", error="needs --yes")
        if as_json or output.is_pipe():
            output.print_json(
                {
                    "status": "refused",
                    "method": verdict.method,
                    "path": target,
                    "kind": verdict.kind,
                    "reason": "needs --yes",
                }
            )
        else:
            output.print_warning(
                f"{verdict.method} {target} is {verdict.why} -- re-run with --yes to send it."
            )
        raise typer.Exit(2)

    client = get_client(where or None)
    started = time.monotonic()
    try:
        data_out = client._request(verdict.method, target, json=body)
    except LlxConnectionError as exc:
        guard.record(verdict, server=base, outcome="error", error=str(exc), body=body)
        fail(exc)
    except LlxError as exc:
        guard.record(
            verdict,
            server=base,
            outcome="error",
            status=exc.status_code,
            error=exc.message,
            body=body,
            duration_ms=int((time.monotonic() - started) * 1000),
            approved=yes,
        )
        if not as_json and verdict.is_decision and exc.status_code == 409:
            console.print(
                "[llx.dim]A 409 on a decision route usually means the row is not at the "
                "stage that decision requires. Check it with the named command first.[/llx.dim]"
            )
        fail(exc)

    record_path = guard.record(
        verdict,
        server=base,
        outcome="ok",
        body=body,
        duration_ms=int((time.monotonic() - started) * 1000),
        approved=yes,
    )
    _warn_if_audit_missing(record_path)

    if as_json or output.is_pipe():
        output.print_json(data_out)
        return
    _render(data_out)


@app.command("routes")
def api_routes(
    search: str | None = typer.Option(None, "--search", "-q", help="Only routes containing this text"),
    method: str | None = typer.Option(None, "--method", "-m", help="Only routes accepting this method"),
    docs: bool = typer.Option(False, "--docs", help="Include each view's docstring (slower)"),
    server: str | None = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List every route the backend serves -- the map for `api request`.

    Reads `GET /api/routes` (or `GET /api/api-docs` with `--docs`). This is discovery, not
    a promise: a route appearing here says it exists, not that this CLI wraps it well.
    """
    as_json = json_mode(json_out)
    try:
        client = get_client(resolve_server(server))
        data = client.get("/api/api-docs" if docs else "/api/routes")
    except (LlxConnectionError, LlxError) as exc:
        fail(exc)

    rows = pick_list(data, "routes", "docs")
    if search:
        needle = search.lower()
        rows = [r for r in rows if isinstance(r, dict) and needle in str(r.get("rule", "")).lower()]
    if method:
        wanted = method.strip().upper()
        rows = [
            r
            for r in rows
            if isinstance(r, dict)
            and wanted in {m.strip().upper() for m in str(r.get("methods", "")).split(",")}
        ]

    if as_json or output.is_pipe():
        output.print_json(rows)
        return
    if not rows:
        console.print("[llx.dim]No routes matched.[/llx.dim]")
        return
    columns = ["rule", "methods", "doc"] if docs else ["rule", "methods"]
    output.print_table(rows, columns=columns, title=f"Backend routes ({len(rows)})")


@app.command("audit")
def api_audit(
    limit: int = typer.Option(20, "--limit", "-n", help="How many recent entries to show"),
    decisions_only: bool = typer.Option(False, "--decisions", help="Only decision-class calls"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show what this CLI actually sent, refused or failed (the tier-B audit log).

    Reads `<GUAARDVARK_DIR>/api-audit.jsonl`, newest entries last. A missing log is not
    an error -- it means nothing has used the escape hatch yet.
    """
    as_json = json_mode(json_out)
    entries = guard.read_audit(limit, decisions_only=decisions_only)
    if as_json or output.is_pipe():
        output.print_json(entries)
        return
    if not entries:
        console.print(f"[llx.dim]No audit entries at {guard.audit_path()}[/llx.dim]")
        return
    rows = [
        {
            "ts": e.get("ts", ""),
            "method": e.get("method", ""),
            "path": e.get("path", ""),
            "kind": e.get("kind", ""),
            "outcome": e.get("outcome", ""),
            "status": e.get("status") if e.get("status") is not None else "",
        }
        for e in entries
    ]
    output.print_table(
        rows,
        columns=["ts", "method", "path", "kind", "outcome", "status"],
        title=f"api audit ({len(rows)})",
    )
