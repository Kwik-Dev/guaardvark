"""Run one characterization row through the turn router and through today's chain.

A row of ``legacy_corpus.json``::

    {"id": str, "message": str, "source": str,
     "options": {request options as a client sends them},
     "env": {omitted keys take ENV_DEFAULTS},
     "expected": {"route", "handler", "tool" (when a tool takes the turn),
                  "prompt_role" and "image" (when the engine's chat path does)}}

``env`` describes the turn's surroundings: ``image`` (a picture attached),
``last_image`` / ``image_focus`` (the session's last picture, and whether it
was made since the last plain chat turn), ``pending_image_prompt`` and
``pending_image_edit`` (a GPU-busy render awaiting "try again"),
``last_assistant_text`` (the reply before this message), ``screen_drivable``,
``media_requires_command``, ``missing_tools``, ``llm_available``,
``tools_available``, ``reflexes_loaded`` and ``force_tier``.

:func:`router_outcome` asks ``turn_router.route_turn(dry=True)``.
:func:`chain_outcome` runs the real ``AgentBrain.process`` and the engine's
real ``_run_chat`` with no model, database or GPU: every place a handler would
start work (a tool run, the screen loop, the Tier 3 executor, the chat
model's history load) raises :class:`RouteTaken` instead, naming the handler.
Both return the same shape, so a row passes when the two agree with
``expected``.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import types
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from unittest import mock

CORPUS_PATH = Path(__file__).resolve().parent / "legacy_corpus.json"
SESSION_ID = "turn-router-corpus"
ATTACHED_IMAGE = "aGVsbG8="  # any base64; nothing decodes it

ENV_DEFAULTS: Dict[str, Any] = {
    "image": False,
    "last_image": False,
    "image_focus": False,
    "pending_image_prompt": None,
    "pending_image_edit": False,
    "last_assistant_text": None,
    "screen_drivable": True,
    "media_requires_command": False,
    "missing_tools": [],
    "llm_available": True,
    "tools_available": True,
    "reflexes_loaded": True,
    "force_tier": None,
}

OUTCOME_KEYS = ("route", "handler", "tool", "prompt_role", "image")


def load_corpus(path: Path = CORPUS_PATH) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["rows"]


def row_env(row: Dict[str, Any]) -> Dict[str, Any]:
    return {**ENV_DEFAULTS, **(row.get("env") or {})}


class RouteTaken(BaseException):
    """Raised where a handler would start work. BaseException, so the chain's
    own ``except Exception`` fall-throughs do not swallow it."""

    def __init__(self, route: str, handler: str, **extra: Any):
        super().__init__(route)
        self.outcome = {"route": route, "handler": handler, **extra}


class _Tool:
    def __init__(self, name: str):
        self.name = name


class FakeRegistry:
    """The tool registry surface the chain touches. Running a tool is a handler
    starting work, so execute_tool raises RouteTaken."""

    def __init__(self, names):
        self.tools = {n: _Tool(n) for n in names}

    def get_tool(self, name):
        return self.tools.get(name)

    def list_tools(self):
        return list(self.tools)

    def __len__(self):
        return len(self.tools)

    def execute_tool(self, name, **params):
        caller = sys._getframe(1).f_code.co_name
        handler = "reflex" if caller == "handler" else caller
        raise RouteTaken("media_control", handler, tool=name)


def all_tool_names() -> set:
    from backend.services.slash_command_executor import SLASH_COMMAND_TOOL_MAP
    from backend.services.turn_router import ROUTING_TOOLS
    from backend.services.unified_chat_engine import WORKSTATION_TOOLS
    return set(ROUTING_TOOLS) | set(SLASH_COMMAND_TOOL_MAP.values()) | set(WORKSTATION_TOOLS)


def make_state(env: Dict[str, Any]):
    """A BrainState as initialize() leaves it, without models or prompts."""
    from backend.services.brain_state import BrainState, _build_default_reflexes

    registry = FakeRegistry(all_tool_names() - set(env["missing_tools"]))
    state = BrainState()
    state.tool_registry = registry
    state.active_model = "test-model"
    state.llm = types.SimpleNamespace(model="test-model")
    state.health.llm_available = bool(env["llm_available"])
    state.health.tools_available = bool(env["tools_available"])
    state.health.reflexes_loaded = bool(env["reflexes_loaded"])
    state.reflexes = _build_default_reflexes(registry) if env["reflexes_loaded"] else []
    return state


@contextlib.contextmanager
def environment(row: Dict[str, Any], image_dir: str, *, telemetry: bool = False) -> Iterator[Dict[str, Any]]:
    """Session image state, the previous reply and settings for one row; the
    decision telemetry stays off unless ``telemetry``."""
    from backend.services import agent_brain as ab
    from backend.services import unified_chat_engine as ue

    env = row_env(row)
    last_path = None
    if env["last_image"] or env["pending_image_edit"]:
        last_path = os.path.join(image_dir, "last.png")
        Path(last_path).write_bytes(b"\x89PNG")
    last_edit = {SESSION_ID: last_path} if env["last_image"] else {}
    focus = {SESSION_ID} if env["image_focus"] else set()
    pending_prompt = {SESSION_ID: env["pending_image_prompt"]} if env["pending_image_prompt"] else {}
    pending_edit = ({SESSION_ID: {"instruction": "make the sky purple", "image": last_path}}
                    if env["pending_image_edit"] else {})
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(ue, "_SESSION_LAST_EDIT", last_edit))
        stack.enter_context(mock.patch.object(ue, "_SESSION_IMAGE_FOCUS", focus))
        stack.enter_context(mock.patch.object(ue, "_SESSION_PENDING_IMAGE_PROMPT", pending_prompt))
        stack.enter_context(mock.patch.object(ue, "_SESSION_PENDING_IMAGE_EDIT", pending_edit))
        stack.enter_context(mock.patch.object(
            ue, "_media_requires_explicit_command", lambda: bool(env["media_requires_command"])))
        stack.enter_context(mock.patch.object(
            ab, "_last_assistant_text", lambda app, sid: env["last_assistant_text"]))
        stack.enter_context(mock.patch.dict(
            os.environ, {"GUAARDVARK_DECISION_TELEMETRY": "true" if telemetry else "false"}))
        yield env


@contextlib.contextmanager
def _swap_modules(replacements: Dict[str, types.ModuleType]) -> Iterator[None]:
    """Stand-ins for a few lazily imported modules, touching only those
    sys.modules entries (patch.dict would also drop every module first
    imported inside the block)."""
    missing = object()
    saved = {name: sys.modules.get(name, missing) for name in replacements}
    sys.modules.update(replacements)
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _outcome(route: str, handler: str, tool: Optional[str] = None, prompt_role: Optional[str] = None,
             image: Optional[bool] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"route": route, "handler": handler}
    if tool:
        out["tool"] = tool
    if prompt_role is not None:
        out["prompt_role"] = prompt_role
        out["image"] = bool(image)
    return out


def router_outcome(row: Dict[str, Any], image_dir: str) -> Dict[str, Any]:
    """The route turn_router gives the row, in the outcome shape."""
    turn = router_turn(row, image_dir)
    args = turn.route_args
    chat_path = turn.route.value in ("chat", "social", "accepted_offer") and args.get("handler") == "engine"
    return _outcome(
        turn.route.value, args.get("handler") or "engine", args.get("tool"),
        args.get("prompt_role") if chat_path else None, args.get("image") if chat_path else None,
    )


def router_turn(row: Dict[str, Any], image_dir: str):
    from backend.services.turn_router import route_turn

    with environment(row, image_dir) as env:
        state = make_state(env)
        return route_turn(
            row["message"], dict(row.get("options") or {}), surface="api",
            request_id="corpus", session_id=SESSION_ID,
            image_data=ATTACHED_IMAGE if env["image"] else None,
            state=state, force_tier=env["force_tier"],
            screen_drivable=lambda: bool(env["screen_drivable"]), dry=True,
        )


# Callers of _run_direct_tool_execution, by the handler that took the turn.
_DIRECT_CALLERS = {
    "process": ("direct_tool", "brain"),
    "_try_image_generate_retry": ("image_retry", "engine"),
    "_try_image_edit_retry": ("edit_retry", "engine"),
    "_try_named_image_direct": ("named_image", "engine"),
    "_try_music_video_direct": ("music_video", "engine"),
    "_try_film_crew_direct": ("film_crew", "engine"),
    "_try_video_generate_direct": ("video", "engine"),
    "_try_image_generate_direct": ("new_image", "engine"),
}


def chain_outcome(row: Dict[str, Any], image_dir: str) -> Dict[str, Any]:
    """Run today's chain on the row and report which handler took the turn."""
    from backend.services import agent_brain as ab
    from backend.services import unified_chat_engine as ue
    from backend.services.agent_brain import AgentBrain

    Engine = ue.UnifiedChatEngine
    original_try_direct = Engine._try_direct_tool
    row_options = dict(row.get("options") or {})

    def run_direct(self, tool_name, params, session_id, emit_fn, request_id, user_message, options=None):
        caller = sys._getframe(1).f_code.co_name
        route, handler = _DIRECT_CALLERS.get(caller, ("direct_tool", "engine"))
        raise RouteTaken(route, handler, tool=tool_name)

    def try_direct(self, message, session_id, options, emit_fn, request_id):
        folded = bool(options.get("direct_tool")) and not row_options.get("direct_tool")
        route = "workstation" if folded else "direct_tool"
        try:
            result = original_try_direct(self, message, session_id, options, emit_fn, request_id)
        except RouteTaken as taken:
            raise RouteTaken(route, "engine", tool=taken.outcome.get("tool")) from None
        if result is not None:
            # The tool is not registered: the engine answers that it is not available.
            from backend.services.slash_command_executor import resolve_slash_direct_tool
            raise RouteTaken(route, "engine", tool=resolve_slash_direct_tool(options)[0])
        return None

    def save_message(self, session_id, role, content, *args, **kwargs):
        frame = sys._getframe(1)
        caller = frame.f_code.co_name
        if caller == "_try_media_direct":
            raise RouteTaken("media_control", "engine", tool=frame.f_locals.get("tool_name"))
        if caller == "_try_image_edit_direct":
            raise RouteTaken("edit_image", "engine", tool="edit_image")
        raise RouteTaken("unexpected_save", caller)

    def usage_notice(self, session_id, emit_fn, request_id, user_message, text):
        caller = sys._getframe(1).f_code.co_name
        route, handler = _DIRECT_CALLERS.get(caller, ("unexpected_notice", caller))
        tool = "generate_music_video" if route == "music_video" else "start_film_crew"
        raise RouteTaken(route, handler, tool=tool)

    def load_history(self, session_id, limit=20):
        opts_seen = getattr(self, "_seen_options", {}) or {}
        if opts_seen.get("accepted_offer"):
            route = "accepted_offer"
        elif self._skip_tools:
            route = "social"
        else:
            route = "chat"
        raise RouteTaken(route, "engine", prompt_role=self._prompt_role, image=bool(self._image_data))

    original_run_chat = Engine._run_chat

    def run_chat(self, session_id, message, options, emit_fn, request_id, steps):
        self._seen_options = options
        return original_run_chat(self, session_id, message, options, emit_fn, request_id, steps)

    class _Screen:
        def __init__(self, *a, **k):
            raise RouteTaken("screen_task", "brain")

    class _Executor:
        def __init__(self, *a, **k):
            raise RouteTaken("deliberate", "brain")

    fake_screen_module = types.ModuleType("backend.services.local_screen_backend")
    fake_screen_module.LocalScreenBackend = _Screen
    fake_director = types.ModuleType("backend.services.media_director")
    fake_director.refine_edit_instruction = lambda text: text
    fake_llm_service = types.ModuleType("backend.utils.llm_service")
    fake_llm_service.get_llm_for_startup = lambda: None

    import backend.services.agent_executor as agent_executor
    import backend.services.hardware_policy as hardware_policy

    with environment(row, image_dir) as env:
        state = make_state(env)
        brain = AgentBrain(state=state)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(ab, "_log_telemetry", lambda t: None))
            stack.enter_context(mock.patch.object(hardware_policy, "_load_hardware", lambda: None))
            stack.enter_context(mock.patch.object(AgentBrain, "_screen_drivable",
                                                  lambda self: bool(env["screen_drivable"])))
            stack.enter_context(mock.patch.object(ue, "get_semantic_selector", lambda: None))
            stack.enter_context(mock.patch.object(Engine, "_run_direct_tool_execution", run_direct))
            stack.enter_context(mock.patch.object(Engine, "_try_direct_tool", try_direct))
            stack.enter_context(mock.patch.object(Engine, "_save_message", save_message))
            stack.enter_context(mock.patch.object(Engine, "_pipeline_usage_notice", usage_notice))
            stack.enter_context(mock.patch.object(Engine, "_load_history", load_history))
            stack.enter_context(mock.patch.object(Engine, "_run_chat", run_chat))
            stack.enter_context(mock.patch.object(
                Engine, "_materialize_attached_image",
                lambda self: "attached.png" if getattr(self, "_image_data", None) else None))
            stack.enter_context(mock.patch.object(agent_executor, "AgentExecutor", _Executor))
            stack.enter_context(_swap_modules({
                "backend.services.local_screen_backend": fake_screen_module,
                "backend.services.media_director": fake_director,
                "backend.utils.llm_service": fake_llm_service,
            }))
            try:
                result = brain.process(
                    session_id=SESSION_ID,
                    message=row["message"],
                    options=dict(row_options),
                    emit_fn=lambda event, payload: None,
                    app=None,
                    image_data=ATTACHED_IMAGE if env["image"] else None,
                    force_tier=env["force_tier"],
                )
            except RouteTaken as taken:
                o = taken.outcome
                return _outcome(o["route"], o["handler"], o.get("tool"), o.get("prompt_role"), o.get("image"))
    error = str((result or {}).get("error") or "")
    if "Model not loaded" in error:
        return _outcome("unavailable", "brain")
    return _outcome("no_handler", "none", error[:80] or None)
