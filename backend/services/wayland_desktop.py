"""Client for the Wayland desktop bridge (wayland_bridge_server.py).

On a Wayland session the X11 tools pyautogui and xdotool capture a black
screen and reach only XWayland windows, so desktop control goes through the
bridge, which holds an XDG desktop portal session for the whole desktop. One
bridge serves every process of this install: the backend and the MCP server
both connect to the same socket, and whichever needs it first starts it.

The first start shows the desktop's own permission dialog, so a call can wait
for a person; later starts reuse the saved restore token and do not ask.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import time
from pathlib import Path

BRIDGE = Path(__file__).with_name("wayland_bridge_server.py")
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "desktop_bridge"
# The bridge needs python3-dbus, python3-gi and GStreamer, which the system
# Python has and a virtualenv usually cannot see.
SYSTEM_PYTHON = os.environ.get("GUAARDVARK_WAYLAND_PYTHON", "/usr/bin/python3")
# How long the person has to answer the desktop's permission dialog.
START_TIMEOUT_S = 180.0
# The session closes after this long without a request, which also clears the
# desktop's "being controlled" indicator.
IDLE_S = float(os.environ.get("GUAARDVARK_WAYLAND_BRIDGE_IDLE", "600"))


class BridgeError(RuntimeError):
    pass


def is_wayland_session() -> bool:
    return (os.environ.get("XDG_SESSION_TYPE") == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY")))


def _runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def socket_path() -> Path:
    return _runtime_dir() / "guaardvark-desktop-bridge.sock"


def _connect(timeout: float) -> socket.socket | None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(socket_path()))
        return sock
    except OSError:
        sock.close()
        return None


def _start() -> None:
    """Start the bridge and wait until it serves, or report why it stopped."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = DATA_DIR / "bridge.log"
    with open(log, "ab") as out:
        proc = subprocess.Popen(
            [SYSTEM_PYTHON, str(BRIDGE), "--socket", str(socket_path()),
             "--token-file", str(DATA_DIR / "restore_token"), "--idle-seconds", str(IDLE_S)],
            stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        sock = _connect(1.0)
        if sock is not None:
            sock.close()
            return
        if proc.poll() is not None:
            tail = log.read_text(errors="replace").strip().splitlines()[-1:] or ["no output"]
            raise BridgeError(f"the desktop bridge stopped: {tail[0]}")
        time.sleep(0.5)
    proc.terminate()
    raise BridgeError("the desktop's permission dialog was not answered in time")


@contextlib.contextmanager
def _start_lock():
    import fcntl
    lock = open(_runtime_dir() / "guaardvark-desktop-bridge.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
    finally:
        lock.close()


def call(op: str, *, timeout: float = 60.0, **params) -> dict:
    """Send one request to the bridge, starting it first if no one has."""
    sock = _connect(timeout)
    if sock is None:
        with _start_lock():
            sock = _connect(timeout)          # another process may have started it
            if sock is None:
                _start()
                sock = _connect(timeout)
    if sock is None:
        raise BridgeError("the desktop bridge is not answering")
    with sock, sock.makefile("rwb") as fh:
        fh.write((json.dumps({"op": op, **params}) + "\n").encode())
        fh.flush()
        line = fh.readline()
    if not line:
        raise BridgeError("the desktop bridge closed the connection")
    reply = json.loads(line)
    if not reply.pop("ok", False):
        raise BridgeError(reply.get("error") or "the desktop bridge refused the request")
    return reply
