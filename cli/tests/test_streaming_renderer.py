from llx.streaming import ChatRenderer


def test_tool_call_renderer_displays_params_payload():
    renderer = ChatRenderer()

    renderer.on_tool_call({
        "tool": "edit_code",
        "params": {"filepath": "example.py", "dry_run": True},
    })

    assert "edit_code" in renderer._tool_lines[0]
    assert "dry_run" in renderer._tool_lines[0]
    assert "example.py" in renderer._tool_lines[0]


def test_approval_prompt_rejects_mismatched_edit_target(tmp_path):
    renderer = ChatRenderer()
    expected = tmp_path / "docs" / "test-container.sh"
    expected.parent.mkdir()
    expected.write_text("#!/usr/bin/env bash\n")

    approved = renderer.prompt_for_approval(
        {
            "tools": ["edit_code"],
            "tool_details": [
                {"tool": "edit_code", "params": {"filepath": "CONTRIBUTING.md"}},
            ],
        },
        expected_target=str(expected),
    )

    assert approved is False


# ── Live status line ──────────────────────────────────────────

import io
import json
import re

import pytest
from rich.console import Console
from typer.testing import CliRunner

from llx import streaming, working
from llx.theme import _get_active_rich_theme

_SPINNER_FRAMES = set("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


class _TtyIO(io.StringIO):
    def isatty(self):
        return True


def _console(tty=False, width=80):
    buf = _TtyIO() if tty else io.StringIO()
    console = Console(
        file=buf, force_terminal=tty, width=width, height=24, theme=_get_active_rich_theme(),
    )
    return console, buf


@pytest.fixture
def titles(monkeypatch):
    calls = []
    monkeypatch.setattr(streaming, "_set_title", calls.append)
    monkeypatch.setattr("llx.output._json_mode", False)
    monkeypatch.delenv("GUAARDVARK_NO_SPINNER", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    return calls


def _play_turn(renderer):
    renderer.on_connected()
    renderer.on_thinking({"iteration": 1, "status": "Calling LLM..."})
    renderer.on_tool_call({"tool": "web_search", "params": {"query": "hi"}, "iteration": 1})
    renderer.on_tool_result({"tool": "web_search", "result": {"success": True}, "duration_ms": 900})
    renderer.on_thinking({"iteration": 2, "status": "Calling LLM..."})
    renderer.on_token("Hello ")
    renderer.on_token("there.")
    renderer.on_complete({"response": "Hello there."})


def test_non_terminal_console_gets_only_the_final_text(titles):
    console, buf = _console(tty=False)
    renderer = ChatRenderer(console=console)
    renderer.start(esc_hint=True)
    _play_turn(renderer)
    renderer.stop()
    out = buf.getvalue()
    assert "Hello there." in out
    assert "\x1b" not in out
    assert not (_SPINNER_FRAMES & set(out))
    assert "Thinking…" not in out and "esc to stop" not in out
    assert titles == []
    # Status-only "Calling LLM..." events print no "Agent thinking" block.
    assert "Agent thinking" not in out
    assert "✓ web_search" in out


@pytest.mark.parametrize("env", [{"GUAARDVARK_NO_SPINNER": "1"}, {"TERM": "dumb"}])
def test_spinner_off_switches(titles, monkeypatch, env):
    console, _buf = _console(tty=True)
    assert working.live_status_enabled(console) is True
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert working.live_status_enabled(console) is False


def test_forced_color_pipe_does_not_animate(titles):
    console = Console(file=io.StringIO(), force_terminal=True)
    assert working.live_status_enabled(console) is False


def test_json_mode_does_not_animate(titles, monkeypatch):
    console, _buf = _console(tty=True)
    monkeypatch.setattr("llx.output._json_mode", True)
    assert working.live_status_enabled(console) is False


def test_status_line_shows_label_elapsed_and_hint(titles):
    console, _buf = _console(tty=True)
    renderer = ChatRenderer(console=console)
    renderer._hint = "esc to stop"
    renderer.on_connected()
    renderer.on_tool_call({"tool": "web_search", "params": {"query": "hi"}})
    probe, probe_buf = _console(tty=False)
    probe.print(renderer._view())
    assert re.search(r"Searching the web… · \d+\.\ds · esc to stop", probe_buf.getvalue())

    renderer.on_tool_result({"tool": "web_search", "result": {"success": True}})
    renderer.on_token("Hi")
    probe, probe_buf = _console(tty=False)
    probe.print(renderer._view())
    assert "Thinking…" not in probe_buf.getvalue()
    assert "Hi" in probe_buf.getvalue()


def test_approval_takes_live_down_and_brings_a_new_one_back(titles, monkeypatch):
    console, _buf = _console(tty=True)
    renderer = ChatRenderer(console=console)
    renderer.start(esc_hint=False)
    first_live = renderer._live
    assert first_live is not None and first_live.is_started

    seen = {}

    def confirm(*_args, **_kwargs):
        seen["live_during_prompt"] = renderer._live
        seen["first_still_started"] = first_live.is_started
        return True

    monkeypatch.setattr("typer.confirm", confirm)
    try:
        assert renderer.prompt_for_approval({"tools": ["generate_image"]}) is True
        assert seen == {"live_during_prompt": None, "first_still_started": False}
        assert renderer._live is not None and renderer._live is not first_live
        assert renderer._live.is_started
    finally:
        renderer.stop()
    assert renderer._live is None


def test_working_helper_is_silent_off_a_terminal(titles):
    console, buf = _console(tty=False)
    with working.working("Generating image…", console=console) as handle:
        handle.update("Still generating…")
    assert buf.getvalue() == ""


def test_working_helper_animates_on_a_terminal(titles):
    console, buf = _console(tty=True)
    with working.working("Generating image…", console=console):
        pass
    assert "Generating image…" in buf.getvalue()
    assert not console._live_stack


class _FakeStreamer:
    def __init__(self, server_url=None):
        self.type_ahead = ""

    def stream_chat(self, session_id, **callbacks):
        callbacks["on_connected"]()
        callbacks["on_thinking"]({"iteration": 1, "status": "Calling LLM..."})
        callbacks["on_reasoning"]({"delta": "hmm"})
        callbacks["on_reasoning"]({"done": True, "text": "hmm"})
        callbacks["on_token"]("draft that gets retracted")
        callbacks["on_token_reset"]()
        callbacks["on_tool_call"]({"tool": "web_search", "params": {"query": "q"}})
        callbacks["on_tool_result"]({"tool": "web_search", "result": {"success": True}})
        callbacks["on_token"]("hi ")
        callbacks["on_token"]("there")
        callbacks["on_complete"]({"response": "hi there"})

    def wait_for_completion(self, *args, **kwargs):
        return True

    def hard_abort(self, *args, **kwargs):
        pass

    def disconnect(self):
        pass


class _FakeClient:
    server_url = "http://localhost:5002"

    def post(self, endpoint, json=None, **kwargs):
        return {"success": True}


class _FakeSio:
    def __init__(self):
        self.handlers = {}

    def on(self, name):
        def register(fn):
            self.handlers[name] = fn
            return fn
        return register

    def event(self, fn):
        self.handlers[fn.__name__] = fn
        return fn

    def connect(self, *args, **kwargs):
        pass

    def emit(self, *args, **kwargs):
        pass


def test_streamer_routes_reasoning_results_and_resets(monkeypatch):
    streamer = streaming.LlxStreamer(server_url="http://localhost:5002")
    streamer.sio = _FakeSio()
    monkeypatch.setattr(streamer, "_connect_headers", lambda: {})
    seen = []
    streamer.stream_chat(
        "s1",
        on_token=lambda c: seen.append(("token", c)),
        on_reasoning=lambda d: seen.append(("reasoning", d.get("delta"))),
        on_tool_result=lambda d: seen.append(("result", d.get("tool"))),
        on_token_reset=lambda: seen.append(("reset", None)),
        on_connected=lambda: seen.append(("connected", None)),
    )
    handlers = streamer.sio.handlers
    streamer._last_activity = 0.0
    handlers["chat:reasoning"]({"delta": "x"})
    assert streamer._last_activity > 0.0
    handlers["chat:tool_result"]({"tool": "web_search"})
    handlers["chat:token"]({"content": "", "reset": True})
    handlers["chat:token"]({"content": "ok"})
    assert seen == [
        ("connected", None), ("reasoning", "x"), ("result", "web_search"),
        ("reset", None), ("token", "ok"),
    ]


def test_chat_stream_json_stays_clean(monkeypatch, titles):
    from llx.main import app

    monkeypatch.setattr("llx.commands.chat.get_client", lambda server=None: _FakeClient())
    monkeypatch.setattr("llx.commands.chat.save_session", lambda *a, **k: None)
    monkeypatch.setattr(streaming, "LlxStreamer", _FakeStreamer)
    result = CliRunner().invoke(app, ["chat", "hi", "--stream", "--json"])
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.stdout
    assert not (_SPINNER_FRAMES & set(result.stdout))
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["data"]["response"] == "hi there"
    assert titles == []
