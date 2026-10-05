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

# Deliberate exceptions, each with a reason.
#
# `cast.py` approves *samples* — which of the user's own generated images become the
# training set for a character. That is creative selection, not a safety gate: D2 is
# about releasing held code, publishes and outbound messages, where the decision is
# about something that leaves the machine. Without this the Cast loop cannot be driven
# from the terminal at all (generate samples, then never approve or train them).
_ALLOWED: tuple[tuple[str, str], ...] = (
    ("cast.py", "/api/cast-library/subjects"),          # base path; approve is a suffix
    ("cast.py", "/subjects"),                            # f-string suffix, see below
    # CLI_PLAN D6 — the three render gates, and the only other approval D2 permits.
    # Neither pipeline has a render route: the render IS the consequence of one of these
    # POSTs, so driving a production from the terminal means sending one. They decide what
    # to do with output the operator already owns (which storyboard frames become shots;
    # whether to spend the GPU on clips) — the `cast approve` class, not the held-code /
    # inbound-guard / publish class. All three live in `render_gates.py`, so this stays a
    # single file-wide exception rather than a rule any future module could lean on, and
    # every one of them refuses without `--yes`.
    ("render_gates.py", "/api/production/%d/casting/confirm"),
    ("render_gates.py", "/api/production/%d/storyboard/approve"),
    ("render_gates.py", "/api/music-video/%d/approve"),
)

# The approval routes in cast.py are written as f-strings built from BASE, so the
# literal scan above cannot see them; they are allowed by explicit path here, and the
# behavioural test still proves the read-only groups issue no write at all.
_ALLOWED_DECISION_PATHS = (
    "/samples/approve",
)


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
            if not any(marker in literal for marker in _DECISION_MARKERS):
                continue
            if (path.name, literal) in _ALLOWED:
                continue
            if any(allowed in literal for allowed in _ALLOWED_DECISION_PATHS):
                continue
            offenders.append(f"{path.name}:{lineno}: {literal!r}")
    assert not offenders, (
        "a fork command calls a route a person is supposed to decide in the Studio:\n  "
        + "\n  ".join(offenders)
        + "\n\nApprovals stay in the Studio (CLI_PLAN D2). If this is a creative selection"
        " like cast's sample approval rather than a safety gate, say so in _ALLOWED."
    )


def test_the_cast_sample_approval_is_the_only_approved_exception():
    """Guards the allowlist itself: a second approval appearing must be a decision."""
    holders = set()
    for path in _fork_modules():
        for _lineno, literal in _string_literals_outside_docstrings(path):
            if any(allowed in literal for allowed in _ALLOWED_DECISION_PATHS):
                holders.add(path.name)
    assert holders <= {"cast.py"}, (
        f"only cast.py may approve samples; these also do: {sorted(holders - {'cast.py'})}"
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
    ["websearch", "status"],
    ["connections", "list"],
    ["connections", "show", "1"],
    ["connections", "providers"],
    ["connections", "environment"],
    ["approvals", "list"],
    ["approvals", "show", "publish", "1"],
    # Phase 2 groups have read-only halves too; they are held to the same rule.
    ["cast", "list"],
    ["cast", "show", "1"],
    ["cast", "samples", "1"],
    ["upscale", "models"],
    ["upscale", "jobs"],
    ["upscale", "status", "j1"],
    ["infographic", "models"],
    ["infographic", "status"],
    ["infographic", "download-status"],
    # Phase 3 groups.
    ["video-editor", "health"],
    ["video-editor", "projects"],
    ["video-editor", "project", "abc"],
    ["video-editor", "jobs"],
    ["video-editor", "job", "j1"],
    ["video-editor", "filters"],
    ["video-editor", "transitions"],
    ["training", "datasets"],
    ["training", "dataset", "1"],
    ["training", "backends"],
    # Phase 4 groups (the writes in these are separately gated: llm cloud on,
    # audio model-download and wordpress process-run all require --yes).
    ["llm", "provider"],
    ["llm", "models"],
    ["audio", "models"],
    ["wordpress", "sites"],
    ["wordpress", "site", "1"],
    ["wordpress", "pages"],
    ["wordpress", "pull-status", "1"],
    # Phase 6 (D5): the generic escape hatch's own read-only half. `api request` is not
    # listed because it is general by construction — its gate is proven in
    # test_fork_api_command.py instead.
    ["api", "routes"],
    ["api", "audit"],
    # Phase 7 (D6): the read-only halves of the two pipelines. The gates those modules sit
    # beside (confirm-casting / approve-storyboard / approve) are writes by definition and
    # are covered in test_fork_render_gates.py.
    ["film-crew", "subjects", "1"],
    ["film-crew", "shots", "1"],
    ["film-crew", "templates"],
    ["music-video", "cuts", "1"],
    ["music-video", "clips", "1"],
    # `film-crew shot <id> <shot>` and `music-video storyboard` are read-only too, but they
    # need a populated payload (a shot that exists; a PNG to write), and this list runs
    # every command against one empty default. Both are covered in test_fork_render_gates.py.
]


@pytest.mark.parametrize("args", _READ_ONLY_INVOCATIONS, ids=lambda a: " ".join(a))
def test_read_only_commands_perform_no_write(fake_backend, cli_runner, isolated_home, args):
    from llx.main import app

    fake_backend.default(json={"success": True, "data": {}})

    result = cli_runner.invoke(app, [*args, "--json"])

    assert result.exit_code == 0, result.output
    writes = [c for c in fake_backend.calls if c[0] in ("POST", "PUT", "PATCH", "DELETE")]
    assert not writes, f"{' '.join(args)} wrote: {writes}"
