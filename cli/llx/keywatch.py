"""Esc stops a running chat reply, the same as Ctrl+C.

While a reply streams, the terminal is put in cbreak mode with echo off and a
thread watches stdin for a lone Esc byte. Arrow and function keys also start
with Esc but arrive as one multi-byte read, so they are not taken for it.
Ctrl+C keeps working because signal keys stay enabled. Anything else typed
meanwhile is kept in ``type_ahead`` for the next prompt instead of being lost.

Only POSIX terminals are watched; elsewhere, or when stdin or stdout is not a
terminal, the watcher does nothing and Ctrl+C remains the way to stop.
"""

from __future__ import annotations

import os
import re
import select
import sys
import threading
from contextlib import contextmanager

try:
    import termios
except ImportError:  # Windows
    termios = None

_ESC = b"\x1b"
# A lone Esc may be the first byte of a sequence split across reads; this is
# how long to wait for the rest before calling it a key press.
_SEQUENCE_GAP = 0.03
_ESCAPE_SEQUENCE = re.compile(r"\x1b(\[[0-9;?]*[ -/]*[@-~]|O.|.)?", re.DOTALL)


def available() -> bool:
    """True when Esc can be read from this terminal."""
    if termios is None:
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _clean_type_ahead(raw: str) -> str:
    """Apply backspaces and drop control keys so the text reads as typed."""
    text: list[str] = []
    for ch in _ESCAPE_SEQUENCE.sub("", raw):
        if ch in ("\x7f", "\b"):
            if text:
                text.pop()
        elif ch == "\x15":  # Ctrl+U clears the line
            text.clear()
        elif ch in ("\r", "\n", "\t"):
            text.append(" ")
        elif ch >= " ":
            text.append(ch)
    return " ".join("".join(text).split())


class EscWatch:
    """Context manager that sets ``pressed`` when Esc is hit."""

    def __init__(self):
        self.pressed = threading.Event()
        self.type_ahead = ""
        self._raw = bytearray()
        self._fd: int | None = None
        self._saved = None
        self._cbreak = None
        self._thread: threading.Thread | None = None
        self._reading = threading.Event()
        self._closed = threading.Event()
        # Held by the reader for each select/read, so pausing waits for one in flight.
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._thread is not None

    def __enter__(self) -> "EscWatch":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        if self.active or not available():
            return
        try:
            fd = sys.stdin.fileno()
            saved = termios.tcgetattr(fd)
        except (termios.error, OSError, ValueError):
            return
        cbreak = termios.tcgetattr(fd)
        cbreak[3] &= ~(termios.ECHO | termios.ICANON)
        cbreak[6][termios.VMIN] = 1
        cbreak[6][termios.VTIME] = 0
        try:
            termios.tcsetattr(fd, termios.TCSANOW, cbreak)
        except termios.error:
            return
        self._fd, self._saved, self._cbreak = fd, saved, cbreak
        self._reading.set()
        self._thread = threading.Thread(target=self._run, name="esc-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.active:
            return
        self._reading.clear()
        self._closed.set()
        self._thread.join(timeout=0.5)
        self._thread = None
        self._restore()
        self.type_ahead = _clean_type_ahead(bytes(self._raw).decode("utf-8", errors="ignore"))

    @contextmanager
    def paused(self):
        """Hand the terminal back for a prompt (an approval question), then resume."""
        if not self.active:
            yield
            return
        self._reading.clear()
        with self._lock:
            self._restore()
        try:
            yield
        finally:
            with self._lock:
                try:
                    termios.tcsetattr(self._fd, termios.TCSANOW, self._cbreak)
                except termios.error:
                    pass
            self._reading.set()

    def _restore(self) -> None:
        try:
            termios.tcsetattr(self._fd, termios.TCSANOW, self._saved)
        except termios.error:
            pass

    def _readable(self, timeout: float) -> bool:
        ready, _, _ = select.select([self._fd], [], [], timeout)
        return bool(ready)

    def _run(self) -> None:
        while not self._closed.is_set():
            if not self._reading.wait(0.05):
                continue
            with self._lock:
                if not self._reading.is_set():
                    continue
                try:
                    if not self._readable(0.05):
                        continue
                    data = os.read(self._fd, 1024)
                    if data.endswith(_ESC) and self._readable(_SEQUENCE_GAP):
                        data += os.read(self._fd, 1024)
                except OSError:
                    return
            if not data:
                return
            # Nothing followed the trailing Esc within the gap: it was the key.
            if data.endswith(_ESC):
                self._raw += data.rstrip(_ESC)
                self.pressed.set()
            else:
                self._raw += data
