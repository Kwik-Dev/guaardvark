"""Fork-owned command registry — the single seam into the upstream CLI.

Why this exists
---------------
`llx/main.py` imports each command module by hand and calls `app.add_typer(...)`
once per group. Adding a fork command the same way would edit an upstream file on
every single command, and `cli/` is upstream-owned: no `cloud-plus` commit has ever
touched it, so each edit is a conflict on the next upstream sync.

Instead, `main.py` carries exactly two forked statements: a single import, and the
loop below. Everything else lives under this package, and modules are discovered
automatically — adding a command means adding a file, not editing a shared list.

    from llx.commands._fork import registry as _fork_registry
    ...
    for _fork_app, _fork_name in _fork_registry.typer_apps():
        app.add_typer(_fork_app, name=_fork_name)

A broken fork module must not take the CLI with it: `typer_apps()` runs at import
time from `main.py`, so an unguarded import error would break every command *and*
`guaardvark --version` with a raw traceback. Imports are guarded per module, the
failure is collected rather than raised, and `load_errors()` exposes it for tests
and diagnostics.

Adding a command
----------------
1. Create `commands/_fork/<name>.py` exporting:

       COMMAND_NAME = "cast"          # the shell command name
       app = typer.Typer(...)         # the Typer app to mount

2. Declare the backend API area(s) it drives in `api_coverage.EXPOSED`. The
   spec-parity test (`cli/tests/test_spec_parity.py`) fails until you do, which is
   the point: the coverage table in `docs/CLI_SPEC.md` must not drift from the code.

3. Add a `--json` branch, and cover the command with a fixture-based test (see
   `cli/tests/test_fixture_smoke.py` for the pattern; the shared `fake_backend`,
   `cli_runner` and `isolated_home` fixtures live in `cli/tests/conftest.py`).

REPL (`/foo`) commands are deliberately NOT hooked yet
------------------------------------------------------
The REPL surface is `llx/command_catalog.py`'s `COMMAND_TREE`, and
`cli/tests/test_command_catalog_contract.py` pins it to `SlashRouter`'s registered
names, so a fork REPL command means editing both `slash.py` and `command_catalog.py`
— two more upstream files. No planned fork group needs a REPL-only command (they map
to an existing group or a new shell group), so the hook is deferred rather than paid
for. When it is needed, the patch is one merge in `slash.py::_register_repl_commands`
plus the catalog entry; see `docs/CLI_SPEC.md` section 11.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Any, List, Tuple

logger = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).resolve().parent
_RESERVED = {"registry", "api_coverage"}


def _candidate_modules() -> List[str]:
    """Importable module names directly under this package, bar the infrastructure."""
    names = []
    for info in pkgutil.iter_modules([str(_PKG_DIR)]):
        if info.ispkg or info.name.startswith("_") or info.name in _RESERVED:
            continue
        names.append(info.name)
    return sorted(names)


# Modules that failed to import, as (module_name, "ExceptionType: message"). A broken
# fork command is reported here and skipped; it must never break the whole CLI.
_LOAD_ERRORS: List[Tuple[str, str]] = []


def load_errors() -> List[Tuple[str, str]]:
    """Fork modules that could not be imported, in discovery order."""
    return list(_LOAD_ERRORS)


def typer_apps() -> List[Tuple[Any, str]]:
    """`(typer_app, command_name)` pairs for `main.py` to mount.

    A module participates by exporting both `COMMAND_NAME` (str) and `app` (a
    `typer.Typer`). Anything else in the package is ignored, so helper modules can
    live here without being mounted. An import error is recorded in `load_errors()`
    and skipped — one bad fork module must not take the whole CLI down.
    """
    import typer

    mounted: List[Tuple[Any, str]] = []
    seen: dict[str, str] = {}
    del _LOAD_ERRORS[:]
    for name in _candidate_modules():
        try:
            module = importlib.import_module(f"{__package__}.{name}")
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            message = f"{type(exc).__name__}: {exc}"
            _LOAD_ERRORS.append((name, message))
            # Say so out loud: a fork command that vanishes without explanation is
            # the silent-failure pattern this repo keeps paying for.
            logger.warning(
                "fork command %r failed to import and will not be available: %s", name, message
            )
            continue
        command_name = getattr(module, "COMMAND_NAME", None)
        app = getattr(module, "app", None)
        if not isinstance(command_name, str) or not command_name:
            continue
        if not isinstance(app, typer.Typer):
            continue
        # Click's add_typer/add_command overwrites silently on a name clash, so a
        # duplicate would drop one command with no message at all.
        if command_name in seen:
            message = (
                f"duplicate COMMAND_NAME {command_name!r}, already mounted from {seen[command_name]}"
            )
            _LOAD_ERRORS.append((name, message))
            logger.warning("fork command %r skipped: %s", name, message)
            continue
        seen[command_name] = name
        mounted.append((app, command_name))
    return mounted


def repl_commands() -> List[str]:
    """Fork REPL command names. Empty by design — see the module docstring."""
    return []
