"""The read-only tier must stay read-only, and the decision gate must stay shut.

CLI_PLAN D2: the CLI never approves, releases or applies anything a person is meant to
look at — that decision happens in the Studio, where the diff or the draft is visible.
`outreach approve` is the one historical exception and it lives outside this package.

Two checks, because they catch different mistakes:

* a *static* one over the source, so a new command cannot quietly acquire an approval
  route (docstrings discuss approve/reject at length to explain their absence, which is
  why the scan skips them);
* a *behavioural* one that actually runs every read-only command and asserts the CLI
  issued no write at all.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_FORK_DIR = Path(__file__).resolve().parents[1] / "llx" / "commands" / "_fork"

_DECISION_MARKERS = ("/approve", "/reject", "/decide", "/apply", "/release-held", "/dispatch")

# Deliberate exceptions, each with a reason. Expected to stay empty.
_ALLOWED: tuple[tuple[str, str], ...] = ()


def _string_literals_outside_docstrings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstring_nodes.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]


def _fork_modules() -> list[Path]:
    if not _FORK_DIR.is_dir():  # pragma: no cover
        pytest.skip(f"no fork package at {_FORK_DIR}")
    return sorted(p for p in _FORK_DIR.glob("*.py") if not p.name.startswith("_"))


def test_no_fork_command_calls_a_decision_route():
    offenders = []
    for path in _fork_modules():
        for lineno, literal in _string_literals_outside_docstrings(path):
            if any(marker in literal for marker in _DECISION_MARKERS):
                if (path.name, literal) in _ALLOWED:
                    continue
                offenders.append(f"{path.name}:{lineno}: {literal!r}")
    assert not offenders, (
        "a fork command calls a route a person is supposed to decide in the Studio:\n  "
        + "\n  ".join(offenders)
        + "\n\nApprovals stay in the Studio (CLI_PLAN D2). If this is deliberate, add it "
        "to _ALLOWED with the reason."
    )


# Commands that must not write anything. `content page-delete` is absent on purpose:
# it is the one Phase 1 write, and it is gated behind --yes (covered elsewhere).
_READ_ONLY_INVOCATIONS = [
    ["guard", "status"],
    ["guard", "scans"],
    ["guard", "show", "1"],
    ["guard", "git", "abc123"],
    ["improve", "status"],
    ["improve", "precheck"],
    ["improve", "runs"],
    ["improve", "metrics"],
    ["improve", "pending"],
    ["system-map", "health"],
    ["system-map", "snapshot"],
    ["system-map", "findings"],
    ["content", "pages"],
    ["content", "page", "1"],
    ["content", "stats"],
    ["content", "generations"],
    ["web", "status"],
    ["connections", "list"],
    ["connections", "show", "1"],
    ["connections", "providers"],
    ["connections", "environment"],
    ["approvals", "list"],
    ["approvals", "show", "publish", "1"],
]


@pytest.mark.parametrize("args", _READ_ONLY_INVOCATIONS, ids=lambda a: " ".join(a))
def test_read_only_commands_perform_no_write(fake_backend, cli_runner, isolated_home, args):
    from llx.main import app

    fake_backend.default(json={"success": True, "data": {}})

    result = cli_runner.invoke(app, [*args, "--json"])

    assert result.exit_code == 0, result.output
    writes = [c for c in fake_backend.calls if c[0] in ("POST", "PUT", "PATCH", "DELETE")]
    assert not writes, f"{' '.join(args)} wrote: {writes}"
