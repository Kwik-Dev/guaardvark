"""The command surface must match the backend API surface.

`docs/CLI_SPEC.md` documents which Studio features the CLI can reach, and which it
cannot. That table drifted silently for a year because nothing connected the two
surfaces: backend areas appeared, the CLI never grew a command, and no test noticed.

This test closes that loop. It walks `backend/api/*_api.py` and fails unless every
area is declared in `cli/llx/commands/_fork/api_coverage.py` as one of:

* ``EXPOSED``      — a command drives it today
* ``PLANNED``      — a command is planned (names the phase in docs/CLI_PLAN.md)
* ``NOT_EXPOSED``  — deliberately none, with the reason

When upstream adds a backend API area this fails and names it. That is the point:
either write the command, or say why not. A silent gap becomes a red test.

It also checks the reverse direction, so deleting or renaming a backend API cannot
leave a stale claim behind in the coverage table.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from llx.commands._fork import api_coverage

_API_DIR = Path(__file__).resolve().parents[2] / "backend" / "api"


def _backend_areas() -> set[str]:
    """Backend API module stems, e.g. ``backend/api/cast_library_api.py`` -> ``cast_library``."""
    if not _API_DIR.is_dir():
        pytest.skip(f"no backend/api at {_API_DIR} — CLI installed without the repo")
    return {
        re.sub(r"_api$", "", p.stem)
        for p in _API_DIR.glob("*_api.py")
    }


def test_every_backend_area_is_exposed_or_declared():
    areas = _backend_areas()
    undeclared = sorted(a for a in areas if api_coverage.classify(a) == "unknown")
    assert not undeclared, (
        "these backend API areas have no CLI command and no declared reason:\n  "
        + "\n  ".join(undeclared)
        + "\n\nAdd a command, or declare each in "
        "cli/llx/commands/_fork/api_coverage.py "
        "(EXPOSED / PLANNED / NOT_EXPOSED with a reason). "
        "Keep docs/CLI_SPEC.md section 9 in step."
    )


def test_no_declaration_outlives_its_backend_area():
    areas = _backend_areas()
    stale = sorted(a for a in api_coverage.all_declared() if a not in areas)
    assert not stale, (
        "these areas are declared in api_coverage.py but no longer exist in "
        "backend/api:\n  " + "\n  ".join(stale)
        + "\n\nRemove or rename them so the coverage table stays honest."
    )


def test_a_planned_area_becomes_exposed_when_its_command_lands():
    """A shipped command must not still be filed as planned."""
    from llx.commands._fork import registry

    mounted = {name for _app, name in registry.typer_apps()}
    if not mounted:
        pytest.skip("no fork commands mounted yet (Phase 0)")

    # area -> the command name that would expose it, derived from PLANNED values
    # ("cast — CLI_PLAN 3.2 (Phase 2)" -> "cast").
    for area, plan in api_coverage.PLANNED.items():
        command = plan.split("—")[0].strip().split()[0]
        if command in mounted and area not in api_coverage.EXPOSED:
            pytest.fail(
                f"command {command!r} is mounted but {area!r} is still filed as "
                f"PLANNED ({plan!r}). Move it to EXPOSED in api_coverage.py."
            )


def _known_command_names() -> set[str]:
    """Every command name the CLI exposes: the REPL tree plus the shell commands.

    The two surfaces differ — `ask`, `setup` and `launch` are shell-only, while
    `remember` and `todo` are REPL-only — so a coverage claim must be checkable
    against both, not just COMMAND_TREE.
    """
    from llx.command_catalog import COMMAND_TREE

    names = set(COMMAND_TREE)
    try:
        import typer.main as typer_main

        from llx.main import app as typer_app

        click_cmd = typer_main.get_command(typer_app)
        names |= set(getattr(click_cmd, "commands", {}) or {})
    except Exception:  # pragma: no cover - introspection is best-effort
        pass
    return names


def test_exposed_commands_are_real_groups():
    """The primary command named by each EXPOSED value must exist.

    Values are prose ("jobs (bulk generation job polling)"), so only the first token
    is validated — enough to catch a typo or a renamed group, which is the failure
    this test exists for.
    """
    known = _known_command_names()
    if "ask" not in known:  # pragma: no cover - introspection unavailable
        pytest.skip("could not introspect the shell command tree; COMMAND_TREE alone is not enough")
    unknown = {}
    for area, value in api_coverage.EXPOSED.items():
        command = value.split()[0].strip(",;:")
        if command not in known:
            unknown[command] = area
    assert not unknown, (
        "EXPOSED names a command that does not exist (first token of each value):\n  "
        + "\n  ".join(f"{c} <- {a}" for c, a in sorted(unknown.items()))
    )
