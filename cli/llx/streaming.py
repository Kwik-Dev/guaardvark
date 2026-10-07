"""Socket.IO streaming client for chat and job progress."""

# python-engineio 4.x caps polling payloads at 16 packets per HTTP response and
# aborts the whole connection if the server exceeds that. The backend happily
# batches 20–40 chat:token events into one poll when Gemma4 streams a short
# reply, so without this bump the CLI drops its socket after the first few
# tokens and then waits 5 minutes for a chat:complete that never lands. Monkey-
# patch the class attribute BEFORE socketio imports anything that caches it.
from engineio import payload as _engineio_payload
_engineio_payload.Payload.max_decode_packets = 10000

import socketio
import threading
import time
from contextlib import nullcontext
from typing import Callable

from llx.config import get_api_key, get_server_url
from llx.working_memory import approval_target_mismatch, extract_approval_targets

# Idle silence before treating a stream as stalled (activity resets this).
DEFAULT_IDLE_TIMEOUT = 300.0
# Absolute ceiling so a continuously chatty but never-completing stream ends.
DEFAULT_HARD_TIMEOUT = 1800.0


class LlxStreamer:
    """Handles Socket.IO connections for streaming chat and job progress."""

    def __init__(self, server_url: str | None = None):
        self.server_url = server_url or get_server_url()
        self.sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
        self._connected = False
        self._done = threading.Event()
        # Approval requests get stashed here for the main thread to pick up.
        # Doing the prompt inside the socketio receive thread freezes the
        # whole event loop and the user can't see what they're answering.
        self._approval_pending = threading.Event()
        self._approval_data: dict | None = None
        self._approval_lock = threading.Lock()
        self._session_id: str | None = None
        self._last_activity = time.monotonic()
        self._activity_lock = threading.Lock()
        self._closing = False
        # What the user typed while a reply was streaming (see wait_for_completion).
        self.type_ahead = ""

    def _connect_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        api_key = get_api_key(self.server_url)
        if api_key:
            headers["X-API-Key"] = api_key
        return headers

    def _touch_activity(self):
        with self._activity_lock:
            self._last_activity = time.monotonic()

    def stream_chat(
        self,
        session_id: str,
        on_token: Callable[[str], None],
        on_thinking: Callable[[dict], None] | None = None,
        on_tool_call: Callable[[dict], None] | None = None,
        on_tool_output_chunk: Callable[[dict], None] | None = None,
        on_complete: Callable[[dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_reasoning: Callable[[dict], None] | None = None,
        on_tool_result: Callable[[dict], None] | None = None,
        on_token_reset: Callable[[], None] | None = None,
        on_connected: Callable[[], None] | None = None,
    ):
        """
        Connect to Socket.IO, join session, and listen for chat events.
        Call this BEFORE posting the chat message via HTTP.

        Approvals are NOT handled via callback — they're stashed in the
        streamer and the main thread picks them up via pop_pending_approval()
        or wait_for_completion(approval_handler=...). This keeps blocking
        prompts off the socketio receive thread.

        ``on_token_reset`` fires when the backend retracts the text it has
        streamed so far (it is re-asking the model); ``on_connected`` once the
        socket has joined the session.
        """
        self._done.clear()
        self._approval_pending.clear()
        with self._approval_lock:
            self._approval_data = None
        self._session_id = session_id
        self._closing = False
        self._touch_activity()

        @self.sio.on("chat:token")
        def handle_token(data):
            self._touch_activity()
            if data.get("reset"):
                if on_token_reset:
                    on_token_reset()
                return
            content = data.get("content", "")
            if content:
                on_token(content)

        @self.sio.on("chat:thinking")
        def handle_thinking(data):
            self._touch_activity()
            if on_thinking:
                on_thinking(data)

        # A thinking model can reason for minutes before its first token;
        # each reasoning chunk counts as activity for the idle timeout.
        @self.sio.on("chat:reasoning")
        def handle_reasoning(data):
            self._touch_activity()
            if on_reasoning:
                on_reasoning(data)

        @self.sio.on("chat:tool_call")
        def handle_tool_call(data):
            self._touch_activity()
            if on_tool_call:
                on_tool_call(data)

        @self.sio.on("chat:tool_result")
        def handle_tool_result(data):
            self._touch_activity()
            if on_tool_result:
                on_tool_result(data)

        @self.sio.on("chat:tool_approval_request")
        def handle_tool_approval_request(data):
            self._touch_activity()
            # Stash and signal — never block this thread on user I/O.
            with self._approval_lock:
                self._approval_data = data
            self._approval_pending.set()

        @self.sio.on("chat:tool_output_chunk")
        def handle_tool_output_chunk(data):
            self._touch_activity()
            if on_tool_output_chunk:
                on_tool_output_chunk(data)

        @self.sio.on("chat:complete")
        def handle_complete(data):
            self._touch_activity()
            if on_complete:
                on_complete(data)
            self._done.set()

        @self.sio.on("chat:error")
        def handle_error(data):
            self._touch_activity()
            if on_error:
                on_error(data.get("error", "Unknown error"))
            self._done.set()

        @self.sio.on("chat:aborted")
        def handle_aborted(data):
            self._touch_activity()
            if on_error:
                on_error("Chat aborted")
            self._done.set()

        @self.sio.event
        def disconnect():
            if self._closing or self._done.is_set():
                return
            if on_error:
                on_error("Stream disconnected before completion")
            self._done.set()

        try:
            self.sio.connect(
                self.server_url,
                transports=["polling", "websocket"],
                headers=self._connect_headers(),
            )
            self._connected = True
            self.sio.emit("chat:join", {"session_id": session_id})
        except Exception as e:
            if on_error:
                on_error(f"Failed to connect for streaming: {e}")
            self._done.set()
            return
        if on_connected:
            on_connected()

    def wait(self, timeout: float = DEFAULT_IDLE_TIMEOUT) -> bool:
        """Block until streaming is done. Returns True if completed, False on timeout."""
        return self._done.wait(timeout=timeout)

    def pop_pending_approval(self) -> dict | None:
        """Atomically retrieve and clear any pending approval request.
        Safe to call from the main thread; returns None if nothing is pending."""
        with self._approval_lock:
            if not self._approval_pending.is_set():
                return None
            data = self._approval_data
            self._approval_data = None
            self._approval_pending.clear()
        return data

    def wait_for_completion(
        self,
        approval_handler: Callable[[dict], bool] | None = None,
        timeout: float = DEFAULT_IDLE_TIMEOUT,
        hard_timeout: float = DEFAULT_HARD_TIMEOUT,
        esc_stops: bool = False,
    ) -> bool:
        """Block until chat is done, dispatching approval requests to the
        current thread via approval_handler(data) -> bool.

        ``timeout`` is idle silence (activity resets it). ``hard_timeout`` is
        an absolute ceiling from the start of the wait.

        If approval_handler is None, any approval request is auto-rejected
        (suitable for non-interactive / json mode). KeyboardInterrupt raised
        from the handler aborts the chat and propagates up.

        With ``esc_stops``, Esc raises KeyboardInterrupt like Ctrl+C, so the
        caller's existing Ctrl+C path stops the chat. Text typed during the
        wait is left in ``self.type_ahead``.

        Returns True if chat completed, False on timeout.
        """
        from llx.keywatch import EscWatch

        watch = EscWatch() if esc_stops else None
        self.type_ahead = ""
        try:
            with watch or nullcontext():
                return self._wait_loop(approval_handler, timeout, hard_timeout, watch)
        finally:
            if watch is not None:
                self.type_ahead = watch.type_ahead

    def _wait_loop(self, approval_handler, timeout, hard_timeout, watch) -> bool:
        started = time.monotonic()
        self._touch_activity()
        while True:
            if watch is not None and watch.pressed.is_set():
                raise KeyboardInterrupt
            now = time.monotonic()
            if now - started >= hard_timeout:
                return False
            with self._activity_lock:
                idle_for = now - self._last_activity
            if idle_for >= timeout:
                return False
            # Wake up regularly to check for pending approvals.
            slice_timeout = min(0.25, timeout - idle_for, hard_timeout - (now - started))
            if slice_timeout <= 0:
                return False
            if self._done.wait(timeout=slice_timeout):
                return True
            data = self.pop_pending_approval()
            if data is None:
                continue
            self._touch_activity()
            if approval_handler is None:
                approved = False
            else:
                try:
                    with watch.paused() if watch is not None else nullcontext():
                        approved = bool(approval_handler(data))
                except KeyboardInterrupt:
                    if self._session_id:
                        self.send_approval_response(self._session_id, False)
                        self.abort(self._session_id)
                    raise
                except Exception as e:
                    import sys
                    sys.stderr.write(f"\nApproval handler failed; rejecting tool call: {e}\n")
                    sys.stderr.flush()
                    approved = False
            if self._session_id:
                self.send_approval_response(self._session_id, approved)

    def abort(self, session_id: str):
        """Send abort signal for current chat over Socket.IO."""
        if self._connected:
            try:
                self.sio.emit("chat:abort", {"session_id": session_id})
            except Exception:
                pass

    def hard_abort(self, session_id: str, client=None):
        """Abort via Socket.IO (if connected) and HTTP hard-kill endpoint."""
        self.abort(session_id)
        if client is not None:
            try:
                client.abort_session(session_id)
            except Exception:
                pass
        else:
            try:
                from llx.client import get_client
                get_client(self.server_url).abort_session(session_id)
            except Exception:
                pass

    def send_approval_response(self, session_id: str, approved: bool):
        """Send a tool approval response back to the server."""
        if self._connected:
            try:
                self.sio.emit("chat:tool_approval_response", {"session_id": session_id, "approved": approved})
            except Exception:
                pass

    def disconnect(self):
        """Disconnect from Socket.IO."""
        self._closing = True
        if self._connected:
            try:
                self.sio.disconnect()
            except Exception:
                pass
            self._connected = False

    def watch_job(
        self,
        job_id: str,
        on_progress: Callable[[dict], None],
        on_complete: Callable[[dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        """Subscribe to job progress updates via Socket.IO."""
        self._done.clear()

        @self.sio.on("job_progress")
        def handle_progress(data):
            on_progress(data)
            status = data.get("status", "")
            if status in ("completed", "done", "failed", "error"):
                if status in ("failed", "error") and on_error:
                    on_error(data.get("message", "Job failed"))
                elif on_complete:
                    on_complete(data)
                self._done.set()

        try:
            self.sio.connect(
                self.server_url,
                transports=["polling", "websocket"],
                headers=self._connect_headers(),
            )
            self._connected = True
            self.sio.emit("subscribe", {"job_id": job_id})
        except Exception as e:
            if on_error:
                on_error(f"Failed to connect: {e}")
            self._done.set()


# ── Chat Renderer ─────────────────────────────────────────────

import sys

from rich.console import Group
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from llx import turn_status
from llx.theme import make_console
from llx.working import (
    REFRESH_PER_SECOND,
    SPINNER_NAME,
    live_status_enabled,
    status_renderable,
    verbose_status_enabled,
    write_status_to_stderr,
)

_ICON_TOOL  = "⟡"   # ⟡
_ICON_OK    = "✓"   # ✓
_ICON_FAIL  = "✗"   # ✗
_ICON_THOUGHT = "∴"  # ∴
_ICON_ASSISTANT = "❯"  # ❯  (brand mark — Unicode has no aardvark)

# Live-region budget for tool calls and tool output; the reply text gets the
# rest of the screen so the status line stays in view.
_LIVE_TOOL_LINES = 6
_LIVE_OUTPUT_LINES = 10

_WEB_ACCESS_HINT = (
    "Web access is disabled. Enable it in Settings (allow_web_search), then retry."
)


def _maybe_web_access_hint(message: str) -> str | None:
    lowered = (message or "").lower()
    if "web access is disabled" in lowered or "web search is disabled" in lowered:
        return _WEB_ACCESS_HINT
    return None


def _set_title(title: str):
    """Set the terminal tab title via ANSI escape.

    Writes to /dev/tty directly to bypass Rich's Live display capture.
    Falls back to stderr if /dev/tty is unavailable and stderr is a terminal.
    """
    try:
        with open("/dev/tty", "w") as tty:
            tty.write(f"\033]0;{title}\007")
            tty.flush()
    except OSError:
        # While Live runs, sys.stderr is Rich's proxy, which would print the
        # escape as visible text; write to the terminal underneath it.
        err = getattr(sys.stderr, "rich_proxied_file", sys.stderr)
        try:
            if err.isatty():
                err.write(f"\033]0;{title}\007")
                err.flush()
        except (OSError, ValueError, AttributeError):
            pass


def _tool_text(call: turn_status.ToolCall, now: float) -> Text:
    if not call.done:
        icon, icon_style = _ICON_TOOL, "llx.accent"
    elif call.ok:
        icon, icon_style = _ICON_OK, "llx.success"
    else:
        icon, icon_style = _ICON_FAIL, "llx.error"
    line = Text()
    line.append(f"{icon} ", style=icon_style)
    line.append(f"{call.name}({call.args})", style="llx.dim")
    duration = call.duration_s if call.done else now - call.started_at
    if duration is not None and (call.done or duration >= 1.0):
        line.append(f" · {turn_status.format_elapsed(duration)}", style="llx.dim")
    return line


def _output_text(chunks: list[str]) -> str:
    out_text = "".join(chunks)
    lines = out_text.splitlines()
    if len(lines) > _LIVE_OUTPUT_LINES:
        out_text = "...\n" + "\n".join(lines[-_LIVE_OUTPUT_LINES:])
    return out_text


class TurnCollector:
    """Follows a chat turn from its Socket.IO events without drawing anything.

    Used as-is for --json and piped output, where only the final text is
    printed; with --verbose each new status label goes to stderr. The event
    callbacks may be called from the Socket.IO thread.
    """

    def __init__(self, echo_status: bool = False):
        self._lock = threading.Lock()
        self._state = turn_status.new_turn(time.monotonic())
        self._verbose = echo_status
        self._last_label = ""

    def start(self, label: str = turn_status.CONNECTING):
        with self._lock:
            self._state = turn_status.new_turn(time.monotonic(), label)
        self._last_label = label
        if self._verbose:
            write_status_to_stderr(label)

    def stop(self):
        """Nothing to clear; present so callers can treat both kinds alike."""

    def stream_callbacks(self) -> dict:
        """Keyword arguments for ``LlxStreamer.stream_chat``."""
        return {
            "on_token": self.on_token,
            "on_thinking": self.on_thinking,
            "on_tool_call": self.on_tool_call,
            "on_tool_output_chunk": self.on_tool_output_chunk,
            "on_complete": self.on_complete,
            "on_error": self.on_error,
            "on_reasoning": self.on_reasoning,
            "on_tool_result": self.on_tool_result,
            "on_token_reset": self.on_token_reset,
            "on_connected": self.on_connected,
        }

    def _apply(self, event: str, data: dict):
        with self._lock:
            turn_status.reduce(self._state, event, data, time.monotonic())
            label = self._state.label
        if self._verbose and label != self._last_label:
            self._last_label = label
            write_status_to_stderr(label)

    @property
    def text(self) -> str:
        """The reply text so far (tool chatter and retracted text removed)."""
        with self._lock:
            return self._state.full_text

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._state.error

    _error = error

    @property
    def _tool_lines(self) -> list[str]:
        now = time.monotonic()
        with self._lock:
            return [_tool_text(call, now).plain for call in self._state.tools]

    # ── Event Callbacks ───────────────────────────────────────

    def on_connected(self):
        self._apply("connected", {})

    def on_token(self, content: str):
        self._apply("chat:token", {"content": content})

    def on_token_reset(self):
        """The backend took back the text it streamed and is asking again."""
        self._apply("chat:token", {"content": "", "reset": True})

    def on_thinking(self, data: dict):
        self._apply("chat:thinking", data)

    def on_reasoning(self, data: dict):
        self._apply("chat:reasoning", data)

    def on_tool_call(self, data: dict):
        self._apply("chat:tool_call", data)

    def on_tool_result(self, data: dict):
        self._apply("chat:tool_result", data)

    def on_tool_output_chunk(self, data: dict):
        self._apply("chat:tool_output_chunk", data)

    def on_complete(self, data: dict):
        self._apply("chat:complete", data)

    def on_error(self, message: str):
        self._apply("chat:error", {"error": message})


class ChatRenderer(TurnCollector):
    """Renders a streaming chat turn: a live status line, tool calls, then the reply.

    Socket.IO callbacks only update the turn state under a lock; one rich
    ``Live`` redraws from that state on its own thread, so nothing else writes
    to the terminal while a turn is on screen. When the console cannot animate
    (piped, dumb terminal, GUAARDVARK_NO_SPINNER=1) nothing is drawn until
    ``stop()`` prints the final reply.
    """

    def __init__(self, server_url: str | None = None, console=None):
        super().__init__()
        self.server_url = server_url
        self._console = console or make_console()
        self._live: Live | None = None
        self._live_enabled = False
        self._hint = ""
        self._spinner = Spinner(SPINNER_NAME, style="llx.brand")
        self._title = ""
        self._stopped = True

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self, esc_hint: bool | None = None, label: str = turn_status.CONNECTING):
        """Reset the turn and show the status line (call before connecting)."""
        self._stopped = False
        self._live_enabled = live_status_enabled(self._console)
        self._verbose = not self._live_enabled and verbose_status_enabled()
        super().start(label)
        if esc_hint is None:
            from llx.keywatch import available as esc_available
            esc_hint = esc_available()
        self._hint = "esc to stop" if esc_hint and self._live_enabled else ""
        self._spinner = Spinner(SPINNER_NAME, style="llx.brand")
        self._title = ""
        self._start_live()

    def stop(self):
        """Clear the live region and print the finished turn. Safe to call twice."""
        if self._stopped:
            return
        self._stopped = True
        self._stop_live()
        if self._live_enabled:
            _set_title("agent")

        with self._lock:
            state = self._state
            trail = list(state.trail)
            tools = list(state.tools)
            outputs = {k: list(v) for k, v in state.tool_outputs.items()}
            thought_s = state.thought_s
            full_text = state.full_text
            complete = state.complete
            error = state.error
        now = time.monotonic()

        self._print_thinking_trail(trail)
        for call in tools:
            self._console.print(_tool_text(call, now))

        for tool, chunks in outputs.items():
            if not chunks:
                continue
            out_text = _output_text(chunks)
            # Pretty diff if it looks like one
            if tool in ("edit", "edit_code", "apply") or out_text.lstrip().startswith("--- ") or "diff --git" in out_text[:200]:
                try:
                    from rich.syntax import Syntax
                    self._console.print(Syntax(out_text, "diff", line_numbers=False))
                    continue
                except Exception:
                    pass
            self._console.print(Text(f"[{tool} output]\n{out_text}", style="llx.dim"))

        if thought_s > 0:
            self._console.print(Text(
                f"{_ICON_THOUGHT} Thought for {turn_status.format_elapsed(thought_s)}", style="llx.dim",
            ))

        if full_text.strip():
            self._console.print(Text(f"{_ICON_ASSISTANT} ", style="llx.brand"), end="")
            # The marker takes two columns of the first line; laid out at full
            # width, that line's last word broke mid-word at the window edge.
            self._console.print(Markdown(full_text), width=max(20, self._console.width - 2))
            hint = _maybe_web_access_hint(full_text)
            if hint:
                self._console.print(f"[llx.dim]{hint}[/llx.dim]")

        # Pictures the turn made are drawn once the live display has stopped,
        # so its redraws cannot overwrite them.
        images = complete.get("generated_images") if isinstance(complete, dict) else None
        if images:
            try:
                from llx.config import get_server_url
                from llx.media_preview import show_generated

                show_generated(images, self.server_url or get_server_url(), self._console)
            except Exception:
                pass

        if error:
            self._console.print(Text(error, style="llx.error"))
            hint = _maybe_web_access_hint(error)
            if hint:
                self._console.print(f"[llx.dim]{hint}[/llx.dim]")

        self._console.print()

    def _start_live(self):
        if not self._live_enabled or self._live is not None:
            return
        # A fresh Live each time: a restarted one would first erase the lines
        # printed while it was stopped (the approval prompt).
        self._live = Live(
            get_renderable=self._view,
            console=self._console,
            refresh_per_second=REFRESH_PER_SECOND,
            transient=True,
        )
        self._live.start(refresh=True)

    def _stop_live(self) -> bool:
        live, self._live = self._live, None
        if live is None:
            return False
        try:
            live.stop()
        except Exception:
            pass
        return True

    def prompt_for_approval(self, data: dict, expected_target: str | None = None) -> bool:
        """Ask the user whether to allow the listed tools.

        MUST be called from the main thread (not a socketio callback). The
        live region is taken down for the question and put back afterwards.
        Raises KeyboardInterrupt if the user aborts at the prompt; the caller
        treats that as 'cancel chat'.
        """
        tools = data.get("tools", [])
        tools_str = ", ".join(tools) if tools else "(unknown tools)"

        live_was_active = self._stop_live()
        if self._live_enabled:
            _set_title("agent — awaiting approval")
            self._title = ""

        import typer
        self._console.print()
        self._console.print("[bold yellow]⚠ Approval Required[/bold yellow]")
        self._console.print(f"  Tool(s): [bold]{tools_str}[/bold]")
        actual_targets = extract_approval_targets(data)
        if expected_target:
            self._console.print(f"  Expected target: [bold]{expected_target}[/bold]")
        if actual_targets:
            self._console.print(f"  Actual target(s): [bold]{', '.join(actual_targets)}[/bold]")

        mismatch, _targets = approval_target_mismatch(data, expected_target)
        if mismatch:
            self._console.print("[red]✗ Rejected: edit target does not match the active file.[/red]\n")
            if live_was_active:
                self._start_live()
            return False

        aborted = False
        approved = False
        try:
            approved = typer.confirm("Allow execution?", default=False)
        except (KeyboardInterrupt, EOFError, typer.Abort):
            aborted = True

        if aborted:
            self._console.print("[red]✗ Aborted.[/red]\n")
        elif approved:
            self._console.print("[green]✓ Approved.[/green]\n")
        else:
            self._console.print("[red]✗ Rejected.[/red]\n")

        # Always resume display so further events can render — even on abort
        if live_was_active:
            self._start_live()

        if aborted:
            raise KeyboardInterrupt
        return approved

    def _print_thinking_trail(self, trail: list[dict]):
        """Print one collapsed thinking-trail block (never per-step accordions)."""
        if not trail:
            return
        n = len(trail)
        label = "step" if n == 1 else "steps"
        self._console.print(
            f"\n[bold dim]▸ Agent thinking[/bold dim] [dim]({n} {label})[/dim]"
        )
        for step in trail:
            iteration = step.get("iteration", "?")
            status = step.get("status") or "thinking"
            self._console.print(Text(f"  [{iteration}] {status}", style="dim"))
            reasoning = (step.get("reasoning") or "").strip()
            if reasoning:
                preview = reasoning[:240] + ("…" if len(reasoning) > 240 else "")
                self._console.print(Text(f"    {preview}", style="dim"))

    # ── Live view ─────────────────────────────────────────────

    def _view(self):
        """Build the live region from the current turn state (runs on Live's thread)."""
        now = time.monotonic()
        width = max(20, self._console.width)
        height = max(8, self._console.height)
        with self._lock:
            state = self._state
            last_step = dict(state.trail[-1]) if state.trail else None
            tools = list(state.tools)
            outputs = [(k, list(v)) for k, v in state.tool_outputs.items() if v]
            thought_s = state.thought_s
            text = state.full_text
            visible = turn_status.status_visible(state, now)
            label = state.label
            detail = state.detail
            elapsed_s = turn_status.elapsed(state, now)

        title = label if visible else "responding…"
        if title != self._title:
            self._title = title
            _set_title(f"agent — {title}")

        parts = []
        if last_step:
            iteration = last_step.get("iteration", "?")
            status = last_step.get("status") or "thinking"
            parts.append(Text(
                f"▸ step {iteration} · {status}", style="llx.dim", no_wrap=True, overflow="ellipsis",
            ))

        if len(tools) > _LIVE_TOOL_LINES:
            parts.append(Text(f"  … {len(tools) - _LIVE_TOOL_LINES} earlier", style="llx.dim"))
            tools = tools[-_LIVE_TOOL_LINES:]
        parts.extend(_tool_text(call, now) for call in tools)

        output_lines = 0
        for tool, chunks in outputs[-1:]:
            out_text = _output_text(chunks)
            output_lines += out_text.count("\n") + 1
            parts.append(Text(f"[{tool} output]\n{out_text}", style="llx.dim"))

        if thought_s > 0:
            parts.append(Text(
                f"{_ICON_THOUGHT} Thought for {turn_status.format_elapsed(thought_s)}", style="llx.dim",
            ))

        status_lines = (2 if detail else 1) if visible else 0
        used = len(parts) + output_lines + status_lines
        if text:
            parts.append(self._tail(text, max(3, height - used - 2), width))

        if visible:
            parts.append(status_renderable(
                self._spinner, label, elapsed_s, hint=self._hint, detail=detail, width=width,
            ))
        return Group(*parts)

    def _tail(self, text: str, max_lines: int, width: int) -> Text:
        """The last ``max_lines`` wrapped lines of ``text``."""
        chunk = text[-(max_lines + 1) * width:]
        lines = Text(chunk).wrap(self._console, width)
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return Text("\n").join(lines)

