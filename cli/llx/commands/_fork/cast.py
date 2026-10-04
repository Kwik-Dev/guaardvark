"""`guaardvark cast` — the Cast Library: characters, environments and props.

The single biggest gap in the CLI (CLI_PLAN 3.2): the fork's Cast work had no terminal
surface at all — you could not even list what you had.

On approvals (CLI_PLAN D2): the *safety* gates — held code, publishes, outreach drafts —
stay read-only or absent, because releasing them is a judgement about code and outbound
content that belongs where the diff is visible. `cast approve` is a different thing: it
picks which of *your own* generated samples become the training set for a character. It
is creative selection, not a security decision, and without it the Cast loop cannot be
driven from the terminal at all. It is the one approval in this package, it is listed in
the read-only contract's allowlist, and it is a sample selection — not a gate.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success, upload_files

COMMAND_NAME = "cast"
app = typer.Typer(help="Cast Library — subjects, samples, LoRAs, training.", no_args_is_help=True)

BASE = "/api/cast-library"


def _subject_rows(subjects: list) -> list[dict]:
    return [
        {
            "id": s.get("id", ""),
            "name": (s.get("name") or "")[:32],
            "kind": s.get("kind", ""),
            "status": s.get("training_status") or s.get("status", ""),
            "lora": "yes" if s.get("lora_path") else "",
        }
        for s in subjects
    ]


@app.command("list")
def cast_list(
    kind: str = typer.Option(None, "--kind", "-k", help="character, environment or prop"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Everything in the Cast Library.

    Two things the routes make easy to get wrong: the collection is `GET
    /api/cast-library` (the `/subjects` path is POST-only, and a GET there is a 405),
    and `?kind=` is accepted but ignored — every kind comes back either way, so the
    filter is applied here rather than passed through and silently doing nothing.
    """
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(BASE)
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "subjects")
    if kind:
        rows = [s for s in rows if (s.get("kind") or "").lower() == kind.lower()]
    if json_out or output.is_pipe():
        success({"subjects": rows})
        return
    output.print_table(_subject_rows(rows), columns=["id", "name", "kind", "status", "lora"],
                       title=f"Cast ({len(rows)})")


@app.command("show")
def cast_show(
    subject_id: int = typer.Argument(..., help="Subject id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One subject in full, including its LoRAs and references."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/subjects/{subject_id}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data, "subject")
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("samples")
def cast_samples(
    subject_id: int = typer.Argument(..., help="Subject id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generated samples, with the ids `cast approve` takes."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/subjects/{subject_id}/samples")
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "samples")
    if json_out or output.is_pipe():
        success({"samples": rows})
        return
    table = [
        {
            "id": s.get("id", ""),
            "approved": s.get("approved", ""),
            "status": s.get("status", ""),
            "seed": s.get("seed", ""),
        }
        for s in rows
    ]
    output.print_table(table, columns=["id", "approved", "status", "seed"],
                       title=f"Samples ({len(table)})")


@app.command("plan")
def cast_plan(
    subject_id: int = typer.Argument(..., help="Subject id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """(Re)plan a subject's reference/sample strategy."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/subjects/{subject_id}/plan")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Planned subject {subject_id}.")


@app.command("generate")
def cast_generate(
    subject_id: int = typer.Argument(..., help="Subject id"),
    count: int = typer.Option(None, "--count", "-n", help="How many samples"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate samples. Queues GPU work through the backend's own gate."""
    json_out = json_mode(json_out)
    body = {"count": count} if count else {}
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/subjects/{subject_id}/generate", json=body)
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Generating samples for subject {subject_id}.")


@app.command("cancel")
def cast_cancel(
    subject_id: int = typer.Argument(..., help="Subject id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Cancel an in-flight sample generation."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/subjects/{subject_id}/generate/cancel")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Cancelled generation for subject {subject_id}.")


@app.command("approve")
def cast_approve(
    subject_id: int = typer.Argument(..., help="Subject id"),
    sample: list[int] = typer.Option(..., "--sample", help="Sample id to approve (repeatable)"),
    unapprove: bool = typer.Option(False, "--unapprove", help="Reverse it instead"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Choose which samples become this subject's training set.

    Creative selection, not a safety gate — see the module docstring. `--sample`
    repeats: `cast approve 4 --sample 11 --sample 12`.
    """
    json_out = json_mode(json_out)
    body = {"sample_ids": sample, "approved": not unapprove}
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/subjects/{subject_id}/samples/approve", json=body)
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    verb = "Un-approved" if unapprove else "Approved"
    output.print_success(f"{verb} {len(sample)} sample(s) for subject {subject_id}.")


@app.command("train")
def cast_train(
    subject_id: int = typer.Argument(..., help="Subject id"),
    backend: str = typer.Option(None, "--backend", help="local or runpod"),
    yes: bool = typer.Option(False, "--yes", help="Confirm: training takes hours and may cost money"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Train a LoRA for this subject. Long and, on a cloud backend, billable."""
    json_out = json_mode(json_out)
    if not yes:
        output.print_error(
            f"Refusing to start training for subject {subject_id} without --yes. "
            "Training runs for hours"
            + (" and a cloud backend bills for the GPU." if backend in (None, "runpod") else "."),
            code="CONFIRMATION_REQUIRED",
        )
        raise typer.Exit(2)
    body = {"training_settings": {"backend": backend}} if backend else {}
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/subjects/{subject_id}/train", json=body)
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Training subject {subject_id}.")


@app.command("train-cancel")
def cast_train_cancel(
    subject_id: int = typer.Argument(..., help="Subject id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Cancel a running training job."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/subjects/{subject_id}/train/cancel")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Cancel requested for subject {subject_id}.")


@app.command("make-default")
def cast_make_default(
    subject_id: int = typer.Argument(..., help="Subject id"),
    base_model: str = typer.Argument(..., help="Base model id the LoRA is for"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Make the current LoRA for a base model the default for that base."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(
            f"{BASE}/subjects/{subject_id}/loras/{base_model}/make-default"
        )
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"{base_model} default set for subject {subject_id}.")


@app.command("delete")
def cast_delete(
    subject_id: int = typer.Argument(..., help="Subject id"),
    yes: bool = typer.Option(False, "--yes", help="Confirm the deletion"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Delete a subject. Destructive: needs --yes."""
    json_out = json_mode(json_out)
    if not yes:
        output.print_error(
            f"Refusing to delete subject {subject_id} without --yes.", code="CONFIRMATION_REQUIRED"
        )
        raise typer.Exit(2)
    try:
        data = get_client(resolve_server(server)).delete(f"{BASE}/subjects/{subject_id}")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Deleted subject {subject_id}.")


@app.command("import-lora")
def cast_import_lora(
    subject_id: int = typer.Argument(..., help="Subject id"),
    file: str = typer.Argument(..., help="Path to the .safetensors file"),
    base_model: str = typer.Option(None, "--base", help="Base model the LoRA targets"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Attach a LoRA you already have to a subject."""
    json_out = json_mode(json_out)
    fields = {"base_model_id": base_model} if base_model else None
    try:
        data = upload_files(
            get_client(resolve_server(server)),
            f"{BASE}/subjects/{subject_id}/import-lora",
            [file],
            field="file",
            fields=fields,
        )
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data) if isinstance(data, dict) else data)
        return
    output.print_success(f"Imported {file} onto subject {subject_id}.")
