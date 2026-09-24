#!/usr/bin/env python3
"""
Agent Display Utilities — shared helpers for detecting, starting, and idling
the agent's virtual display (:99).

Used by app_launch, browser_navigate, agent control tools, and the UI to
determine whether to route operations to the Xvfb virtual display or the host
machine. Display starts on demand (not at boot) and shuts down after idle.
"""

import logging
import os
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

AGENT_DISPLAY = os.environ.get("GUAARDVARK_AGENT_DISPLAY", ":99")
GUAARDVARK_ROOT = os.environ.get("GUAARDVARK_ROOT", "")
DEFAULT_IDLE_SHUTDOWN_SECONDS = 300

_idle_lock = threading.Lock()
_idle_deadline: Optional[float] = None
_idle_timer: Optional[threading.Timer] = None


def _display_num() -> int:
    return int(AGENT_DISPLAY.lstrip(":") or "99")


def probe_display_socket(display_num: Optional[int] = None) -> bool:
    """True if /tmp/.X11-unix/X<n> exists — the cheap way to know Xvfb is up."""
    num = display_num if display_num is not None else _display_num()
    return os.path.exists(f"/tmp/.X11-unix/X{num}")


def is_agent_display_active() -> bool:
    """Check if the Xvfb virtual display is running."""
    display_num = AGENT_DISPLAY.lstrip(":")
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"Xvfb :{display_num}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def run_display_script(action: str, timeout: int = 60) -> dict:
    """Shell out to scripts/start_agent_display.sh <action>."""
    from backend.config import GUAARDVARK_ROOT as root

    script = os.path.join(root, "scripts", "start_agent_display.sh")
    if not os.path.exists(script):
        return {
            "success": False,
            "error": f"start_agent_display.sh missing at {script} — re-pull the repo",
            "returncode": -1,
        }

    try:
        result = subprocess.run(
            ["bash", script, action],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"{action} timed out after {timeout}s",
            "returncode": -1,
        }

    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-500:],
        "stderr_tail": (result.stderr or "")[-500:],
    }


def is_display_idle_blocker_active() -> bool:
    """True when the display must not be torn down (agent task or training)."""
    try:
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        if service.is_active:
            return True
        if service.is_learning:
            return True
    except Exception as e:
        logger.debug("Could not probe agent control service for idle blockers: %s", e)
    return False


def cancel_display_idle_shutdown() -> None:
    """Cancel a pending idle shutdown (viewer reopened, display started, etc.)."""
    global _idle_deadline, _idle_timer
    with _idle_lock:
        _idle_deadline = None
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None


def maybe_stop_idle_display() -> bool:
    """Stop the display if idle deadline passed and no blockers remain."""
    with _idle_lock:
        if _idle_deadline is None:
            return False
        if time.time() < _idle_deadline:
            return False

    if is_display_idle_blocker_active():
        logger.info("Idle display shutdown deferred — agent task or training active")
        return False

    if not probe_display_socket():
        cancel_display_idle_shutdown()
        return True

    logger.info("Idle timeout reached — stopping agent virtual display")
    result = run_display_script("stop", timeout=30)
    running = probe_display_socket()
    cancel_display_idle_shutdown()
    if running:
        logger.warning(
            "Idle stop ran but display socket still alive (exit %s)",
            result.get("returncode"),
        )
        return False
    return True


def _idle_timer_callback() -> None:
    try:
        maybe_stop_idle_display()
    except Exception as e:
        logger.error("Idle display shutdown callback failed: %s", e, exc_info=True)


def arm_display_idle_shutdown(seconds: int = DEFAULT_IDLE_SHUTDOWN_SECONDS) -> float:
    """Arm idle shutdown after ``seconds``. Returns the deadline timestamp."""
    global _idle_deadline, _idle_timer
    deadline = time.time() + seconds
    with _idle_lock:
        _idle_deadline = deadline
        if _idle_timer is not None:
            _idle_timer.cancel()
        _idle_timer = threading.Timer(seconds, _idle_timer_callback)
        _idle_timer.daemon = True
        _idle_timer.start()
    logger.info("Agent display idle shutdown armed in %ss", seconds)
    return deadline


def get_idle_shutdown_deadline() -> Optional[float]:
    with _idle_lock:
        return _idle_deadline


def start_agent_display_if_needed() -> bool:
    """Start Xvfb + x11vnc if not already running. Idempotent."""
    cancel_display_idle_shutdown()

    if probe_display_socket() or is_agent_display_active():
        return True

    logger.info("Starting agent virtual display on demand")
    result = run_display_script("start", timeout=60)
    running = probe_display_socket()
    if running:
        return True

    logger.error(
        "Failed to start agent display (exit %s): %s",
        result.get("returncode"),
        result.get("stderr_tail") or result.get("error"),
    )
    return False


def stop_agent_display(force: bool = False) -> dict:
    """Stop the agent display. Returns structured result dict."""
    if not force and is_display_idle_blocker_active():
        return {
            "success": False,
            "blocked": True,
            "error": "Agent task or training is active — stop when idle or use force",
            "display_running": probe_display_socket(),
        }

    cancel_display_idle_shutdown()
    result = run_display_script("stop", timeout=30)
    result["display_running"] = probe_display_socket()
    result["success"] = not result["display_running"]
    return result


def is_firefox_on_agent_display() -> bool:
    """Check if Firefox has a window on the agent display."""
    env = {**os.environ, "DISPLAY": AGENT_DISPLAY}
    try:
        result = subprocess.run(
            ["xdotool", "search", "--name", "Mozilla Firefox"],
            env=env, capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except Exception:
        return False


def get_agent_display_env() -> dict:
    """Return environment dict for subprocess targeting the agent display."""
    return {
        **os.environ,
        "DISPLAY": AGENT_DISPLAY,
        "MOZ_ENABLE_WAYLAND": "0",
        "GDK_BACKEND": "x11",
    }


def get_firefox_profile_path() -> str:
    """Return the path to the agent's persistent Firefox profile."""
    root = GUAARDVARK_ROOT or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "agent", "firefox_profile")


def wait_for_firefox_on_display(timeout: float = 8.0, poll_interval: float = 0.5) -> bool:
    """Poll until Firefox appears on the agent display, or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if is_firefox_on_agent_display():
            return True
        time.sleep(poll_interval)
    return False
