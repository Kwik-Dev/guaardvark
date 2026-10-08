"""What a chat turn is doing right now, derived from the backend's chat events.

``reduce(state, event, data, now)`` applies one Socket.IO event to a
``TurnState`` and returns it. It does no I/O and never reads the clock; the
caller passes ``now`` (``time.monotonic()``), so the same event sequence always
yields the same state. Rendering lives in ``llx.streaming`` and ``llx.working``.

Status-only events ("Calling LLM...", approval and GPU waits) change the label
and never enter the thinking trail; only the agent loop's steps do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

# A reply that goes quiet this long while the turn is still open shows the
# status line again, so a stalled stream does not look finished.
STALL_AFTER_S = 2.0

CONNECTING = "Connecting…"
THINKING = "Thinking…"
RECHECKING = "Re-checking…"
WAITING_APPROVAL = "Waiting for your approval…"
WAITING_GPU = "Waiting for GPU…"

_TOOL_LABELS = {
    "web_search": "Searching the web…",
    "fetch_url": "Reading a web page…",
    "analyze_website": "Reading a website…",
    "search_knowledge_base": "Searching documents…",
    "list_documents": "Listing documents…",
    "get_document_outline": "Reading a document…",
    "read_document_section": "Reading a document…",
    "summarize_corpus": "Reading documents…",
    "search_codebase": "Searching code…",
    "search_code": "Searching code…",
    "find_files": "Finding files…",
    "find_records": "Searching records…",
    "search_memory": "Searching memory…",
    "save_memory": "Saving to memory…",
    "generate_image": "Generating image…",
    "edit_image": "Editing image…",
    "inpaint_image": "Editing image…",
    "outpaint_image": "Editing image…",
    "remove_background": "Editing image…",
    "generate_video": "Generating video…",
    "generate_animation": "Generating video…",
    "generate_music": "Generating music…",
    "generate_speech": "Generating speech…",
}
# Tools that act on one file: the label names the file.
_FILE_TOOLS = {"read_code": "Reading", "analyze_code": "Reading", "edit_code": "Editing"}
_FILE_PARAMS = ("filepath", "file_path", "path", "filename")


@dataclass
class ToolCall:
    name: str
    label: str
    args: str
    started_at: float
    done: bool = False
    ok: bool | None = None
    duration_s: float | None = None


@dataclass
class TurnState:
    started_at: float
    label: str = CONNECTING
    detail: str = ""
    connected: bool = False
    answering: bool = False
    last_token_at: float | None = None
    last_activity_at: float = 0.0
    text: list[str] = field(default_factory=list)
    trail: list[dict] = field(default_factory=list)
    tools: list[ToolCall] = field(default_factory=list)
    tool_outputs: dict[str, list[str]] = field(default_factory=dict)
    reasoning: str = ""
    reasoning_started_at: float | None = None
    iteration_started_at: float | None = None
    thought_s: float = 0.0
    complete: dict | None = None
    error: str | None = None
    closed: bool = False

    @property
    def full_text(self) -> str:
        return "".join(self.text)


def new_turn(now: float, label: str = CONNECTING) -> TurnState:
    return TurnState(started_at=now, label=label, last_activity_at=now)


def format_elapsed(seconds: float) -> str:
    """0.8s, 14s, 1m 04s, 1h 02m."""
    seconds = max(0.0, float(seconds))
    if seconds < 9.95:
        return f"{seconds:.1f}s"
    whole = max(10, int(seconds))
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        minutes, secs = divmod(whole, 60)
        return f"{minutes}m {secs:02d}s"
    hours, rest = divmod(whole, 3600)
    return f"{hours}h {rest // 60:02d}m"


def _as_dict(params: Any) -> dict:
    if isinstance(params, dict):
        return params
    if isinstance(params, str) and params.strip().startswith("{"):
        try:
            loaded = json.loads(params)
        except ValueError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def tool_label(name: str, params: Any = None) -> str:
    """Plain-words status for a tool call, e.g. "Searching the web…"."""
    name = (name or "").strip() or "tool"
    if name in _FILE_TOOLS:
        p = _as_dict(params)
        target = next((p[k] for k in _FILE_PARAMS if isinstance(p.get(k), str) and p[k]), "")
        verb = _FILE_TOOLS[name]
        return f"{verb} {PurePath(target).name}…" if target else f"{verb} code…"
    if name in _TOOL_LABELS:
        return _TOOL_LABELS[name]
    if "rag" in name.lower().split("_") or "knowledge" in name.lower():
        return "Searching documents…"
    return f"Running {name}…"


def args_preview(params: Any, limit: int = 120) -> str:
    if params is None:
        return ""
    if isinstance(params, (dict, list)):
        params = json.dumps(params, sort_keys=True, default=str)
    text = str(params)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _last_line(text: str, limit: int = 200) -> str:
    for line in reversed((text or "").splitlines()):
        line = " ".join(line.split())
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _status_label(status: str) -> str:
    """Label for a chat:thinking status that is not an agent step."""
    lowered = status.lower()
    if lowered.startswith("calling llm"):
        return THINKING
    if lowered.startswith("waiting for approval") or "consent" in lowered:
        return WAITING_APPROVAL
    if lowered.startswith("waiting for the gpu") or lowered.startswith("waiting for gpu"):
        return WAITING_GPU
    return status.rstrip(" .…") + "…"


def _running_label(state: TurnState) -> str:
    for call in reversed(state.tools):
        if not call.done:
            return call.label
    return THINKING


def status_visible(state: TurnState, now: float) -> bool:
    """Whether the spinner line belongs on screen at ``now``."""
    if state.closed:
        return False
    if not state.answering:
        return True
    return state.last_token_at is not None and now - state.last_token_at >= STALL_AFTER_S


def elapsed(state: TurnState, now: float) -> float:
    return now - state.started_at


def _on_token(state: TurnState, data: dict, now: float) -> None:
    if data.get("reset"):
        # The backend retracted what it streamed and is asking the model again.
        state.text.clear()
        state.answering = False
        state.label = RECHECKING
        state.detail = ""
        return
    content = data.get("content") or ""
    if not content:
        return
    # Raw tool-call markup leaked by the model is not part of the answer.
    if content.startswith("<tool") or content.startswith("</tool"):
        return
    if state.text and state.text[-1].startswith("<tool"):
        state.text.pop()
        return
    state.text.append(content)
    state.last_token_at = now
    if content.strip():
        state.answering = True
        state.detail = ""


def _on_thinking(state: TurnState, data: dict, now: float) -> None:
    status = (data.get("status") or data.get("label") or "").strip()
    reasoning = (data.get("reasoning") or "").strip()
    if data.get("source") != "agent_loop":
        if status.lower().startswith("calling llm"):
            state.iteration_started_at = now
        if status:
            state.label = _status_label(status)
            state.detail = ""
        return
    iteration = data.get("iteration")
    step = {"iteration": iteration, "status": status, "reasoning": reasoning}
    for idx, existing in enumerate(state.trail):
        if iteration is not None and existing.get("iteration") == iteration:
            merged = dict(existing)
            if status:
                merged["status"] = status
            if reasoning:
                merged["reasoning"] = reasoning
            state.trail[idx] = merged
            break
    else:
        state.trail.append(step)
    if status.lower().startswith("step") or iteration is None:
        state.label = status or THINKING
    else:
        state.label = f"Step {iteration} · {status}" if status else f"Step {iteration}"
    state.detail = _last_line(reasoning)


def _on_reasoning(state: TurnState, data: dict, now: float) -> None:
    if data.get("done"):
        text = data.get("text") or ""
        if state.reasoning or text.strip():
            began = state.reasoning_started_at or state.iteration_started_at or now
            state.thought_s += max(0.0, now - began)
        state.reasoning = ""
        state.reasoning_started_at = None
        state.detail = ""
        return
    delta = data.get("delta") or ""
    if not delta:
        return
    if state.reasoning_started_at is None:
        state.reasoning_started_at = now
    state.reasoning += delta
    state.label = THINKING
    state.detail = _last_line(state.reasoning)


def _on_tool_call(state: TurnState, data: dict, now: float) -> None:
    name = data.get("name") or data.get("tool") or "unknown"
    params = data.get("params")
    if params is None:
        params = data.get("arguments")
    if params is None:
        params = data.get("args", "")
    # Text streamed before a tool call is the model thinking aloud, not the answer.
    state.text.clear()
    state.answering = False
    state.tools.append(ToolCall(
        name=name, label=tool_label(name, params), args=args_preview(params), started_at=now,
    ))
    state.label = state.tools[-1].label
    state.detail = ""


def _result_ok(data: dict) -> bool:
    result = data.get("result")
    if isinstance(result, dict):
        if "success" in result:
            return bool(result.get("success"))
        return not result.get("error")
    return not data.get("error")


def _on_tool_result(state: TurnState, data: dict, now: float) -> None:
    name = data.get("tool") or data.get("name") or "unknown"
    call = next((c for c in reversed(state.tools) if c.name == name and not c.done), None)
    if call is None:
        call = ToolCall(name=name, label=tool_label(name), args="", started_at=now)
        state.tools.append(call)
    call.done = True
    call.ok = _result_ok(data)
    duration_ms = data.get("duration_ms")
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        call.duration_s = duration_ms / 1000.0
    else:
        call.duration_s = max(0.0, now - call.started_at)
    state.label = _running_label(state)
    state.detail = ""


def reduce(state: TurnState, event: str, data: Any, now: float) -> TurnState:
    """Apply one chat event to ``state`` (in place) and return it."""
    data = data if isinstance(data, dict) else {}
    if event != "connected":
        state.last_activity_at = now
    if event == "connected":
        state.connected = True
        if state.label == CONNECTING:
            state.label = THINKING
    elif event == "chat:token":
        _on_token(state, data, now)
    elif event == "chat:thinking":
        _on_thinking(state, data, now)
    elif event == "chat:reasoning":
        _on_reasoning(state, data, now)
    elif event == "chat:tool_call":
        _on_tool_call(state, data, now)
    elif event == "chat:tool_result":
        _on_tool_result(state, data, now)
    elif event == "chat:tool_output_chunk":
        tool = data.get("tool") or "unknown"
        state.tool_outputs.setdefault(tool, []).append(data.get("chunk") or "")
    elif event == "chat:complete":
        state.complete = data
        if not state.text and data.get("response"):
            state.text.append(data["response"])
        state.closed = True
    elif event == "chat:error":
        state.error = data.get("error") or "Unknown error"
        state.closed = True
    elif event == "chat:aborted":
        state.error = state.error or "Chat aborted"
        state.closed = True
    return state
