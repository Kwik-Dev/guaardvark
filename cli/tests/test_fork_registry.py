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


def test_repl_commands_are_deferred_on_purpose():
    """No fork REPL command yet — wiring one means editing slash.py and
    command_catalog.py, which the upstream contract test pins to each other."""
    assert registry.repl_commands() == []


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
    assert api_coverage.classify(next(iter(api_coverage.PLANNED))) == "planned"
    assert api_coverage.classify(next(iter(api_coverage.NOT_EXPOSED))) == "not_exposed"
    assert api_coverage.classify("definitely-not-an-api") == "unknown"
