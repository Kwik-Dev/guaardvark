"""Fork-owned command registry — the single seam into the upstream CLI.

Why this exists
---------------
`llx/main.py` imports each command module by hand and calls `app.add_typer(...)`
once per group. Adding a fork command the same way would edit an upstream file on
every single command, and `cli/` is upstream-owned: no `cloud-plus` commit has ever
touched it, so each edit is a conflict on the next upstream sync.

Instead, `main.py` carries exactly two fork lines:

    from llx.commands._fork import registry as _fork_registry
    ...
    for _fork_app, _fork_name in _fork_registry.typer_apps():
        app.add_typer(_fork_app, name=_fork_name)

Everything else lives under this package, and modules are discovered automatically —
adding a command means adding a file, not editing a shared list.

Adding a command
----------------
1. Create `commands/_fork/<name>.py` exporting:

       COMMAND_NAME = "cast"          # the shell command name
       app = typer.Typer(...)         # the Typer app to mount

   Optionally also `HELP = "..."`.

2. Declare the backend API area(s) it drives in `api_coverage.EXPOSED`. The
   spec-parity test (`cli/tests/test_spec_parity.py`) fails until you do, which is
   the point: the coverage table in `docs/CLI_SPEC.md` must not drift from the code.

3. Add a `--json` branch and a golden snapshot test (see `cli/tests/conftest.py`).

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
import pkgutil
from pathlib import Path
from typing import Any, List, Tuple

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


def typer_apps() -> List[Tuple[Any, str]]:
    """`(typer_app, command_name)` pairs for `main.py` to mount.

    A module participates by exporting both `COMMAND_NAME` (str) and `app` (a
    `typer.Typer`). Anything else in the package is ignored, so helper modules can
    live here without being mounted.
    """
    import typer  # local import: `llx --version` should not pay for it

    mounted: List[Tuple[Any, str]] = []
    for name in _candidate_modules():
        module = importlib.import_module(f"{__package__}.{name}")
        command_name = getattr(module, "COMMAND_NAME", None)
        app = getattr(module, "app", None)
        if not isinstance(command_name, str) or not command_name:
            continue
        if not isinstance(app, typer.Typer):
            continue
        mounted.append((app, command_name))
    return mounted


def repl_commands() -> List[str]:
    """Fork REPL command names. Empty by design — see the module docstring."""
    return []
