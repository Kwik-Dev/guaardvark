"""The "⠋ Thinking… · 14s · esc to stop" line shown while the CLI waits on work.

``live_status_enabled`` decides whether anything animated may be drawn: never
when output is piped or JSON, on a dumb terminal, or with
GUAARDVARK_NO_SPINNER=1. ``working(label)`` wraps a blocking call (an image
render, a synchronous chat POST) in that line with elapsed time. The chat
renderer in ``llx.streaming`` draws the same line from its turn state.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from llx import output
from llx.global_opts import get_global_verbose
from llx.turn_status import format_elapsed

SPINNER_NAME = "dots"
REFRESH_PER_SECOND = 12


def live_status_enabled(console: Console, enabled: bool = True) -> bool:
    """True when a spinner and cursor movement may be written to ``console``."""
    if not enabled or output.is_json_mode():
        return False
    if os.environ.get("GUAARDVARK_NO_SPINNER", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    if not console.is_terminal or console.is_dumb_terminal:
        return False
    # FORCE_COLOR makes a piped console claim to be a terminal; a pipe still
    # must not receive cursor movement.
    isatty = getattr(console.file, "isatty", None)
    try:
        return bool(isatty and isatty())
    except (ValueError, OSError):
        return False


def verbose_status_enabled() -> bool:
    return get_global_verbose()


def write_status_to_stderr(label: str) -> None:
    """Plain status line for --verbose runs that cannot animate."""
    try:
        sys.stderr.write(f"{label}\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def status_renderable(
    spinner: Spinner,
    label: str,
    elapsed_s: float,
    hint: str = "",
    detail: str = "",
    width: int = 80,
) -> RenderableType:
    """Spinner + label + elapsed (+ hint), with an optional dimmed detail line."""
    line = Text()
    line.append(label, style="llx.brand_bright")
    line.append(f" · {format_elapsed(elapsed_s)}", style="llx.dim")
    if hint:
        line.append(f" · {hint}", style="llx.dim")
    spinner.update(text=line)
    if not detail:
        return spinner
    room = max(10, width - 4)
    if len(detail) > room:
        detail = detail[: room - 1] + "…"
    return Group(spinner, Text(f"  {detail}", style="llx.dim", no_wrap=True, overflow="ellipsis"))


class WorkingHandle:
    """Lets the wrapped block change the label while it runs."""

    def __init__(self, label: str, live: bool = False):
        self._lock = threading.Lock()
        self._label = label
        self._live = live

    @property
    def label(self) -> str:
        with self._lock:
            return self._label

    def update(self, label: str) -> None:
        with self._lock:
            self._label = label
        if not self._live and verbose_status_enabled():
            write_status_to_stderr(label)


@contextmanager
def working(
    label: str,
    *,
    console: Console | None = None,
    enabled: bool = True,
    hint: str = "",
) -> Iterator[WorkingHandle]:
    """Show an animated status line with elapsed time until the block exits."""
    if console is None:
        from llx.theme import make_console

        console = make_console()
    if not live_status_enabled(console, enabled):
        if enabled and verbose_status_enabled():
            write_status_to_stderr(label)
        yield WorkingHandle(label)
        return

    handle = WorkingHandle(label, live=True)
    started = time.monotonic()
    spinner = Spinner(SPINNER_NAME, style="llx.brand")

    def view() -> RenderableType:
        return status_renderable(
            spinner, handle.label, time.monotonic() - started, hint=hint, width=console.width,
        )

    live = Live(
        get_renderable=view,
        console=console,
        refresh_per_second=REFRESH_PER_SECOND,
        transient=True,
    )
    live.start(refresh=True)
    try:
        yield handle
    finally:
        live.stop()
