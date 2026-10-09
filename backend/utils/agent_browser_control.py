"""The agent browser's control port, open only while something posts.

Outreach posters drive the agent's Firefox on the virtual display and read the
page back over WebDriver BiDi (``reddit_outreach.BIDI_PORT``). Firefox opens
that port only when scripts/agent_firefox_launch.sh starts it with
GUAARDVARK_AGENT_CDP=1, and the default is off: Google's sign-in refuses a
browser with the port open ("This browser or app may not be secure").

``agent_browser_control`` restarts the agent Firefox with the port for the
length of a post and puts it back afterwards, so sign-in is affected only while
a post is in progress. The agent Firefox is found by its profile path alone
(``agent_web_gate.agent_firefox_pids``); the person's own Firefox and another
install's agent browser are never touched.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import time
from typing import Iterator, Optional

from backend.utils.agent_display_utils import (
    AGENT_DISPLAY,
    clear_display_in_use,
    is_display_idle_blocker_active,
    mark_display_in_use,
    start_agent_display_if_needed,
)
from backend.utils.agent_web_gate import agent_firefox_pids, close_agent_firefox

logger = logging.getLogger(__name__)

# Same reason the posters give when the agent is running a task.
AGENT_BUSY = "agent_busy"
# The backend could not say whether the agent is running a task.
AGENT_STATUS_UNKNOWN = "agent_status_unknown"

# Firefox started with the port on the virtual display listens within a few
# seconds; the rest is headroom for a cold snap start.
PORT_WAIT_S = 20.0
# SIGTERM first; SIGKILL only after this (agent_web_gate.close_agent_firefox).
CLOSE_WAIT_S = 10.0
# After the kill, until the process is gone from /proc. The launcher raises an
# existing window instead of starting one while the old process is listed.
GONE_WAIT_S = 5.0
LAUNCH_TIMEOUT_S = 30.0

# What the agent desktop hands the programs it starts
# (scripts/start_agent_display.sh runs the session under ``env -i``). Nothing
# from the host session: with the host's session bus or runtime dir the
# browser could open dialogs on the person's own desktop or play sound there.
# FLASK_PORT lets the launcher's web access step find the backend.
_LAUNCH_ENV_KEYS = ("HOME", "USER", "LOGNAME", "SHELL", "PATH", "LANG", "LC_ALL",
                    "GUAARDVARK_ROOT", "FLASK_PORT")
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _control_port() -> int:
    from backend.services.social_outreach.reddit_outreach import BIDI_PORT
    return BIDI_PORT


def _port_open() -> bool:
    """The control port already answers: a Firefox someone started with it."""
    return _bidi_answers(0)[0]


def _bidi_answers(wait_s: float) -> tuple[bool, str]:
    from backend.services.social_outreach.reddit_outreach import bidi_reachable
    return bidi_reachable(wait_s=wait_s)


def _busy_reason() -> Optional[str]:
    """Why the agent browser must be left alone now, or None.

    Tasks the person starts run in the backend process, so its agent status is
    asked as well as this process's; a status that cannot be read counts as
    busy, since restarting the browser under a running task breaks it.
    """
    if is_display_idle_blocker_active():
        return AGENT_BUSY
    from backend.utils.backend_http import request_json
    try:
        body = request_json("GET", "/api/agent-control/status",
                            connect_timeout=2.0, read_timeout=5.0).body
    except Exception as e:  # noqa: BLE001 — BackendError, or a malformed URL/answer
        return f"{AGENT_STATUS_UNKNOWN}: {e}"
    status = body.get("status") if isinstance(body, dict) else None
    if not isinstance(status, dict):
        return f"{AGENT_STATUS_UNKNOWN}: the backend answered without an agent status"
    if status.get("active") or status.get("learning"):
        return AGENT_BUSY
    return None


def _launch_env(control_port: bool) -> dict:
    env = {k: os.environ[k] for k in _LAUNCH_ENV_KEYS if os.environ.get(k)}
    env.setdefault("PATH", _DEFAULT_PATH)
    # The launcher takes the display number without the colon.
    display_num = AGENT_DISPLAY.lstrip(":") or "99"
    env["GUAARDVARK_AGENT_DISPLAY"] = display_num
    runtime_dir = f"/tmp/xdg-runtime-agent-{display_num}"
    if os.path.isdir(runtime_dir):
        env["XDG_RUNTIME_DIR"] = runtime_dir
    if control_port:
        env["GUAARDVARK_AGENT_CDP"] = "1"
        env["GUAARDVARK_AGENT_CDP_PORT"] = str(_control_port())
    return env


def _launch(control_port: bool) -> bool:
    """Start the agent Firefox through its launcher; True when the launcher ran.

    The launcher backgrounds the browser and returns at once. It starts
    nothing when an agent Firefox is already listed, so callers wait for the
    old one to be gone first.
    """
    from backend.config import GUAARDVARK_ROOT as root

    script = os.path.join(root, "scripts", "agent_firefox_launch.sh")
    try:
        result = subprocess.run(
            ["bash", script],
            env=_launch_env(control_port),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=LAUNCH_TIMEOUT_S,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("agent browser launch failed (control port %s): %s",
                       "on" if control_port else "off", e)
        return False
    if result.returncode != 0:
        logger.warning("agent browser launcher exited %s (control port %s): %s",
                       result.returncode, "on" if control_port else "off",
                       (result.stderr or result.stdout or "")[-300:])
        return False
    return True


def _wait_closed(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while agent_firefox_pids():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
    return True


def _close() -> bool:
    """Close the agent Firefox, if it runs; True once it is gone."""
    if agent_firefox_pids():
        close_agent_firefox(wait_s=CLOSE_WAIT_S)
    return _wait_closed(GONE_WAIT_S)


def _restore(was_running: bool) -> None:
    """Close the browser started with the port; start it without the port if
    the person had it open before."""
    try:
        if not _close():
            logger.warning("agent browser with the control port did not exit; "
                           "the port stays open until it is closed")
            return
        if was_running and not _launch(control_port=False):
            logger.warning("agent browser was closed for a post and could not be started again")
    except Exception as e:  # noqa: BLE001 — must not mask the post's own outcome
        logger.warning("agent browser restore failed: %s", e)


@contextlib.contextmanager
def agent_browser_control() -> Iterator[Optional[str]]:
    """Hold the agent browser's control port open for the ``with`` body.

    Yields None to go ahead, or a refusal reason (``agent_busy``,
    ``agent_status_unknown: ...``) when the agent is running a task or
    training, or its state cannot be read; then nothing is touched.

    * Port already open (someone started Firefox with it): yields None and
      leaves the browser as it is afterwards.
    * Otherwise the agent Firefox, if running, is closed and started again
      with the port, and BiDi is given ``PORT_WAIT_S`` to answer. On the way
      out, also after an exception, the browser is closed and, if it was
      running before, started again without the port.

    A start that does not bring the port up still yields None: the posters'
    own checks then refuse without posting.
    """
    busy = _busy_reason()
    if busy:
        logger.info("agent browser left alone: %s", busy)
        yield busy
        return

    # The backend's idle shutdown must not stop the display under the post.
    mark_display_in_use()
    try:
        if _port_open():
            logger.info("agent browser control port already open; the browser is left as it is")
            yield None
            return

        if not start_agent_display_if_needed():
            logger.warning("agent display could not be started; the browser was not opened for the post")
            yield None
            return

        was_running = bool(agent_firefox_pids())
        if was_running and not _close():
            logger.warning("agent browser did not exit; posting without restarting it")
            yield None
            return

        try:
            if _launch(control_port=True):
                ok, why = _bidi_answers(PORT_WAIT_S)
                if ok:
                    logger.info("agent browser open with its control port for a post")
                else:
                    logger.warning("agent browser control port did not answer within %.0fs: %s "
                                   "(agent Firefox running: %s)",
                                   PORT_WAIT_S, why, bool(agent_firefox_pids()))
            yield None
        finally:
            _restore(was_running)
    finally:
        clear_display_in_use()
