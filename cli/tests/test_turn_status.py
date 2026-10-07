"""The turn-status reducer: chat events in, what the status line says out."""

import pytest

from llx import turn_status as ts


def _turn(now=100.0, connected=True):
    state = ts.new_turn(now)
    if connected:
        ts.reduce(state, "connected", {}, now)
    return state


def test_starts_connecting_then_thinking_once_connected():
    state = ts.new_turn(0.0)
    assert state.label == "Connecting…"
    ts.reduce(state, "connected", {}, 0.2)
    assert state.label == "Thinking…"


def test_calling_llm_sets_thinking_and_never_enters_trail():
    state = _turn()
    for iteration in (1, 2, 3):
        ts.reduce(state, "chat:thinking", {"iteration": iteration, "status": "Calling LLM..."}, 101.0)
    assert state.label == "Thinking…"
    assert state.trail == []


def test_gpu_wait_is_status_only_and_repeats_collapse():
    state = _turn()
    gpu = {"status": "Waiting for the GPU: another render is running. This starts as soon as it finishes."}
    ts.reduce(state, "chat:thinking", gpu, 101.0)
    ts.reduce(state, "chat:thinking", dict(gpu), 105.0)
    assert state.label == "Waiting for GPU…"
    assert state.trail == []


@pytest.mark.parametrize("status", [
    "Waiting for approval to run: edit_code...",
    "Waiting for likeness consent...",
])
def test_approval_wait_is_status_only(status):
    state = _turn()
    ts.reduce(state, "chat:thinking", {"iteration": 2, "status": status}, 101.0)
    assert state.label == "Waiting for your approval…"
    assert state.trail == []


def test_agent_loop_steps_enter_trail_and_label():
    state = _turn()
    ts.reduce(state, "chat:thinking", {
        "source": "agent_loop", "iteration": 2, "status": "click 'Save'",
        "reasoning": "The dialog is open.\nSave is bottom right.",
    }, 101.0)
    assert state.label == "Step 2 · click 'Save'"
    assert state.detail == "Save is bottom right."
    assert len(state.trail) == 1
    # The same step updated again merges instead of adding a line.
    ts.reduce(state, "chat:thinking", {
        "source": "agent_loop", "iteration": 2, "status": "click 'Save' — not sent",
    }, 102.0)
    assert len(state.trail) == 1
    assert state.trail[0]["status"] == "click 'Save' — not sent"
    assert state.trail[0]["reasoning"].startswith("The dialog is open.")


def test_agent_executor_step_label_is_not_doubled():
    state = _turn()
    ts.reduce(state, "chat:thinking", {
        "source": "agent_loop", "iteration": 3, "status": "Step 3", "reasoning": "look again",
    }, 101.0)
    assert state.label == "Step 3"


def test_reset_token_clears_text_and_rechecks():
    state = _turn()
    ts.reduce(state, "chat:token", {"content": "I searched and found"}, 101.0)
    assert state.answering
    ts.reduce(state, "chat:token", {"content": "", "reset": True}, 102.0)
    assert state.full_text == ""
    assert state.label == "Re-checking…"
    assert not state.answering
    assert ts.status_visible(state, 102.1)


def test_reasoning_touches_activity_and_shows_last_line():
    state = _turn(now=100.0)
    ts.reduce(state, "chat:thinking", {"iteration": 1, "status": "Calling LLM..."}, 100.0)
    ts.reduce(state, "chat:reasoning", {"delta": "The user wants a greeting.\nThree words"}, 104.0)
    assert state.last_activity_at == 104.0
    assert state.label == "Thinking…"
    assert state.detail == "Three words"
    ts.reduce(state, "chat:reasoning", {"delta": " only."}, 110.0)
    assert state.last_activity_at == 110.0
    assert state.detail == "Three words only."
    ts.reduce(state, "chat:reasoning", {"done": True, "text": "..."}, 116.0)
    assert state.thought_s == pytest.approx(12.0)
    assert state.detail == ""


def test_reasoning_done_without_reasoning_adds_no_thought_time():
    state = _turn()
    ts.reduce(state, "chat:reasoning", {"done": True, "text": ""}, 105.0)
    assert state.thought_s == 0.0


@pytest.mark.parametrize("name,params,label", [
    ("web_search", {"query": "weather"}, "Searching the web…"),
    ("search_knowledge_base", {"query": "renewal"}, "Searching documents…"),
    ("rag_search", {}, "Searching documents…"),
    ("read_code", {"filepath": "backend/app.py"}, "Reading app.py…"),
    ("read_code", '{"filepath": "cli/llx/repl.py"}', "Reading repl.py…"),
    ("generate_image", {"prompt": "a fox"}, "Generating image…"),
    ("generate_video", {"prompt": "a fox"}, "Generating video…"),
    ("list_code_files", {}, "Running list_code_files…"),
])
def test_friendly_tool_labels(name, params, label):
    assert ts.tool_label(name, params) == label


def test_tool_call_keeps_status_up_and_result_marks_line():
    state = _turn()
    ts.reduce(state, "chat:token", {"content": "Let me look that up."}, 101.0)
    ts.reduce(state, "chat:tool_call", {"tool": "web_search", "params": {"query": "x"}, "iteration": 1}, 101.5)
    # Text before a tool call is the model thinking aloud.
    assert state.full_text == ""
    assert state.label == "Searching the web…"
    assert ts.status_visible(state, 101.6)
    ts.reduce(state, "chat:tool_result", {
        "tool": "web_search", "result": {"success": True}, "duration_ms": 1234,
    }, 103.0)
    call = state.tools[0]
    assert call.done and call.ok
    assert call.duration_s == pytest.approx(1.234)
    assert state.label == "Thinking…"


def test_failed_tool_result_without_duration_uses_clock():
    state = _turn()
    ts.reduce(state, "chat:tool_call", {"tool": "fetch_url", "params": {"url": "u"}}, 200.0)
    ts.reduce(state, "chat:tool_result", {
        "tool": "fetch_url", "result": {"success": False, "error": "blocked"}, "duration_ms": 0,
    }, 202.5)
    assert state.tools[0].ok is False
    assert state.tools[0].duration_s == pytest.approx(2.5)


def test_parallel_tools_label_follows_the_one_still_running():
    state = _turn()
    ts.reduce(state, "chat:tool_call", {"tool": "web_search", "params": {}}, 1.0)
    ts.reduce(state, "chat:tool_call", {"tool": "generate_image", "params": {}}, 1.0)
    ts.reduce(state, "chat:tool_result", {"tool": "web_search", "result": {"success": True}}, 2.0)
    assert state.label == "Generating image…"


def test_first_token_hides_status_and_stall_brings_it_back():
    state = _turn(now=0.0)
    assert ts.status_visible(state, 0.5)
    ts.reduce(state, "chat:token", {"content": "Hi"}, 1.0)
    assert not ts.status_visible(state, 1.5)
    assert ts.status_visible(state, 1.0 + ts.STALL_AFTER_S)
    ts.reduce(state, "chat:complete", {"response": "Hi"}, 4.0)
    assert not ts.status_visible(state, 10.0)


def test_whitespace_token_does_not_count_as_the_answer():
    state = _turn()
    ts.reduce(state, "chat:token", {"content": "\n\n"}, 101.0)
    assert not state.answering
    assert ts.status_visible(state, 101.1)


def test_tool_markup_tokens_are_dropped():
    state = _turn()
    ts.reduce(state, "chat:token", {"content": "<tool_call>"}, 101.0)
    assert state.full_text == ""
    assert not state.answering


def test_complete_supplies_text_when_nothing_streamed():
    state = _turn()
    ts.reduce(state, "chat:complete", {"response": "Done."}, 101.0)
    assert state.full_text == "Done."
    assert state.closed


def test_error_closes_turn():
    state = _turn()
    ts.reduce(state, "chat:error", {"error": "boom"}, 101.0)
    assert state.error == "boom"
    assert not ts.status_visible(state, 101.0)


@pytest.mark.parametrize("seconds,text", [
    (0.0, "0.0s"),
    (0.8, "0.8s"),
    (9.94, "9.9s"),
    (9.96, "10s"),
    (14.2, "14s"),
    (59.9, "59s"),
    (64, "1m 04s"),
    (600, "10m 00s"),
    (3725, "1h 02m"),
])
def test_elapsed_formatting(seconds, text):
    assert ts.format_elapsed(seconds) == text
