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

REPL (`/foo`) commands
----------------------
Fork groups are REPL commands too, and this registry is what drives them. The REPL
surface is `llx/command_catalog.py`'s `COMMAND_TREE`, and
`cli/tests/test_command_catalog_contract.py` pins it to `SlashRouter`'s registered
names, so a fork group has to appear in both. Instead of editing those upstream files
once per command, each merges this registry in one guarded block:

* `command_catalog.py` unions `repl_catalog()[0]` into `COMMAND_TREE` (a new name is
  added; an existing upstream group keeps its order and gains the fork's subcommands)
  and fills gaps in `COMMAND_META`, so the catalog can never drift from the apps.
* `slash.py` registers `repl_apps()` through its existing `_register_subapp` path and
  appends `repl_help_group()` to `_HELP_GROUPS`, so `/help` lists them too.

A module that **extends an upstream group** (no `COMMAND_NAME`/`app`; importing it
registers commands on the upstream app) declares `EXTENDS = {group_name: upstream_app}`.
`extended_groups()` reads that, so the subcommands it adds reach completion and `/help`
too — still without a hand-kept list of group names.

Adding a fork group therefore still means adding one file — it reaches the shell and
the REPL with no further edit. See `docs/CLI_SPEC.md` section 11.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

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


def _import_module(name: str) -> Any:
    """Import one candidate module, recording a failure in `load_errors()`; None on failure.

    Shared by `typer_apps()` and `extended_groups()` so a broken fork module is reported
    the same way whichever one imports it first. Deduplicated by module name, since both
    walk the same candidate list.
    """
    try:
        return importlib.import_module(f"{__package__}.{name}")
    except Exception as exc:  # noqa: BLE001 - isolation is the point
        message = f"{type(exc).__name__}: {exc}"
        if not any(existing == name for existing, _ in _LOAD_ERRORS):
            _LOAD_ERRORS.append((name, message))
        # Say so out loud: a fork command that vanishes without explanation is
        # the silent-failure pattern this repo keeps paying for.
        logger.warning(
            "fork command %r failed to import and will not be available: %s", name, message
        )
        return None


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
        module = _import_module(name)
        if module is None:
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
    """Fork group names, which are REPL command names too (one per shell group)."""
    return [name for _app, name in typer_apps()]


def repl_apps() -> List[Tuple[Any, str]]:
    """`(typer_app, name)` pairs for `SlashRouter` to register as REPL commands.

    The same apps `main.py` mounts on the shell, so a group behaves identically on
    both surfaces: same options, same `--yes` gates, same JSON.
    """
    return typer_apps()


def extended_groups() -> Dict[str, Any]:
    """Upstream groups the fork extends, mapped to the upstream app object.

    An extension module has no `COMMAND_NAME`/`app` (importing it registers extra
    commands on an upstream app) and declares `EXTENDS = {group_name: upstream_app}`.
    Read here so the REPL catalog knows which upstream groups gained subcommands,
    without a second hand-kept list of group names.

    Import errors are isolated per module, exactly as in `typer_apps()`.
    """
    groups: Dict[str, Any] = {}
    for name in _candidate_modules():
        module = _import_module(name)
        if module is None:
            continue
        extends = getattr(module, "EXTENDS", None)
        if not isinstance(extends, dict):
            continue
        for group, upstream_app in extends.items():
            if isinstance(group, str) and group:
                groups[group] = upstream_app
    return groups


def _subcommands(app: Any) -> List[str]:
    """The subcommand names `app` registers, sorted; `[]` when not introspectable.

    Note ``get_command(app).commands`` includes commands registered as `hidden=True`
    (click keeps them), so a future hidden fork subcommand would surface in completion
    and be required by the catalog contract test. That is a deliberate consequence of
    deriving the list from the app rather than a hand-kept one.
    """
    from typer.main import get_command

    try:
        return sorted(getattr(get_command(app), "commands", {}) or {})
    except Exception as exc:  # noqa: BLE001 - isolation is the point
        logger.warning("fork app subcommands not introspectable: %s", exc)
        return []


def repl_catalog() -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """`(COMMAND_TREE entries, COMMAND_META entries)` for the fork groups.

    One pass over the mounted apps: the subcommand names come from the app itself
    (via `typer.main.get_command`) and the description from its help text, so the
    REPL catalog is *derived* rather than a second hand-kept list that can drift.

    `tree` also carries every **upstream** group the fork extends
    (`extended_groups()`), with the app's *complete* subcommand list. `command_catalog.py`
    unions that with the group's existing upstream list, so the fork's own subcommands
    reach completion and `/help` without a hand-kept entry. No `meta` entry is produced
    for those groups — their upstream description stands.

    Introspection of one app is isolated: a malformed app yields an empty subcommand
    list rather than taking the catalog (and the whole REPL) down.
    """
    from collections import OrderedDict

    tree: Dict[str, List[str]] = OrderedDict()
    meta: Dict[str, str] = {}
    for app, name in typer_apps():
        tree[name] = _subcommands(app)
        help_text = getattr(getattr(app, "info", None), "help", None) or ""
        meta[name] = " ".join(str(help_text).split())
    for group, upstream_app in extended_groups().items():
        tree[group] = _subcommands(upstream_app)
    return tree, meta


def repl_help_group() -> Tuple[str, List[str]]:
    """`(section_title, command_names)` for the REPL `/help` listing."""
    return ("Fork Commands", repl_commands())
