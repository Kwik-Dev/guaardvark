"""The safety rules decide() enforces, whatever a model answers.

Guards never ask a model: no file in decision_data.GUARD_MODULES imports the
decision modules, and decide() refuses a call made from one of them.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.services import decision_settings
from backend.services import nl_control_plane as ncp
from backend.services.decision_data import GUARD_MODULES

REPO = Path(ncp._REPO_ROOT)

# Everything that asks a model, or that decides how a model is asked.
DECISION_MODULES = (
    "backend.services.nl_control_plane",
    "backend.services.decision_data",
    "backend.services.decision_prompt",
    "backend.services.decision_evidence",
    "backend.services.decision_settings",
)
DECISION_TAILS = {m.rsplit(".", 1)[-1] for m in DECISION_MODULES}


def _is_decision_module(name: str) -> bool:
    return name in DECISION_MODULES or name.rsplit(".", 1)[-1] in DECISION_TAILS


def _guard_files():
    for path, functions in sorted(GUARD_MODULES.items()):
        if path.endswith("/"):
            for file in sorted((REPO / path).rglob("*.py")):
                yield file, None
        else:
            yield REPO / path, functions


def _forbidden_bindings(tree: ast.AST) -> tuple[list[str], set[str]]:
    """(what a file imports from the decision modules, the names those imports bind)."""
    found, bound = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_decision_module(alias.name):
                    found.append(alias.name)
                    bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if _is_decision_module(module) or _is_decision_module(f"{module}.{alias.name}"):
                    found.append(f"{module}.{alias.name}")
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in DECISION_MODULES:
            found.append(node.value)
    return found, bound


def test_every_guard_path_exists():
    for path, functions in GUARD_MODULES.items():
        target = REPO / path
        assert target.exists(), f"GUARD_MODULES names {path}, which does not exist"
        if functions:
            tree = ast.parse(target.read_text(encoding="utf-8"))
            defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert set(functions) <= defined, (path, set(functions) - defined)


@pytest.mark.parametrize("file, functions", list(_guard_files()), ids=lambda v: str(v))
def test_no_guard_imports_the_decision_modules(file, functions):
    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    if functions is None:
        found, _ = _forbidden_bindings(tree)
        assert not found, f"{file.relative_to(REPO)} imports {found}"
        return
    _, module_bound = _forbidden_bindings(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            found, _ = _forbidden_bindings(node)
            used = sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & module_bound)
            assert not found and not used, f"{node.name} in {file.relative_to(REPO)} reaches {found or used}"


def _call_from(path: str, function: str = "check"):
    """Run decide() from code that the interpreter believes lives in ``path``."""
    source = (
        f"def {function}():\n"
        "    return decide('needs_live_data', {'message': 'latest news?'}, fallback=False)\n"
    )
    namespace = {"decide": ncp.decide}
    exec(compile(source, str(REPO / path), "exec"), namespace)
    return namespace[function]()


@pytest.fixture
def every_decision_on(monkeypatch):
    monkeypatch.setattr(decision_settings, "get_decision_mode", lambda d: "on")


@pytest.fixture
def no_model(monkeypatch):
    """Fails the test if decide() reaches for the model or the GPU state."""
    def refuse(*args, **kwargs):
        raise AssertionError("a guard's call must not reach the model")

    for seam in ("_chat", "_resident", "gpu_busy", "_chat_model"):
        if hasattr(ncp, seam):
            monkeypatch.setattr(ncp, seam, refuse)


@pytest.mark.parametrize("path", [
    "backend/utils/path_guard.py",
    "backend/services/job_operation_gate.py",
    "backend/services/social_outreach/gates.py",
    "scripts/inbound_guard/policy.py",
])
def test_a_call_from_a_guard_is_refused(every_decision_on, no_model, path):
    result = _call_from(path)
    assert (result.answer, result.source, result.reason) == (False, "fallback", "guard_caller")


def test_only_the_named_function_of_a_partial_guard_is_refused(every_decision_on, no_model):
    assert _call_from("backend/tools/llama_code_tools.py", "verify_change").reason == "guard_caller"
    assert ncp.guard_for(str(REPO / "backend/tools/llama_code_tools.py"), "read_file") is None


def test_code_outside_the_repository_is_not_a_guard():
    assert ncp.guard_for("/nowhere/backend/utils/path_guard.py", "check") is None
