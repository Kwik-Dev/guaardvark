"""`guaardvark training` — LoRA training *datasets*.

Deliberately narrower than CLI_PLAN 3.5 first sketched. Launching a training run already
lives in `cast train` (Phase 2), which is where the money is spent and where the --yes
gate sits. Giving `training` a second `start` would mean two ways to spend hours of GPU
and two places to keep the confirmation honest, so this group owns what `cast` does not:
the reusable datasets a run is built from, and which trainers are available.

Training a subject: `guaardvark cast train <subject> --yes`.
"""
from __future__ import annotations

import typer
from typer.core import TyperGroup

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "training"

# Names a reader might expect here, because CLI_PLAN 3.5 first sketched
# `training start <subject>`. The run lives in `cast train` (one launcher, one --yes),
# so these answer with that instead of a bare "No such command".
_LAUNCHER_NAMES = {"start", "train", "launch", "run"}


class _TrainingGroup(TyperGroup):
    """A `training` group that names the real launcher for the sketched one."""

    def resolve_command(self, ctx, args):
        # `not ctx.resilient_parsing` mirrors Click's own guard: shell completion calls
        # this with resilient_parsing=True and must not raise (a traceback on <TAB>).
        if (
            args
            and args[0] in _LAUNCHER_NAMES
            and self.get_command(ctx, args[0]) is None
            and not ctx.resilient_parsing
        ):
            # ctx.fail raises the Click Typer itself uses (Typer vendors Click as
            # typer._click, so excepting click.exceptions.UsageError would not catch it).
            ctx.fail(
                f"No such command {args[0]!r}. Subject training is "
                "`guaardvark cast train <subject> --yes`; "
                "`guaardvark training` only manages datasets."
            )
        return super().resolve_command(ctx, args)


app = typer.Typer(
    cls=_TrainingGroup,
    help="Training datasets (subject training lives in `cast train`).",
    no_args_is_help=True,
)

BASE = "/api/training_datasets"


def _row(dataset: dict) -> dict:
    return {
        "id": dataset.get("id", ""),
        "name": (dataset.get("name") or "")[:36],
        "path": (dataset.get("path") or "")[-40:],
        "created": str(dataset.get("created_at", ""))[:19],
    }


@app.command("datasets")
def training_datasets(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Reusable training datasets."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(BASE)
    except LlxError as exc:
        fail(exc)
    rows = pick_list(data, "datasets")
    if json_out or output.is_pipe():
        success({"datasets": rows})
        return
    output.print_table([_row(d) for d in rows], columns=["id", "name", "path", "created"],
                       title=f"Training datasets ({len(rows)})")


@app.command("dataset")
def training_dataset(
    dataset_id: int = typer.Argument(..., help="Dataset id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """One dataset."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/{dataset_id}")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data, "dataset")
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("dataset-new")
def training_dataset_new(
    name: str = typer.Argument(..., help="Dataset name (unique)"),
    path: str = typer.Option(None, "--path", help="Where the images live on the server"),
    description: str = typer.Option(None, "--desc", "-d"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a dataset."""
    json_out = json_mode(json_out)
    body = {"name": name, "path": path, "description": description}
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/", json=body)
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data) if isinstance(data, dict) else data)
        return
    output.print_success(f"Created dataset {name!r}.")


@app.command("dataset-update")
def training_dataset_update(
    dataset_id: int = typer.Argument(..., help="Dataset id"),
    name: str = typer.Option(None, "--name"),
    path: str = typer.Option(None, "--path"),
    description: str = typer.Option(None, "--desc", "-d"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Rename or re-point a dataset."""
    json_out = json_mode(json_out)
    body = {k: v for k, v in (("name", name), ("path", path), ("description", description)) if v is not None}
    if not body:
        output.print_error("Nothing to update: pass --name, --path or --desc.", code="MISSING_INPUT")
        raise typer.Exit(2)
    try:
        data = get_client(resolve_server(server)).put(f"{BASE}/{dataset_id}", json=body)
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data) if isinstance(data, dict) else data)
        return
    output.print_success(f"Updated dataset {dataset_id}.")


@app.command("dataset-delete")
def training_dataset_delete(
    dataset_id: int = typer.Argument(..., help="Dataset id"),
    yes: bool = typer.Option(False, "--yes", help="Confirm the deletion"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Delete a dataset row. Destructive: needs --yes."""
    json_out = json_mode(json_out)
    if not yes:
        output.print_error(
            f"Refusing to delete dataset {dataset_id} without --yes.", code="CONFIRMATION_REQUIRED"
        )
        raise typer.Exit(2)
    try:
        data = get_client(resolve_server(server)).delete(f"{BASE}/{dataset_id}")
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data) if isinstance(data, dict) else data)
        return
    output.print_success(f"Deleted dataset {dataset_id}.")


@app.command("backends")
def training_backends(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Which LoRA trainers are installed. The run itself is `cast train`."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get("/api/plugins")
    except LlxError as exc:
        fail(exc)
    rows = [
        p for p in pick_list(data, "plugins")
        if isinstance(p, dict) and "lora" in str(p.get("id", "")).lower() + str(p.get("name", "")).lower()
    ]
    if json_out or output.is_pipe():
        success({"backends": rows})
        return
    table = [
        {
            "backend": p.get("id", ""),
            "status": p.get("status", ""),
            "enabled": p.get("enabled", ""),
        }
        for p in rows
    ]
    output.print_table(table, columns=["backend", "status", "enabled"],
                       title=f"LoRA trainers ({len(table)})")
    if not table:
        output.print_warning("No LoRA trainer plugin found; `cast train` will report what it needs.")
