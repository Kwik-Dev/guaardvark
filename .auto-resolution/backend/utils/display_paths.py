"""Paths as they are safe to show on a shared screen.

CLI output and tool results end up in screenshots, recordings, issues and MCP
clients. An absolute path names the user's home directory and the folder the
checkout lives in, so anything that prints one routes it through here first:
inside the checkout it becomes relative, under the home directory it starts
with ``~``.

Paths are normalised without following symlinks: a venv's ``python`` links to
the system interpreter, and resolving it would move it outside the checkout.
"""
from __future__ import annotations

import os
from pathlib import Path


def _root() -> Path:
    env = os.environ.get("GUAARDVARK_ROOT")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def _bases() -> list[tuple[Path, str]]:
    """The checkout, then the home directory, each as written and as resolved."""
    out: list[tuple[Path, str]] = []
    for base, prefix in ((_root(), ""), (Path.home(), "~")):
        for variant in (Path(os.path.abspath(base)), base.resolve()):
            if (variant, prefix) not in out:
                out.append((variant, prefix))
    return out


def display_path(value) -> str:
    """``logs/backend.log`` for a path inside the checkout, ``~/.cursor/mcp.json``
    for one under the home directory, anything else unchanged."""
    p = Path(os.path.abspath(Path(str(value)).expanduser()))
    for base, prefix in _bases():
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        if rel.as_posix() == ".":
            return prefix or "."
        return f"{prefix}/{rel.as_posix()}" if prefix else rel.as_posix()
    return str(value)


def display_text(text: str) -> str:
    """Replace the checkout root with ``.`` and the home directory with ``~``
    wherever they occur in a command line or message."""
    out = str(text)
    pairs = [(str(base), "." if not prefix else "~") for base, prefix in _bases()]
    for base, repl in sorted(pairs, key=lambda pair: -len(pair[0])):
        if base and base != "/":
            out = out.replace(base, repl)
    return out
