"""Unit tests for on-demand agent display startup and idle shutdown."""

import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ADU_PATH = Path(__file__).resolve().parents[1] / "utils" / "agent_display_utils.py"


def _load_adu():
    """Load agent_display_utils without importing backend.utils package __init__."""
    name = "backend.utils.agent_display_utils"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _ADU_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def reset_idle_state():
    adu = _load_adu()
    adu.cancel_display_idle_shutdown()
    yield
    adu.cancel_display_idle_shutdown()


class TestStartAgentDisplayIfNeeded:
    def test_starts_when_down(self):
        adu = _load_adu()
        with patch.object(adu, "run_display_script") as run_script, patch.object(
            adu, "is_agent_display_active", return_value=False
        ), patch.object(adu, "probe_display_socket", side_effect=[False, True]):
            run_script.return_value = {"success": True, "returncode": 0}
            assert adu.start_agent_display_if_needed() is True
            run_script.assert_called_once_with("start", timeout=60)

    def test_idempotent_when_up(self):
        adu = _load_adu()
        with patch.object(adu, "run_display_script") as run_script, patch.object(
            adu, "probe_display_socket", return_value=True
        ):
            assert adu.start_agent_display_if_needed() is True
            run_script.assert_not_called()


class TestIdleShutdown:
    def test_maybe_stop_when_deadline_passed(self):
        adu = _load_adu()
        with patch.object(adu, "run_display_script") as run_script, patch.object(
            adu, "probe_display_socket", side_effect=[True, False]
        ), patch.object(adu, "is_display_idle_blocker_active", return_value=False):
            adu._idle_deadline = time.time() - 1
            assert adu.maybe_stop_idle_display() is True
            run_script.assert_called_once_with("stop", timeout=30)

    def test_deferred_when_agent_active(self):
        adu = _load_adu()
        with patch.object(adu, "run_display_script") as run_script, patch.object(
            adu, "is_display_idle_blocker_active", return_value=True
        ):
            adu._idle_deadline = time.time() - 1
            assert adu.maybe_stop_idle_display() is False
            run_script.assert_not_called()

    def test_arm_and_disarm(self):
        adu = _load_adu()
        with patch.object(adu.threading, "Timer") as timer_cls:
            timer = MagicMock()
            timer_cls.return_value = timer
            deadline = adu.arm_display_idle_shutdown(seconds=300)
            assert deadline > time.time()
            timer.start.assert_called_once()
            adu.cancel_display_idle_shutdown()
            timer.cancel.assert_called_once()
            assert adu.get_idle_shutdown_deadline() is None

    def test_start_cancels_idle(self):
        adu = _load_adu()
        with patch.object(adu.threading, "Timer") as timer_cls, patch.object(
            adu, "run_display_script", return_value={"success": True}
        ), patch.object(adu, "probe_display_socket", return_value=True):
            timer = MagicMock()
            timer_cls.return_value = timer
            adu.arm_display_idle_shutdown(seconds=60)
            adu.start_agent_display_if_needed()
            assert adu.get_idle_shutdown_deadline() is None


class TestStopAgentDisplay:
    def test_blocked_without_force(self):
        adu = _load_adu()
        with patch.object(adu, "run_display_script") as run_script, patch.object(
            adu, "probe_display_socket", return_value=False
        ), patch.object(adu, "is_display_idle_blocker_active", return_value=True):
            result = adu.stop_agent_display(force=False)
            assert result["blocked"] is True
            run_script.assert_not_called()

    def test_force_stops_despite_blocker(self):
        adu = _load_adu()
        with patch.object(adu, "run_display_script") as run_script, patch.object(
            adu, "probe_display_socket", return_value=False
        ), patch.object(adu, "is_display_idle_blocker_active", return_value=True):
            run_script.return_value = {"success": True, "returncode": 0}
            result = adu.stop_agent_display(force=True)
            assert result["success"] is True
            run_script.assert_called_once_with("stop", timeout=30)
