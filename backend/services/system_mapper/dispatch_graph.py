"""Dynamic-dispatch registry edges (B3).

The dependency_graph analyzer is *import-only*: it builds module → set(imported
modules) from AST. That misses every module reached by a string-keyed dispatch
table — Celery task-name strings, Flask blueprint auto-registration, plugin.json
route declarations, MCP/LLM tool tables. Such modules have zero static importers
and would be (wrongly) flagged DORMANT_MODULE.

This analyzer surveys those dynamic-dispatch surfaces and produces a *set of
module names that are reached via dispatch*. `codebase_map()` passes that set
into `dependency_graph.analyze()` (and post-filters as a belt-and-suspenders),
so dynamically-reached modules are suppressed from the dormant finding.

Design contract (mirrors the sibling analyzers):
  * `analyze(root, extra_excludes=...) -> {"graph", "findings", "stats"}`.
  * Never raises. Any internal failure degrades to an empty result + a note in
    stats; the dormant-suppression simply does nothing.
  * Purely static (AST + json). No imports of the live app, no subprocess.

It emits NO new findings of its own — its whole job is to REDUCE false-positive
dormant findings. (`graph["dynamic_modules"]` is the payload other analyzers read.)
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from .core import is_excluded


def _module_name(rel: Path) -> str:
    """Convert backend/services/foo.py -> backend.services.foo (mirror dep graph)."""
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ── Celery task-name strings ─────────────────────────────────────────────────

def _is_task_decorator(dec: ast.expr) -> bool:
    """True for @shared_task, @celery_app.task, @app.task, @task (with/without call)."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id in ("shared_task", "task")
    if isinstance(target, ast.Attribute):
        return target.attr in ("task", "shared_task", "periodic_task")
    return False


def _celery_task_modules(root: Path, extra_excludes: frozenset[str]) -> set[str]:
    """Modules that define a Celery task (decorated def OR a `name="..."` registration).

    A module that registers a task is reachable: Celery beat / the broker invoke
    it by task-name string, never via Python import from another of our modules.
    """
    reached: set[str] = set()
    for py in root.rglob("*.py"):
        if is_excluded(py, extra_excludes):
            continue
        try:
            rel = py.relative_to(root)
        except ValueError:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        mod = _module_name(rel)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(_is_task_decorator(d) for d in node.decorator_list):
                    found = True
                    break
            # Factory-registration: `celery_app.task(name="x")(fn)` as a call.
            if isinstance(node, ast.Call) and _is_task_decorator(node):
                for kw in node.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        found = True
                        break
            if found:
                break
        if found:
            reached.add(mod)
    return reached


# ── Flask blueprint auto-registration ────────────────────────────────────────

def _blueprint_modules(root: Path, extra_excludes: frozenset[str]) -> set[str]:
    """Modules defining a Flask Blueprint, plus auto-discovered backend/api/*_api.py.

    Blueprints are wired by the app factory at boot (auto_register_blueprints
    walks backend/api and imports each *_api.py); nothing in the static import
    graph points at them, so they look dormant. They are not.
    """
    reached: set[str] = set()
    for py in root.rglob("*.py"):
        if is_excluded(py, extra_excludes):
            continue
        try:
            rel = py.relative_to(root)
        except ValueError:
            continue
        rel_str = str(rel).replace("\\", "/")
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        mod = _module_name(rel)

        # Any module under backend/api/ that ends in _api.py is auto-discovered.
        if "backend/api/" in rel_str and rel.name.endswith("_api.py"):
            reached.add(mod)
            continue

        # Otherwise: does it construct a Blueprint(...) ? Then it's reached by
        # register_blueprint at boot.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                fname = (func.id if isinstance(func, ast.Name)
                         else func.attr if isinstance(func, ast.Attribute) else None)
                if fname == "Blueprint":
                    reached.add(mod)
                    break
    return reached


# ── plugin.json route / endpoint declarations ────────────────────────────────

def _plugin_declared_modules(root: Path) -> set[str]:
    """Backend modules a plugin.json points at via a `*module*` / `*backend*` key.

    plugin.json files mostly declare HTTP `endpoints` (sidecar paths, not our
    Python modules). But some carry a backend module/handler reference; when they
    do, follow it so a plugin-only-reached handler isn't called dormant.
    """
    reached: set[str] = set()
    for pj in root.rglob("plugin.json"):
        try:
            data = json.loads(pj.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for key, val in data.items():
            kl = str(key).lower()
            if not any(tok in kl for tok in ("module", "backend", "handler", "entry")):
                continue
            if isinstance(val, str) and "." in val and "/" not in val:
                reached.add(val.rsplit(":", 1)[0])
    return reached


# ── MCP / LLM tool tables ─────────────────────────────────────────────────────

def _tool_modules(root: Path, extra_excludes: frozenset[str]) -> set[str]:
    """Modules under backend/tools/ that define a tool class (a class with a
    class-level `name = "..."`). Tools are dispatched by string name through the
    registry (chat engine + MCP tools_adapter), not imported by callers.
    """
    reached: set[str] = set()
    tools_root = root / "backend" / "tools"
    if not tools_root.is_dir():
        return reached
    for py in tools_root.rglob("*.py"):
        if is_excluded(py, extra_excludes):
            continue
        try:
            rel = py.relative_to(root)
        except ValueError:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        defines_tool = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign):
                        for tgt in stmt.targets:
                            if (isinstance(tgt, ast.Name) and tgt.id == "name"
                                    and isinstance(stmt.value, ast.Constant)
                                    and isinstance(stmt.value.value, str)):
                                defines_tool = True
                                break
                if defines_tool:
                    break
        if defines_tool:
            reached.add(_module_name(rel))
    return reached


def analyze(root: Path, extra_excludes: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Compute the set of modules reached only via dynamic dispatch. Never raises.

    Returns {"graph": {"dynamic_modules": [...], "by_source": {...}},
             "findings": [], "stats": {...}}.
    """
    stats: dict[str, Any] = {"applicable": True}
    by_source: dict[str, list[str]] = {}
    dynamic: set[str] = set()

    for label, fn in (
        ("celery_task", lambda: _celery_task_modules(root, extra_excludes)),
        ("blueprint", lambda: _blueprint_modules(root, extra_excludes)),
        ("plugin_json", lambda: _plugin_declared_modules(root)),
        ("tool", lambda: _tool_modules(root, extra_excludes)),
    ):
        try:
            found = fn()
        except Exception as exc:  # never raises out of analyze()
            found = set()
            stats[f"{label}_error"] = repr(exc)
        by_source[label] = sorted(found)
        dynamic |= found

    stats["dynamic_module_count"] = len(dynamic)
    stats["by_source_counts"] = {k: len(v) for k, v in by_source.items()}

    return {
        "graph": {
            "dynamic_modules": sorted(dynamic),
            "by_source": by_source,
        },
        # This analyzer never emits findings — its purpose is to SUPPRESS
        # false-positive dormant findings, not add new ones.
        "findings": [],
        "stats": stats,
    }
