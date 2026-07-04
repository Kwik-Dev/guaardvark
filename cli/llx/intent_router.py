"""Resolve REPL input to CLI commands before falling through to chat LLM."""

from __future__ import annotations

import re
import shlex

from llx.command_catalog import COMMAND_TREE

# (pattern, command name, fixed sub-args or None to use parsed tail)
_NL_INTENT_RULES: list[tuple[re.Pattern[str], str, list[str] | None]] = [
    (re.compile(r"^(?:list|show|what are)\s+(?:the\s+)?agents?\s*$", re.I), "agents", ["list"]),
    (re.compile(r"^agents?\s+list\s*$", re.I), "agents", ["list"]),
    (re.compile(r"^(?:system\s+)?status\s*$", re.I), "status", []),
    (re.compile(r"^health(?:\s+check)?\s*$", re.I), "health", []),
    (re.compile(r"^(?:run|execute)\s+agent\s+(.+)$", re.I), "agents", None),
]


def resolve_repl_line(line: str) -> tuple[str, list[str]] | None:
    """Return (command, args) for SlashRouter, or None to use chat."""
    raw = line.strip()
    if not raw or raw.startswith("/"):
        return None

    if raw.lower().startswith("guaardvark "):
        raw = raw[len("guaardvark ") :].strip()
    if not raw:
        return None

    for pattern, cmd, fixed_args in _NL_INTENT_RULES:
        match = pattern.match(raw)
        if not match:
            continue
        if fixed_args is not None:
            return cmd, list(fixed_args)
        tail = match.group(1).strip()
        return cmd, ["run", tail]

    try:
        parts = shlex.split(raw)
    except ValueError:
        return None

    if not parts:
        return None

    cmd = parts[0].lower()
    if cmd in COMMAND_TREE:
        return cmd, parts[1:]

    return None
