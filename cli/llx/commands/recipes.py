"""Offline commands for inspecting and validating agent recipes."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import typer

from llx import output
from llx.commands.system import _find_project_root
from llx.global_opts import get_global_json


recipes_app = typer.Typer(
    help="List, inspect, and validate agent recipes without starting the backend.",
    no_args_is_help=True,
)

KNOWN_ACTIONS = frozenset(
    {"hotkey", "type", "click", "wait_until_settled", "wait_until_visible", "wait"}
)


def _default_recipes_path() -> Path:
    start = os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
    return Path(_find_project_root(start)) / "data" / "agent" / "recipes.json"


def _resolve_recipes_path(file: Path | None) -> Path:
    """Resolve an explicit --file, or fall back to data/agent/recipes.json."""
    return file.expanduser().resolve() if file else _default_recipes_path()


def load_recipes(path: Path) -> dict[str, Any]:
    """Load a recipe document, raising a readable ValueError on malformed input."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Recipe file not found: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Could not read recipe file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError("Recipe document must be a JSON object.")
    return payload


def recipe_entries(payload: dict[str, Any]) -> dict[str, Any]:
    """Return actual recipes while excluding reserved document metadata."""
    return {name: recipe for name, recipe in payload.items() if not name.startswith("_")}


def _backend_validator() -> Any:
    """Load the canonical backend validator, or None when unavailable.

    The module is loaded straight from its file on purpose: importing the
    ``backend`` package pulls heavy optional runtime deps (socketio/numpy) that
    the offline CLI must not require. A standalone CLI install without the repo
    layout gets None and falls back to the local checks below.
    """
    try:
        start = os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
        root = Path(_find_project_root(start))
        module_path = root / "backend" / "services" / "agent_knowledge_validator.py"
        if not module_path.is_file():
            return None
        spec = importlib.util.spec_from_file_location(
            "guaardvark_backend_agent_knowledge_validator", module_path
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def validate_recipes(payload: dict[str, Any], strict: bool = False) -> list[str]:
    """Return all schema errors found in a recipe document.

    Prefer the canonical backend validator so the CLI and backend cannot
    drift apart; fall back to the local schema checks when the repo layout is
    unavailable (standalone CLI install).
    """
    backend = _backend_validator()
    if backend is not None:
        return backend.validate_recipe_library(payload, strict=strict).error_messages()
    return _validate_recipes_locally(payload, strict=strict)


def _validate_recipes_locally(payload: dict[str, Any], strict: bool = False) -> list[str]:
    """Offline fallback: schema checks without the backend validator.

    ``strict`` is accepted for signature parity with the backend validator; the
    lightweight fallback has no migration warnings to promote, so it is unused.
    """
    errors: list[str] = []
    recipes = recipe_entries(payload)
    if not recipes:
        return ["Document contains no recipes."]

    for name, recipe in recipes.items():
        prefix = f"Recipe '{name}'"
        if not isinstance(recipe, dict):
            errors.append(f"{prefix} must be an object.")
            continue

        description = recipe.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"{prefix} must have a non-empty string 'description'.")

        triggers = recipe.get("triggers")
        if not (
            isinstance(triggers, list)
            and triggers
            and all(isinstance(trigger, str) and trigger.strip() for trigger in triggers)
        ):
            errors.append(f"{prefix} must have a non-empty string list 'triggers'.")

        steps = recipe.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{prefix} must have a non-empty list 'steps'.")
            continue

        for index, step in enumerate(steps, start=1):
            step_prefix = f"{prefix}, step {index}"
            if not isinstance(step, dict):
                errors.append(f"{step_prefix} must be an object.")
                continue
            action = step.get("action")
            if not isinstance(action, str) or not action.strip():
                errors.append(f"{step_prefix} must have a non-empty string 'action'.")
            elif action not in KNOWN_ACTIONS:
                errors.append(
                    f"{step_prefix} uses unknown action '{action}'. "
                    f"Known actions: {', '.join(sorted(KNOWN_ACTIONS))}."
                )

    return errors


def _load_or_exit(path: Path) -> dict[str, Any]:
    try:
        return load_recipes(path)
    except ValueError as exc:
        output.print_error(str(exc), code="INVALID_RECIPE_FILE")
        raise typer.Exit(1) from exc


@recipes_app.command("list")
def list_recipes(
    file: Path | None = typer.Option(
        None, "--file", "-f", help="Recipe file (defaults to data/agent/recipes.json)."
    ),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output structured JSON."),
):
    """List recipe names and one-line descriptions."""
    recipes = recipe_entries(_load_or_exit(_resolve_recipes_path(file)))
    rows = [
        {"name": name, "description": recipe.get("description", "")}
        for name, recipe in recipes.items()
        if isinstance(recipe, dict)
    ]

    if json_out or get_global_json():
        output.print_json({"status": "success", "data": {"recipes": rows}})
    else:
        output.print_table(rows, columns=["name", "description"], title="Agent Recipes")


@recipes_app.command("show")
def show_recipe(
    name: str = typer.Argument(..., help="Recipe key to inspect."),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="Recipe file (defaults to data/agent/recipes.json)."
    ),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output structured JSON."),
):
    """Show a recipe's triggers, steps, and success proof."""
    recipes = recipe_entries(_load_or_exit(_resolve_recipes_path(file)))
    recipe = recipes.get(name)
    if not isinstance(recipe, dict):
        output.print_error(f"Unknown recipe: {name}", code="RECIPE_NOT_FOUND")
        raise typer.Exit(1)

    details = {
        "name": name,
        "description": recipe.get("description", ""),
        "triggers": recipe.get("triggers", []),
        "preconditions": recipe.get("preconditions", []),
        "step_count": len(recipe.get("steps", [])) if isinstance(recipe.get("steps"), list) else 0,
        "success_proof": recipe.get("success_proof"),
        "success_proof_timeout_s": recipe.get("success_proof_timeout_s"),
    }
    if json_out or get_global_json():
        output.print_json({"status": "success", "data": {"recipe": details}})
    else:
        display = dict(details)
        display["triggers"] = "\n".join(details["triggers"])
        preconditions = details["preconditions"]
        display["preconditions"] = (
            ", ".join(preconditions)
            if isinstance(preconditions, list) and preconditions
            else "None"
        )
        display["success_proof"] = details["success_proof"] or "Not specified"
        display["success_proof_timeout_s"] = (
            details["success_proof_timeout_s"]
            if details["success_proof_timeout_s"] is not None
            else "Default"
        )
        output.print_kv(display, title=f"Recipe: {name}")


@recipes_app.command("validate")
def validate_recipe_file(
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Candidate JSON file (defaults to data/agent/recipes.json).",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Promote migration warnings (anchoring, legacy wait) to errors.",
    ),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output structured JSON."),
):
    """Validate recipe JSON shape, required fields, and action names."""
    path = _resolve_recipes_path(file)
    payload = _load_or_exit(path)
    errors = validate_recipes(payload, strict=strict)
    result = {"file": str(path), "recipe_count": len(recipe_entries(payload)), "errors": errors}

    if errors:
        if json_out or get_global_json():
            output.print_json(
                {"status": "error", "error": {"code": "VALIDATION_FAILED"}, "data": result}
            )
        else:
            output.print_error(
                "Recipe validation failed:\n- " + "\n- ".join(errors),
                code="VALIDATION_FAILED",
            )
        raise typer.Exit(1)

    if json_out or get_global_json():
        output.print_json({"status": "success", "data": result})
    else:
        output.print_success(f"Validated {result['recipe_count']} recipes in {path}")
