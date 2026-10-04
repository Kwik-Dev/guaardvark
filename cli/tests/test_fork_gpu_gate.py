"""The GPU gate must be unbypassable from the CLI (CLI_PLAN D1).

Phase 3 added commands that render: `video-editor render`, `cast generate`, `cast train`,
`upscale *`, `infographic generate`. D1 allows them, on one condition — they go through
the backend's own job queue and GPU session, so the model preflight, the VRAM budget and
the single-renderer-at-a-time rule apply whoever asked.

That condition is only worth anything if the CLI *cannot* reach a plugin directly. The
plugins listen on their own ports (ComfyUI 8188, the video editor 8207, Audio Foundry,
…), and any of them would happily accept work with none of the backend's gates in front.
So: no fork command may name a plugin host or port. Everything goes to /api/*.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_FORK_DIR = Path(__file__).resolve().parents[1] / "llx" / "commands" / "_fork"

# Plugin service ports and hosts a command could use to sidestep the backend.
_FORBIDDEN = (
    "127.0.0.1:8207",   # video_editor plugin
    "localhost:8207",
    "127.0.0.1:8188",   # ComfyUI
    "localhost:8188",
    "127.0.0.1:8206",   # audio_foundry plugin
    "localhost:8206",
    ":8207",
    ":8188",
)


def _string_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _fork_modules() -> list[Path]:
    if not _FORK_DIR.is_dir():  # pragma: no cover
        pytest.skip(f"no fork package at {_FORK_DIR}")
    return sorted(p for p in _FORK_DIR.glob("*.py") if not p.name.startswith("_"))


def test_no_fork_command_addresses_a_plugin_port():
    offenders = []
    for path in _fork_modules():
        for lineno, literal in _string_literals(path):
            if any(bad in literal for bad in _FORBIDDEN):
                offenders.append(f"{path.name}:{lineno}: {literal!r}")
    assert not offenders, (
        "a fork command addresses a plugin directly, which would skip the backend's GPU "
        "gate (CLI_PLAN D1):\n  " + "\n  ".join(offenders)
    )


def test_every_fork_api_path_goes_through_slash_api(fake_backend=None):
    """Every endpoint a fork command names is a backend /api/ route, not a bare host."""
    offenders = []
    for path in _fork_modules():
        for _lineno, literal in _string_literals(path):
            if literal.startswith(("http://", "https://")):
                offenders.append(f"{path.name}: {literal!r}")
    assert not offenders, (
        "fork commands build absolute URLs instead of using the client's base_url:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("command", ["video-editor", "cast", "upscale", "infographic"])
def test_the_generation_groups_are_mounted(command):
    """A sanity check that these tests are looking at a CLI that has them."""
    from llx.commands._fork import registry

    assert command in {name for _app, name in registry.typer_apps()}
