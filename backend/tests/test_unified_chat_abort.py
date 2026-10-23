"""Tests for unified chat hard abort and in-flight slot release."""

import threading
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask


@pytest.fixture
def app_ctx():
    app = Flask("test_unified_chat_abort")
    with app.app_context():
        yield app


@pytest.fixture(autouse=True)
def _clear_inflight():
    from backend.api import unified_chat_api as api

    with api._inflight_lock:
        api._inflight.clear()
        api._released_by_abort.clear()
    yield
    with api._inflight_lock:
        api._inflight.clear()
        api._released_by_abort.clear()


def test_abort_clears_inflight_slot(app_ctx):
    from backend.api import unified_chat_api as api

    session_id = "abort-test-session"
    blocker = threading.Event()

    def _wedge():
        blocker.wait(timeout=30)

    thread = threading.Thread(target=_wedge, daemon=True)
    with api._inflight_lock:
        api._inflight[session_id] = thread
    thread.start()

    with patch("backend.services.unified_chat_engine.set_abort_flag") as mock_flag, \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_svc, \
         patch("backend.socketio_instance.socketio") as mock_sio:
        mock_svc.return_value = MagicMock(_active=False)
        result = api.abort_chat(session_id)

    blocker.set()
    assert result.get_json()["success"] is True
    assert result.get_json()["inflight_cleared"] is True
    mock_flag.assert_called_once_with(session_id)
    mock_sio.emit.assert_called_once_with(
        "chat:aborted", {"session_id": session_id}, room=session_id
    )
    # The thread is still running, so it keeps the slot until it exits; the
    # next turn may replace it (see test_inflight_cleared_allows_new_claim).
    with api._inflight_lock:
        assert api._inflight.get(session_id) is thread
        assert api._released_by_abort.get(session_id) is thread


def test_abort_drops_a_finished_thread_from_the_slot(app_ctx):
    from backend.api import unified_chat_api as api

    session_id = "abort-finished-session"
    done = threading.Thread(target=lambda: None, daemon=True)
    done.start()
    done.join()
    with api._inflight_lock:
        api._inflight[session_id] = done
    with patch("backend.services.unified_chat_engine.set_abort_flag"), \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_svc, \
         patch("backend.socketio_instance.socketio"):
        mock_svc.return_value = MagicMock(_active=False)
        assert api.abort_chat(session_id).get_json()["inflight_cleared"] is True
    with api._inflight_lock:
        assert session_id not in api._inflight


def test_abort_when_no_inflight_still_succeeds(app_ctx):
    from backend.api import unified_chat_api as api

    session_id = "empty-abort-session"
    with patch("backend.services.unified_chat_engine.set_abort_flag"), \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_svc, \
         patch("backend.socketio_instance.socketio"):
        mock_svc.return_value = MagicMock(_active=False)
        result = api.abort_chat(session_id)

    assert result.get_json()["success"] is True
    assert result.get_json()["inflight_cleared"] is False


def test_inflight_cleared_allows_new_claim(app_ctx):
    """After hard abort, a new thread can claim the same session slot."""
    from backend.api import unified_chat_api as api

    session_id = "reclaim-session"
    blocker = threading.Event()

    def _wedge():
        blocker.wait(timeout=30)

    old = threading.Thread(target=_wedge, daemon=True)
    with api._inflight_lock:
        api._inflight[session_id] = old
    old.start()

    with patch("backend.services.unified_chat_engine.set_abort_flag"), \
         patch("backend.services.agent_control_service.get_agent_control_service") as mock_svc, \
         patch("backend.socketio_instance.socketio"):
        mock_svc.return_value = MagicMock(_active=False)
        api.abort_chat(session_id)

    ran = threading.Event()
    with patch.object(api, "INFLIGHT_GRACE_S", 0.1):
        new = api.start_turn_thread(session_id, ran.set, "reclaim")
    assert new is not None and new is not old
    assert ran.wait(2)

    blocker.set()
    old.join(2)
    new.join(2)


def test_a_live_turn_that_was_not_aborted_keeps_the_slot(app_ctx):
    from backend.api import unified_chat_api as api

    session_id = "busy-session"
    blocker = threading.Event()
    with patch.object(api, "INFLIGHT_GRACE_S", 0.1):
        first = api.start_turn_thread(session_id, lambda: blocker.wait(30), "first")
        second = api.start_turn_thread(session_id, lambda: None, "second")
    assert first is not None
    assert second is None
    blocker.set()
    first.join(2)


def test_a_new_turn_waits_for_the_previous_one_to_exit(app_ctx):
    """Two turns on one session never overlap: the second starts after the first ends."""
    import time
    from backend.api import unified_chat_api as api

    session_id = "serial-session"
    spans = {}

    def turn(name, hold):
        def run():
            spans[name] = [time.monotonic(), None]
            time.sleep(hold)
            spans[name][1] = time.monotonic()
        return run

    first = api.start_turn_thread(session_id, turn("first", 0.3), "first")
    second = api.start_turn_thread(session_id, turn("second", 0), "second")
    assert first is not None and second is not None
    second.join(2)
    assert spans["second"][0] >= spans["first"][1]
    with api._inflight_lock:
        assert session_id not in api._inflight
