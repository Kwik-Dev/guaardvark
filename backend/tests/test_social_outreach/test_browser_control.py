"""The agent browser's control port is open only while the posting tick posts.

agent_browser_control runs against a stand-in for the process table, the
launcher and the port; nothing here lists, closes or starts a real Firefox.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import types

import pytest

from backend.models import SocialOutreachLog, db
from backend.services.social_outreach import kill_switch
from backend.tasks import social_outreach_tasks as tasks
from backend.utils import agent_browser_control as control
from backend.utils import agent_web_gate
from backend.utils.backend_http import BackendError, BackendResponse

# conftest.py replaces the module attribute with a no-op for every test; this
# is the real one, taken at import.
real_agent_browser_control = control.agent_browser_control


class Browser:
    """The agent Firefox and its control port, as the helper sees them."""

    def __init__(self, running=False, port_open=False, answers=True, exits=True):
        self.running = running
        self.with_port = False
        self.port_open = port_open
        self.answers = answers
        self.exits = exits
        self.events: list[str] = []

    def pids(self):
        return [4242] if self.running else []

    def close(self, wait_s):
        self.events.append("close")
        if self.exits:
            self.running = self.with_port = False
        return 1

    def launch(self, control_port):
        self.events.append("launch with port" if control_port else "launch without port")
        if not self.running:
            self.running, self.with_port = True, control_port
        return True

    def bidi(self, wait_s):
        self.events.append("wait for port")
        ok = self.with_port and self.answers
        return ok, "" if ok else "connect failed: refused"


@pytest.fixture
def browser(monkeypatch):
    b = Browser()
    monkeypatch.setattr(control, "_busy_reason", lambda: None)
    monkeypatch.setattr(control, "_port_open", lambda: b.port_open)
    monkeypatch.setattr(control, "start_agent_display_if_needed", lambda: True)
    monkeypatch.setattr(control, "agent_firefox_pids", b.pids)
    monkeypatch.setattr(control, "close_agent_firefox", b.close)
    monkeypatch.setattr(control, "_launch", b.launch)
    monkeypatch.setattr(control, "_bidi_answers", b.bidi)
    monkeypatch.setattr(control, "GONE_WAIT_S", 0.0)
    b.marks = []
    monkeypatch.setattr(control, "mark_display_in_use", lambda: b.marks.append("mark"))
    monkeypatch.setattr(control, "clear_display_in_use", lambda: b.marks.append("clear"))
    return b


def _post(b: Browser) -> None:
    b.events.append("post")


def test_a_running_browser_is_restarted_with_the_port_and_put_back(browser):
    browser.running = True

    with real_agent_browser_control() as refusal:
        assert refusal is None
        assert browser.with_port
        _post(browser)

    assert browser.events == [
        "close", "launch with port", "wait for port", "post", "close", "launch without port",
    ]
    assert browser.marks == ["mark", "clear"]
    assert browser.running and not browser.with_port


def test_a_closed_browser_is_opened_for_the_post_and_closed_after(browser):
    with real_agent_browser_control() as refusal:
        assert refusal is None
        _post(browser)

    assert browser.events == ["launch with port", "wait for port", "post", "close"]
    assert not browser.running


def test_the_browser_is_put_back_when_the_post_raises(browser):
    browser.running = True

    with pytest.raises(RuntimeError):
        with real_agent_browser_control():
            _post(browser)
            raise RuntimeError("servo crashed")

    assert browser.events[-3:] == ["post", "close", "launch without port"]
    assert browser.marks == ["mark", "clear"]
    assert browser.running and not browser.with_port


def test_a_port_someone_else_opened_is_left_alone(browser):
    browser.running = browser.port_open = True

    with real_agent_browser_control() as refusal:
        assert refusal is None
        _post(browser)

    assert browser.events == ["post"]


@pytest.mark.parametrize("port_open", [False, True])
def test_a_busy_agent_is_refused_and_its_browser_untouched(browser, monkeypatch, port_open):
    browser.running, browser.port_open = True, port_open
    monkeypatch.setattr(control, "_busy_reason", lambda: control.AGENT_BUSY)

    with real_agent_browser_control() as refusal:
        assert refusal == "agent_busy"

    assert browser.events == []
    assert browser.running


def test_a_port_that_never_answers_still_puts_the_browser_back(browser):
    browser.running, browser.answers = True, False

    with real_agent_browser_control() as refusal:
        # The posters' own checks refuse without posting.
        assert refusal is None
        _post(browser)

    assert browser.events == [
        "close", "launch with port", "wait for port", "post", "close", "launch without port",
    ]
    assert browser.marks == ["mark", "clear"]


def test_a_browser_that_will_not_exit_is_not_launched_over(browser):
    browser.running, browser.exits = True, False

    with real_agent_browser_control() as refusal:
        assert refusal is None
        _post(browser)

    assert browser.events == ["close", "post"]


def test_no_launch_without_the_display(browser, monkeypatch):
    monkeypatch.setattr(control, "start_agent_display_if_needed", lambda: False)

    with real_agent_browser_control() as refusal:
        assert refusal is None
        _post(browser)

    assert browser.events == ["post"]


# --- whether the agent is busy ---------------------------------------------


def _backend_status(monkeypatch, status=None, error=None):
    calls = []

    def request_json(method, path, **kwargs):
        calls.append((method, path))
        if error is not None:
            raise error
        return BackendResponse(status=200, body={"success": True, "status": status}, data=None)

    monkeypatch.setattr("backend.utils.backend_http.request_json", request_json)
    return calls


def test_a_task_in_this_process_is_busy_without_asking_the_backend(monkeypatch):
    monkeypatch.setattr(control, "is_display_idle_blocker_active", lambda: True)
    calls = _backend_status(monkeypatch, status={"active": False, "learning": False})

    assert control._busy_reason() == "agent_busy"
    assert calls == []


@pytest.mark.parametrize("status, expected", [
    ({"active": True, "learning": False}, "agent_busy"),
    ({"active": False, "learning": True}, "agent_busy"),
    ({"active": False, "learning": False}, None),
])
def test_the_backend_agent_status_decides(monkeypatch, status, expected):
    monkeypatch.setattr(control, "is_display_idle_blocker_active", lambda: False)
    calls = _backend_status(monkeypatch, status=status)

    assert control._busy_reason() == expected
    assert calls == [("GET", "/api/agent-control/status")]


def test_an_unreadable_agent_status_counts_as_busy(monkeypatch):
    monkeypatch.setattr(control, "is_display_idle_blocker_active", lambda: False)
    _backend_status(monkeypatch, error=BackendError("unreachable", "not answering"))

    assert control._busy_reason().startswith("agent_status_unknown: ")


# --- the launch ------------------------------------------------------------


def test_the_launch_environment_carries_the_port_only_when_asked(monkeypatch):
    monkeypatch.setattr(control, "AGENT_DISPLAY", ":99")
    monkeypatch.setenv("GUAARDVARK_AGENT_CDP", "1")
    monkeypatch.setenv("GUAARDVARK_AGENT_CDP_PORT", "9333")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")

    with_port = control._launch_env(control_port=True)
    without = control._launch_env(control_port=False)

    assert with_port["GUAARDVARK_AGENT_CDP"] == "1"
    assert with_port["GUAARDVARK_AGENT_CDP_PORT"] == "9222"
    assert "GUAARDVARK_AGENT_CDP" not in without
    assert "GUAARDVARK_AGENT_CDP_PORT" not in without
    for env in (with_port, without):
        assert env["GUAARDVARK_AGENT_DISPLAY"] == "99"
        for host_only in ("DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "DATABASE_URL"):
            assert host_only not in env


def test_the_launcher_script_runs_in_its_own_session(monkeypatch):
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(control.subprocess, "run", run)

    assert control._launch(control_port=True) is True
    assert seen["cmd"][0] == "bash"
    assert seen["cmd"][1].endswith(os.path.join("scripts", "agent_firefox_launch.sh"))
    assert seen["kwargs"]["start_new_session"] is True
    assert seen["kwargs"]["env"]["GUAARDVARK_AGENT_CDP"] == "1"


@pytest.mark.parametrize("outcome", ["exit 1", "timeout"])
def test_a_failed_launch_reports_false(monkeypatch, outcome):
    def run(cmd, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 1, "", "no display")

    monkeypatch.setattr(control.subprocess, "run", run)

    assert control._launch(control_port=True) is False


def test_only_this_installs_agent_firefox_is_found(monkeypatch, tmp_path):
    profile = str(tmp_path / "data" / "agent" / "firefox_profile")
    snap_ff = "/snap/firefox/current/usr/lib/firefox/firefox"
    table = {
        "100": [snap_ff],  # the person's own Firefox
        "101": [snap_ff, "-contentproc", "-isForBrowser", "-appDir", "/snap/firefox/x/browser"],
        "102": [snap_ff, "--no-remote", "--remote-debugging-port", "9222", "--profile", profile],
        "103": ["firefox", "--no-remote", "--profile", "/srv/other-install/data/agent/firefox_profile"],
        "104": ["/venv/bin/python", "-m", "backend.utils.agent_web_gate", "apply", "--profile", profile],
    }

    def fake_open(path, mode="r"):
        pid = path.split("/")[2]
        if pid not in table:
            raise FileNotFoundError(path)
        return contextlib.nullcontext(types.SimpleNamespace(
            read=lambda: b"\0".join(a.encode() for a in table[pid]) + b"\0"))

    fake_os = types.SimpleNamespace(listdir=lambda d: [*table, "self", "999"], path=os.path)
    monkeypatch.setattr(agent_web_gate, "os", fake_os)
    monkeypatch.setattr(agent_web_gate, "open", fake_open, raising=False)

    assert agent_web_gate.agent_firefox_pids(profile) == [102]


# --- the posting tick ------------------------------------------------------


@pytest.fixture
def tick(app, monkeypatch, web_access_on):
    """Run the tick with stand-in posters and a recording browser control.
    Returns (run, events, set_refusal)."""
    events: list[str] = []
    refusal = {"value": None}

    @contextlib.contextmanager
    def recording_control():
        events.append("open")
        try:
            yield refusal["value"]
        finally:
            events.append("close")

    def stand_in(name):
        def post(*args, **kwargs):
            events.append(name)
            return True, "ok"
        return post

    def with_ctx(fn, *args, **kwargs):
        with app.app_context():
            return fn(*args, **kwargs)

    monkeypatch.setattr(tasks, "_with_app_context", with_ctx)
    monkeypatch.setattr(kill_switch, "is_enabled", lambda: True)
    monkeypatch.setattr(kill_switch, "cadence_allows_post", lambda platform: (True, None))
    monkeypatch.setattr("backend.utils.agent_browser_control.agent_browser_control", recording_control)
    for target, name in (
        ("backend.services.social_outreach.reddit_outreach.post_comment_via_servo", "reddit_comment"),
        ("backend.services.social_outreach.youtube_outreach.post_youtube_comment_via_servo", "youtube_comment"),
        ("backend.services.social_outreach.general_poster.post_via_agent_loop", "agent_loop"),
        ("backend.services.social_outreach.reddit_outreach.record_post_via_backend", "record_post"),
    ):
        monkeypatch.setattr(target, stand_in(name))

    def set_refusal(value):
        refusal["value"] = value

    return (lambda: tasks.tick_process_approved_drafts.run()), events, set_refusal


def _approved(platform, action="comment", url="https://www.reddit.com/r/x/comments/abc/t/"):
    row = SocialOutreachLog(
        platform=platform, action=action, status="approved", draft_text="hello",
        target_url=url, target_thread_id="abc",
    )
    db.session.add(row)
    db.session.commit()
    return row.id


def _status(row_id):
    db.session.expire_all()
    return db.session.get(SocialOutreachLog, row_id).status


def test_the_tick_opens_the_port_once_around_all_its_posts(app, tick):
    run, events, _ = tick
    _approved("reddit")
    _approved("youtube", url="https://www.youtube.com/watch?v=abcdefghijk")
    _approved("x", url="https://x.com/someone/status/1")

    result = run()

    assert result["processed"] == 3
    assert events[0] == "open" and events[-1] == "close"
    assert sorted(events[1:-1]) == sorted([
        "reddit_comment", "youtube_comment", "agent_loop", "record_post", "record_post", "record_post",
    ])


def test_a_busy_agent_leaves_rows_approved_and_unclaimed(app, tick):
    run, events, set_refusal = tick
    set_refusal("agent_busy")
    ids = [_approved("reddit"), _approved("youtube", url="https://www.youtube.com/watch?v=abcdefghijk")]

    result = run()

    assert events == ["open", "close"]
    assert [_status(i) for i in ids] == ["approved", "approved"]
    assert result["processed"] == 0
    assert result["skipped_browser_busy"] == 2
    assert result["browser_refusal"] == "agent_busy"


def test_a_tick_with_nothing_to_post_never_touches_the_browser(app, tick, monkeypatch):
    run, events, _ = tick
    monkeypatch.setattr(kill_switch, "cadence_allows_post", lambda platform: (False, "daily cap hit"))
    unsupported = _approved("reddit", action="reply")
    capped = _approved("youtube", url="https://www.youtube.com/watch?v=abcdefghijk")

    run()

    assert events == []
    assert _status(unsupported) == "unsupported"
    assert _status(capped) == "approved"


def test_the_port_closes_when_a_poster_raises(app, tick, monkeypatch):
    run, events, _ = tick

    def crash(*args, **kwargs):
        events.append("reddit_comment")
        raise RuntimeError("servo crashed")

    monkeypatch.setattr("backend.services.social_outreach.reddit_outreach.post_comment_via_servo", crash)
    _approved("reddit")

    with pytest.raises(RuntimeError):
        run()

    assert events == ["open", "reddit_comment", "close"]


def test_the_display_counts_as_in_use_while_a_post_holds_it(tmp_path, monkeypatch):
    from backend.utils import agent_display_utils as adu

    monkeypatch.setattr(adu, "_display_in_use_marker", lambda: str(tmp_path / "pids" / "agent_display_in_use"))
    monkeypatch.setattr(adu, "get_agent_control_service",
                        lambda: type("S", (), {"is_active": False, "is_learning": False})(), raising=False)
    assert adu._display_marked_in_use() is False
    adu.mark_display_in_use()
    assert adu.is_display_idle_blocker_active() is True
    adu.clear_display_in_use()
    assert adu._display_marked_in_use() is False


def test_a_marker_left_by_a_dead_process_does_not_block(tmp_path, monkeypatch):
    from backend.utils import agent_display_utils as adu

    marker = tmp_path / "agent_display_in_use"
    marker.write_text("999999999")
    monkeypatch.setattr(adu, "_display_in_use_marker", lambda: str(marker))
    assert adu._display_marked_in_use() is False

