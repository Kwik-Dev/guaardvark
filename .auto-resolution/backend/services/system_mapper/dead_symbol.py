"""Function-level dead-symbol detection (vulture-lite, AST, conservative).

The dependency_graph analyzer finds dead *modules* (nothing imports them). This
analyzer goes a level deeper: it finds *functions* that are defined inside a
live module but referenced nowhere in the whole repo — the "which functions are
placebo" question.

This is necessarily a STATIC, BEST-EFFORT analysis. Python's dynamic dispatch
(getattr, Celery task-name strings, Flask endpoint registration, framework
method-name conventions) means a function can be very much alive at runtime with
zero static references. So the bias here is heavily toward false-NEGATIVES: when
in doubt, DO NOT flag. The output is advisory (INFO/LOW) and is deliberately
left OUT of actions.DISPATCHABLE_KINDS so it can never auto-dispatch to the fix
engine — a wrong call could delete real recovery logic.

Algorithm:
  1. For each internal .py module, collect every top-level + class-level
     `def`/`async def` name DEFINED (with its source path).
  2. Across the WHOLE repo, collect every NAME that is *referenced*: ast.Name
     loads, ast.Attribute attrs, decorator names, literal getattr() string
     args, and every string literal (covers dynamic dispatch / task names /
     endpoints), plus __all__ entries.
  3. A defined function is a DEAD_SYMBOL candidate iff its name appears NOWHERE
     in the referenced set except its own definition.
  4. Conservatism filters (any one of these spares a candidate):
       - dunder names (__x__)
       - the name appears in any string literal anywhere
       - the function is decorated with anything (route/task/fixture/property…)
       - test_* functions
       - common framework override method names (execute/run/handle/…)
       - names that appear in any __all__
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Any

from .core import Finding, FindingKind, Severity, is_excluded

# Cap so a pathological repo can't flood the findings list.
_MAX_FINDINGS = 100

# Method names that frameworks/base-classes invoke by convention. A subclass
# overriding one of these is live even with no static reference to *that*
# class's version. Conservative allowlist — better a miss than a wrong flag.
_FRAMEWORK_METHODS: frozenset[str] = frozenset({
    # tool / command / handler conventions
    "execute", "run", "handle", "call", "process", "dispatch", "invoke",
    "apply", "perform", "main",
    # lifecycle / context-manager / iterator / descriptor protocol-ish
    "setup", "teardown", "start", "stop", "close", "open", "connect",
    "disconnect", "shutdown", "initialize", "finalize", "cleanup",
    "on_start", "on_stop", "on_error", "on_success", "on_failure",
    # serialization / common dunder-ish-by-name
    "to_dict", "from_dict", "to_json", "from_json", "serialize", "deserialize",
    "validate", "save", "load", "delete", "create", "update", "get", "post",
    "put", "patch", "render", "build", "compile", "parse", "format",
    # pytest / unittest non-test_ hooks
    "setup_method", "teardown_method", "setup_class", "teardown_class",
    "setUp", "tearDown",
    # celery / task conventions
    "before_start", "after_return", "on_retry",
    # web framework callbacks
    "before_request", "after_request", "teardown_request", "index",
})


class _DefCollector(ast.NodeVisitor):
    """Collect top-level and class-level def/async-def names, recording for each
    whether it is decorated and whether it is a class method (vs module-level)."""

    def __init__(self) -> None:
        # name -> dict(decorated: bool, is_method: bool)
        self.defs: dict[str, dict[str, bool]] = {}

    def _record(self, node, is_method: bool) -> None:
        meta = self.defs.setdefault(
            node.name, {"decorated": False, "is_method": False}
        )
        if node.decorator_list:
            meta["decorated"] = True
        if is_method:
            meta["is_method"] = True

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node, is_method=False)
        # do NOT recurse into the function body for *definitions* — nested
        # closures are an internal detail, not a public dead-symbol candidate.

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record(node, is_method=False)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._record(child, is_method=True)
        # recurse so nested classes' methods are also collected
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                self.visit_ClassDef(child)


def _collect_defs(tree: ast.AST) -> dict[str, dict[str, bool]]:
    c = _DefCollector()
    # Walk only module top-level + class bodies (handled inside the collector).
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            c._record(node, is_method=False)
        elif isinstance(node, ast.ClassDef):
            c.visit_ClassDef(node)
    return c.defs


def _decorator_names(dec: ast.expr) -> set[str]:
    """Every identifier appearing in a decorator expression (so a decorator
    *reference* counts as a use of the decorator function itself)."""
    out: set[str] = set()
    for sub in ast.walk(dec):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def _collect_references(tree: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """Return (referenced_names, string_literals, all_exports) for one module.

    referenced_names: ast.Name loads + ast.Attribute attrs + decorator idents
                      + literal getattr() 2nd-arg strings.
    string_literals:  every string constant anywhere (covers dynamic dispatch).
    all_exports:      entries of any module-level __all__ list/tuple.
    """
    referenced: set[str] = set()
    strings: set[str] = set()
    exports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            # Only "load" contexts are uses; a bare store/del isn't a reference.
            if isinstance(node.ctx, ast.Load):
                referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.add(node.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                referenced |= _decorator_names(dec)
        elif isinstance(node, ast.Call):
            # getattr(obj, "method") — the literal attr name is a real reference.
            func = node.func
            is_getattr = (
                (isinstance(func, ast.Name) and func.id == "getattr")
                or (isinstance(func, ast.Attribute) and func.attr == "getattr")
            )
            if is_getattr and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    referenced.add(arg.value)
        elif isinstance(node, ast.Assign):
            # __all__ = [...] / __all__ += [...]
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    exports |= _string_seq(node.value)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                exports |= _string_seq(node.value)

    return referenced, strings, exports


def _string_seq(value: ast.expr) -> set[str]:
    out: set[str] = set()
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        for elt in value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.add(elt.value)
    return out


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def analyze(root: Path, extra_excludes: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Find functions defined but statically referenced nowhere. Never raises."""
    findings: list[Finding] = []
    stats: dict[str, Any] = {
        "modules_scanned": 0,
        "functions_defined": 0,
        "dead_symbols": 0,
        "suppressed_conservative": 0,
    }

    try:
        # 1. Parse every internal module once: collect its defs + references.
        #    module_defs: rel_path_str -> {name: meta}
        module_defs: dict[str, dict[str, dict[str, bool]]] = {}
        all_referenced: set[str] = set()
        all_strings: set[str] = set()
        all_exports: set[str] = set()

        for py in root.rglob("*.py"):
            if is_excluded(py, extra_excludes):
                continue
            try:
                rel = str(py.relative_to(root))
            except ValueError:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue  # unparseable → skip, never raise

            module_defs[rel] = _collect_defs(tree)
            refs, strings, exports = _collect_references(tree)
            all_referenced |= refs
            all_strings |= strings
            all_exports |= exports
            stats["modules_scanned"] += 1

        # 2. Evaluate each defined function against the global reference set.
        for rel, defs in module_defs.items():
            for name, meta in defs.items():
                stats["functions_defined"] += 1

                # --- conservatism gates (any → spare it) --------------------
                if _is_dunder(name):
                    continue
                if meta["decorated"]:
                    # decorators imply external invocation (route/task/fixture/…)
                    stats["suppressed_conservative"] += 1
                    continue
                if name.startswith("test_"):
                    continue
                if meta["is_method"] and name in _FRAMEWORK_METHODS:
                    stats["suppressed_conservative"] += 1
                    continue
                if name in all_exports:
                    stats["suppressed_conservative"] += 1
                    continue
                if name in all_strings:
                    # any string literal mentions it → likely dynamic dispatch
                    stats["suppressed_conservative"] += 1
                    continue

                # --- the actual deadness test -------------------------------
                if name in all_referenced:
                    continue  # statically referenced somewhere → live

                # Dead candidate.
                stats["dead_symbols"] += 1
                if len(findings) < _MAX_FINDINGS:
                    findings.append(Finding(
                        kind=FindingKind.DEAD_SYMBOL,
                        severity=Severity.LOW if meta["is_method"] else Severity.INFO,
                        summary=f"Function never statically referenced: "
                                f"{name}() in {rel}",
                        paths=[rel],
                        evidence={
                            "symbol": name,
                            "defined_in": rel,
                            "reason": "no static reference found",
                        },
                    ))

        # Rollup if we hit the cap.
        if stats["dead_symbols"] > _MAX_FINDINGS:
            findings.append(Finding(
                kind=FindingKind.DEAD_SYMBOL,
                severity=Severity.INFO,
                summary=f"{stats['dead_symbols'] - _MAX_FINDINGS} additional "
                        f"dead-symbol candidates suppressed (cap={_MAX_FINDINGS})",
                paths=[],
                evidence={"total": stats["dead_symbols"], "shown": _MAX_FINDINGS},
            ))

    except Exception as e:  # absolute belt-and-suspenders: never raise
        return {
            "graph": {},
            "findings": [Finding(
                kind=FindingKind.DEAD_SYMBOL,
                severity=Severity.INFO,
                summary=f"dead_symbol analyzer internal error: {e}",
            )],
            "stats": {**stats, "error": str(e)},
        }

    return {"graph": {}, "findings": findings, "stats": stats}
