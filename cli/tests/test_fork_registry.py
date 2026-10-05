"""The fork seam itself: discovery, mounting, and internal consistency of the
coverage maps.

Phase 0 ships no commands, so these tests are what prove the seam works before any
command depends on it. Without them, the first real command would be the first time
the registry is exercised.
"""
from __future__ import annotations

import types

import pytest
import typer

from llx.commands._fork import api_coverage, registry


# --- discovery -------------------------------------------------------------


def _fake_module(name: str, *, command_name=None, app=None):
    mod = types.ModuleType(f"llx.commands._fork.{name}")
    if command_name is not None:
        mod.COMMAND_NAME = command_name
    if app is not None:
        mod.app = app
    return mod


def _stub_iter(monkeypatch, names):
    monkeypatch.setattr(
        registry.pkgutil, "iter_modules",
        lambda path: [types.SimpleNamespace(name=n, ispkg=False) for n in names],
    )


def test_a_module_exporting_command_name_and_app_is_mounted(monkeypatch):
    app = typer.Typer()
    fake = _fake_module("cast", command_name="cast", app=app)
    _stub_iter(monkeypatch, ["cast"])
    monkeypatch.setattr(registry.importlib, "import_module", lambda name: fake)

    assert registry.typer_apps() == [(app, "cast")]


def test_a_module_without_command_name_is_ignored(monkeypatch):
    """Helper modules may live in the package without being mounted."""
    fake = _fake_module("helpers", app=typer.Typer())
    _stub_iter(monkeypatch, ["helpers"])
    monkeypatch.setattr(registry.importlib, "import_module", lambda name: fake)

    assert registry.typer_apps() == []


def test_a_module_whose_app_is_not_a_typer_is_ignored(monkeypatch):
    fake = _fake_module("oops", command_name="oops", app=object())
    _stub_iter(monkeypatch, ["oops"])
    monkeypatch.setattr(registry.importlib, "import_module", lambda name: fake)

    assert registry.typer_apps() == []


def test_private_and_infrastructure_modules_are_never_candidates(monkeypatch):
    _stub_iter(monkeypatch, ["_private", "registry", "api_coverage", "cast"])
    assert registry._candidate_modules() == ["cast"]


def test_the_real_package_mounts_without_error():
    """Phase 0: the seam is wired and the package is importable."""
    assert isinstance(registry.typer_apps(), list)
    # A fork module that raises on import is skipped with a log line, so pin the
    # whole package as clean — otherwise a new broken module keeps the suite green.
    assert registry.load_errors() == []


# --- failure containment ---------------------------------------------------
# `typer_apps()` runs at import time from main.py, so an unguarded error here would
# break every command *and* `guaardvark --version` with a raw traceback.


def test_a_module_that_raises_on_import_does_not_break_the_cli(monkeypatch):
    good = _fake_module("cast", command_name="cast", app=typer.Typer())
    _stub_iter(monkeypatch, ["boom", "cast"])

    def import_module(name):
        if name.endswith(".boom"):
            raise RuntimeError("kaboom")
        return good

    monkeypatch.setattr(registry.importlib, "import_module", import_module)

    mounted = registry.typer_apps()  # must not raise

    assert [n for _a, n in mounted] == ["cast"]
    assert ("boom", "RuntimeError: kaboom") in registry.load_errors()


def test_a_duplicate_command_name_is_reported_not_silently_dropped(monkeypatch):
    """Click's add_typer overwrites silently, so a duplicate would lose a command
    with no message at all."""
    first = typer.Typer()
    second = typer.Typer()
    modules = {"alpha": _fake_module("alpha", command_name="cast", app=first),
               "beta": _fake_module("beta", command_name="cast", app=second)}
    _stub_iter(monkeypatch, ["alpha", "beta"])
    monkeypatch.setattr(registry.importlib, "import_module", lambda name: modules[name.rsplit(".", 1)[-1]])

    mounted = registry.typer_apps()

    assert mounted == [(first, "cast")]
    assert any("duplicate COMMAND_NAME" in message for _mod, message in registry.load_errors())


def test_fork_groups_are_repl_commands_too():
    """Every mounted fork group is offered to the REPL, from this one registry.

    Wiring a REPL command means appearing in BOTH the router and `COMMAND_TREE`,
    which the upstream contract test pins to each other. Both merge from here, so
    this asserts the registry actually offers them.
    """
    mounted = {name for _app, name in registry.typer_apps()}
    names = registry.repl_commands()
    assert names, "no fork group is offered to the REPL"
    assert set(names) == mounted


def test_repl_catalog_covers_every_group_with_sorted_subcommands():
    tree, meta = registry.repl_catalog()
    names = set(registry.repl_commands())
    # tree covers the fork groups plus every upstream group the fork extends.
    assert set(tree) == names | set(registry.extended_groups())
    # meta is only the fork groups: an extended group keeps its upstream description.
    assert set(meta) == names
    for name, subs in tree.items():
        assert subs == sorted(subs), f"{name}: subcommands not sorted"
    # Derive, not hardcode: a real group's subcommands come through, and every group
    # carries a non-empty description for /help.
    assert "list" in tree["cast"]
    assert all(meta.values()), f"groups with no description: {[n for n, m in meta.items() if not m]}"


def test_repl_help_group_lists_the_same_names():
    title, names = registry.repl_help_group()
    assert title == "Fork Commands"
    assert names == registry.repl_commands()


def test_extended_groups_and_their_subcommands_reach_the_catalog():
    """A module extending an upstream group declares EXTENDS; the REPL catalog picks the
    added subcommands up from the app itself, not from a hand-kept list."""
    groups = registry.extended_groups()
    assert {"audio", "film-crew", "music-video"} <= set(groups)

    tree, meta = registry.repl_catalog()
    for group in ("audio", "film-crew", "music-video"):
        assert group in tree, group
    assert "approve-storyboard" in tree["film-crew"]
    assert "transcribe" in tree["audio"]
    assert "cuts" in tree["music-video"]
    # An extended group keeps its upstream description, so the registry emits no meta
    # for it (command_catalog fills gaps rather than overwriting).
    assert not ({"audio", "film-crew", "music-video"} & set(meta))


def test_a_module_that_adds_to_an_upstream_app_declares_extends():
    """A future extension module cannot silently stay out of completion /help.

    The `*_ext.py` modules and `render_gates.py` add commands to an upstream app; each
    must declare `EXTENDS` or the catalog cannot see them. This fails a module that
    imports an upstream app without one. `captions.py` is not flagged: it extends the
    fork's own `video-editor` group, which `repl_catalog()` derives in full.
    """
    import ast
    import importlib
    from pathlib import Path

    package_dir = Path(registry.__file__).resolve().parent
    checked = 0
    for path in sorted(package_dir.glob("*.py")):
        if path.name.startswith("_") or path.name in {"registry.py", "api_coverage.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        adds_to_upstream = any(
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("llx.commands.")
            and not node.module.startswith("llx.commands._fork")
            for node in ast.walk(tree)
        )
        module = importlib.import_module(f"llx.commands._fork.{path.stem}")
        if not (adds_to_upstream or hasattr(module, "EXTENDS")):
            continue
        checked += 1
        assert isinstance(getattr(module, "EXTENDS", None), dict), (
            f"{path.name}: adds commands to an upstream app but declares no EXTENDS"
        )
    assert checked >= 4, f"expected the four extension modules, checked {checked}"


# --- coverage maps ---------------------------------------------------------


def test_coverage_maps_are_disjoint():
    maps = {
        "EXPOSED": set(api_coverage.EXPOSED),
        "PLANNED": set(api_coverage.PLANNED),
        "NOT_EXPOSED": set(api_coverage.NOT_EXPOSED),
    }
    for left, right in (("EXPOSED", "PLANNED"), ("EXPOSED", "NOT_EXPOSED"), ("PLANNED", "NOT_EXPOSED")):
        overlap = maps[left] & maps[right]
        assert not overlap, f"{left} and {right} both declare: {sorted(overlap)}"


def test_every_declaration_carries_a_value():
    for name, mapping in (
        ("EXPOSED", api_coverage.EXPOSED),
        ("PLANNED", api_coverage.PLANNED),
        ("NOT_EXPOSED", api_coverage.NOT_EXPOSED),
    ):
        empty = [a for a, v in mapping.items() if not (v or "").strip()]
        assert not empty, f"{name} entries with no value: {empty}"


def test_planned_entries_name_a_phase_and_a_command():
    for area, plan in api_coverage.PLANNED.items():
        assert "CLI_PLAN" in plan, f"{area}: {plan!r} does not reference docs/CLI_PLAN.md"
        assert "Phase" in plan, f"{area}: {plan!r} names no phase"


def test_classify_covers_every_category():
    assert api_coverage.classify(next(iter(api_coverage.EXPOSED))) == "exposed"
    assert api_coverage.classify(next(iter(api_coverage.NOT_EXPOSED))) == "not_exposed"
    # PLANNED is legitimately empty now that every area is exposed or explained; the
    # category still has to answer correctly when a future phase refills it.
    if api_coverage.PLANNED:
        assert api_coverage.classify(next(iter(api_coverage.PLANNED))) == "planned"
    assert api_coverage.classify("definitely-not-an-api") == "unknown"


def test_every_area_is_exposed_or_explained():
    """The end state of the coverage work: nothing left undecided.

    Phase 4 closed the last planned group. If this ever fails, a backend area appeared
    without anyone deciding what the CLI does about it — which is the whole point of the
    spec-parity test.
    """
    assert not api_coverage.PLANNED, (
        "these areas are still marked planned: "
        + ", ".join(sorted(api_coverage.PLANNED))
    )


def test_the_web_research_group_is_named_websearch_not_web():
    """The shell group is `websearch`; the REPL `/web` opens the web UI.

    `test_spec_parity` accepts `web` in this EXPOSED value because `web` is a valid
    REPL name, so pin the shell name here too, or a stale value passes silently.
    """
    mounted = {name for _app, name in registry.typer_apps()}
    assert "websearch" in mounted
    assert "web" not in mounted
    assert api_coverage.EXPOSED["web_search"].split()[0] == "websearch"
