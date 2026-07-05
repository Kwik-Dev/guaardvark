"""Slash-command tab completion for the Guaardvark REPL.

Provides nested completion for ``/command subcommand`` patterns, wrapped in
a ``FuzzyCompleter`` so partial and out-of-order keystrokes still match.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.completion.fuzzy_completer import FuzzyCompleter
from prompt_toolkit.document import Document

from llx.command_catalog import COMMAND_META, COMMAND_TREE


def _get_meta(command: str) -> str:
    """Return a short description for a top-level command."""
    return COMMAND_META.get(command, "")


# ---------------------------------------------------------------------------
# Completer
# ---------------------------------------------------------------------------

class SlashCompleter(Completer):
    """Tab-completion for ``/command [subcommand]`` input.

    Parameters
    ----------
    get_dynamic_completions:
        Optional callback ``(command, sub_text) -> list[str] | None``.
        When provided, the completer calls it after exhausting static
        subcommands.  If it returns a list, those strings are yielded as
        additional completions.
    """

    def __init__(
        self,
        get_dynamic_completions: Optional[
            Callable[[str, str], Optional[List[str]]]
        ] = None,
    ) -> None:
        self.get_dynamic_completions = get_dynamic_completions

    # ---- prompt_toolkit interface ----------------------------------------

    def get_completions(self, document: Document, complete_event):  # noqa: D401
        """Yield ``Completion`` objects for the current input."""
        text = document.text_before_cursor

        # Only activate when the line starts with "/"
        if not text.startswith("/"):
            return

        stripped = text[1:]  # drop the leading "/"

        if " " not in stripped:
            # Still typing the command name — complete top-level commands.
            prefix = stripped.lower()
            for cmd in COMMAND_TREE:
                if cmd.startswith(prefix):
                    yield Completion(
                        cmd,
                        start_position=-len(prefix),
                        display_meta=_get_meta(cmd),
                    )
            return

        # A space exists — split into command + remainder.
        cmd, _, rest = stripped.partition(" ")
        cmd = cmd.lower()
        sub_prefix = rest.lstrip().lower()

        # Static subcommands
        if cmd in COMMAND_TREE:
            for sub in COMMAND_TREE[cmd]:
                if sub.startswith(sub_prefix):
                    yield Completion(
                        sub,
                        start_position=-len(sub_prefix) if sub_prefix else 0,
                    )

        # Dynamic completions (plugin-provided, live data, etc.)
        if self.get_dynamic_completions is not None:
            dynamic = self.get_dynamic_completions(cmd, rest)
            if dynamic:
                for item in dynamic:
                    if item.lower().startswith(sub_prefix):
                        yield Completion(
                            item,
                            start_position=-len(sub_prefix) if sub_prefix else 0,
                        )

        # Local path completion for coding commands
        if cmd in {"ls", "read", "grep", "edit", "cd", "diff", "run"}:
            try:
                from pathlib import Path
                base = Path.cwd()
                prefix_path = Path(sub_prefix or ".")
                # simple: list siblings of the dir being typed
                search_dir = (base / prefix_path).parent if not (base / prefix_path).is_dir() else (base / prefix_path)
                search_dir = search_dir.resolve()
                if search_dir.exists() and search_dir.is_dir():
                    for entry in sorted(search_dir.iterdir(), key=lambda p: p.name):
                        if entry.name.startswith(".") and not sub_prefix.startswith("."):
                            continue
                        name = entry.name + ("/" if entry.is_dir() else "")
                        if name.lower().startswith(sub_prefix.lower().rsplit("/", 1)[-1] if "/" in sub_prefix else sub_prefix.lower()):
                            yield Completion(
                                name,
                                start_position=-len(sub_prefix.rsplit("/", 1)[-1]) if "/" in sub_prefix else -len(sub_prefix),
                            )
            except Exception:
                pass

        # Dynamic backend tool name completion for /tool and /tools
        if cmd in {"tool", "tools"}:
            try:
                # Try to get cached tool names from state if slash router put them there
                # Fallback to a few common high-value ones
                tool_names = None
                # We can't easily reach router state here; provide useful defaults + common ones
                common_tools = ["read_code", "edit_code", "search_code", "list_files", "execute_python",
                                "grep_search", "save_memory", "search_memory", "list_code_files",
                                "get_repository_map", "verify_change", "agent_task_execute"]
                for name in common_tools:
                    if name.lower().startswith(sub_prefix.lower()):
                        yield Completion(name, start_position=-len(sub_prefix) if sub_prefix else 0)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_completer(
    get_dynamic: Optional[Callable[[str, str], Optional[List[str]]]] = None,
) -> FuzzyCompleter:
    """Return a ``FuzzyCompleter``-wrapped ``SlashCompleter``."""
    return FuzzyCompleter(SlashCompleter(get_dynamic), enable_fuzzy=True)
