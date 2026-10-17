"""Shared natural-language control plane: classify → normalize → dispatch.

Guaardvark grew the same NL pattern in ~6 places (outreach intent, the media /
video / music-video directors, the orchestrator): freeform text → a local Ollama
JSON classification → a validated structured intent → an intent→handler dispatch.
Until now each feature copy-pasted the primitive. This module is the single
validated implementation new NL-controlled features build on.

The canonical exemplar it generalizes is
`backend/services/social_outreach/intent.py`. Three primitives:

  json_chat(system, user)         → one-shot Ollama call, parsed JSON (tolerant)
  normalize(raw, valid_intents=…) → whitelist intent, clamp confidence, coerce ints
  dispatch(classification, …)     → intent→handler table with a default fallback

Feature-specific validation (platform whitelists, topic denylists, regex
fallbacks) stays in the feature module and layers on top of `normalize`.

``json_chat_result`` is the same call reporting why it failed (timeout, call
error, a reply that is not JSON) instead of folding every failure into
``on_error``; ``decide()`` needs the difference.

``decide(decision, state, fallback=...)`` asks one question declared in
``decision_data.DECISIONS`` and returns a ``DecisionResult``; see its
docstring for the modes and the rule for when the model is asked.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JsonChatResult:
    """One json_chat call: the parsed reply, or why there is none.

    ``failure`` is None on success, else ``"timeout"`` (no reply within the
    timeout), ``"error"`` (the call failed: Ollama down, model missing) or
    ``"parse_fail"`` (a reply came back with no JSON in it). ``raw`` is the
    reply text as received, empty when the call failed.
    """

    data: Any = None
    failure: Optional[str] = None
    error: str = ""
    raw: str = ""


def parse_json_reply(raw: str) -> tuple[bool, Any]:
    """(ok, value) for a model's JSON reply text.

    format="json" usually yields clean JSON, but models occasionally wrap or
    truncate it, so the first-to-last brace span is tried after the whole text.
    """
    raw = (raw or "").strip()
    try:
        return True, json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return True, json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return False, None


# One client per (host, timeout): building an httpx client per call costs more
# than the decisions it carries.
_clients: dict = {}
_clients_lock = threading.Lock()


def _client_for(timeout: float):
    import ollama
    from backend.utils.ollama_resource_manager import get_ollama_base_url

    key = (get_ollama_base_url(), float(timeout))
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            client = ollama.Client(host=key[0], timeout=key[1])
            _clients[key] = client
    return client


def _is_timeout(exc: BaseException) -> bool:
    try:
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return True
    except ImportError:  # pragma: no cover - httpx ships with the ollama client
        pass
    return isinstance(exc, TimeoutError)


def json_chat_result(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    temperature: float = 0.6,
    timeout: Optional[float] = None,
    num_ctx: Optional[int] = None,
) -> JsonChatResult:
    """One-shot Ollama chat with format="json". Never raises.

    ``timeout`` (seconds) bounds the call through a client built with that
    timeout; None keeps the module-level ``ollama.chat``, which waits as long
    as Ollama takes. ``num_ctx`` overrides the sized context; pass the
    context length the model is already loaded with, since a request asking
    for another size makes Ollama reload the runner.
    """
    import ollama
    from backend.utils.ollama_resource_manager import think_payload, request_options

    if model is None:
        from backend.config import get_default_llm
        model = get_default_llm()

    try:
        chat = _client_for(timeout).chat if timeout is not None else ollama.chat
        response = chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format="json",
            options=request_options(model, temperature=temperature, num_ctx=num_ctx),
            **think_payload(model),
        )
    except Exception as e:  # noqa: BLE001 — degrade, don't crash the caller
        failure = "timeout" if _is_timeout(e) else "error"
        logger.error("nl_control_plane.json_chat: ollama.chat failed (%s): %s", failure, e)
        return JsonChatResult(failure=failure, error=str(e))

    msg = getattr(response, "message", None)
    if msg is None and isinstance(response, dict):
        msg = response.get("message")
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content", "")
    raw = (content or "").strip()

    ok, value = parse_json_reply(raw)
    if ok:
        return JsonChatResult(data=value, raw=raw)
    logger.warning("nl_control_plane.json_chat: non-JSON response: %s", raw[:200])
    return JsonChatResult(failure="parse_fail", error="reply is not JSON", raw=raw)


def json_chat(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    temperature: float = 0.6,
    on_error: Optional[dict[str, Any]] = None,
    timeout: Optional[float] = None,
    num_ctx: Optional[int] = None,
) -> dict[str, Any]:
    """One-shot Ollama chat that returns parsed JSON. Best-effort, never raises.

    Forces format="json" so the model emits valid JSON; still guards the parse
    because models occasionally wrap or truncate — falls back to the first
    brace-balanced span. Returns `on_error` (default {}) on any failure.
    ``timeout`` and ``num_ctx`` are as in :func:`json_chat_result`; leaving
    them out keeps the call exactly as it always was.
    """
    result = json_chat_result(
        system, user, model=model, temperature=temperature, timeout=timeout, num_ctx=num_ctx,
    )
    if result.failure is None:
        return result.data
    return dict(on_error) if on_error else {}


def normalize(
    raw: Mapping[str, Any],
    *,
    valid_intents: Iterable[str],
    default_intent: str = "refuse",
    int_fields: Iterable[str] = (),
    confidence_bounds: tuple[float, float] = (0.0, 1.0),
) -> dict[str, Any]:
    """Defensive normalization of a classifier's raw dict.

    Generic, feature-agnostic: whitelist `intent` (unknown → default_intent),
    clamp `confidence` into bounds, coerce named `int_fields` to int-or-None.
    Feature-specific fields (platform, topics, …) pass through untouched for the
    caller to validate. Returns a shallow copy — never mutates `raw`.
    """
    valid = frozenset(valid_intents)
    out: dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}

    intent = str(out.get("intent") or default_intent).strip().lower()
    out["intent"] = intent if intent in valid else default_intent

    lo, hi = confidence_bounds
    try:
        c = float(out.get("confidence")) if out.get("confidence") is not None else 0.0
    except (TypeError, ValueError):
        c = 0.0
    out["confidence"] = max(lo, min(hi, c))

    for f in int_fields:
        v = out.get(f)
        if v is None:
            continue
        try:
            out[f] = int(v)
        except (TypeError, ValueError):
            out[f] = None

    return out


def dispatch(
    classification: Mapping[str, Any],
    handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]],
    *,
    default_handler: Callable[[Mapping[str, Any]], Any],
    intent_key: str = "intent",
) -> Any:
    """Route a normalized classification to its handler.

    handlers maps intent → callable(classification). Anything not in the table
    (including a refused/unknown intent) goes to default_handler — so a missing
    branch degrades to the safe default rather than raising.
    """
    intent = classification.get(intent_key)
    return handlers.get(intent, default_handler)(classification)


# ---------------------------------------------------------------------------
# decide(): one declared closed question, answered by the chat model that is
# already loaded, or by today's code whenever the model may not or cannot.

REASONS = (
    "ok",               # the model answered and its answer is used
    "off",              # the decision is off: the fallback, nothing asked
    "shadow",           # shadow mode: the fallback is used
    "unknown",          # no such decision is declared
    "guard_caller",     # called from a guard (decision_data.GUARD_MODULES)
    "below_floor",      # no measured row for the loaded build qualifies; on acts as shadow
    "error",            # something failed inside decide()
)


@dataclass(frozen=True)
class DecisionResult:
    """What decide() answered and why.

    ``answer`` is what the caller acts on. ``source`` says where it came
    from: ``"llm"`` (the model), ``"fallback"`` (the caller's own answer,
    today's code). ``reason`` is one of REASONS. ``model`` and ``digest`` name
    the build that was asked, when one was.
    """

    decision: str
    answer: Any
    source: str
    reason: str
    model: Optional[str] = None
    confidence: Optional[float] = None
    latency_ms: int = 0
    digest: Optional[str] = None


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_THIS_FILE = os.path.abspath(__file__)

# Called with (event, state) for every decision that is not off. Nothing is
# installed by default; a telemetry writer installs itself with
# set_decision_recorder. The event never carries the state's text, only its
# hash and size; the writer decides whether to keep the text.
_recorder: Optional[Callable[[dict, Mapping[str, Any]], None]] = None


def set_decision_recorder(recorder: Optional[Callable[[dict, Mapping[str, Any]], None]]) -> None:
    """Install the function that records each decision made in shadow or on; None removes it."""
    global _recorder
    _recorder = recorder


def _record(event: dict, state: Mapping[str, Any]) -> None:
    recorder = _recorder
    if recorder is None:
        return
    try:
        recorder(event, state)
    except Exception as e:  # noqa: BLE001 - a recorder failure never changes an answer
        logger.warning("decide: recording %s failed: %s", event.get("decision"), e)


def guard_for(filename: str, function: str) -> Optional[str]:
    """The GUARD_MODULES entry code in ``filename``/``function`` belongs to, or None."""
    from backend.services.decision_data import GUARD_MODULES

    try:
        rel = os.path.relpath(os.path.abspath(filename), _REPO_ROOT)
    except ValueError:  # another drive on Windows: not in this repository
        return None
    if rel.startswith(".."):
        return None
    rel = rel.replace(os.sep, "/")
    for guard, functions in GUARD_MODULES.items():
        hit = rel.startswith(guard) if guard.endswith("/") else rel == guard
        if hit and (functions is None or function in functions):
            return f"{rel}:{function}" if functions else rel
    return None


def _guard_caller() -> Optional[str]:
    """The guard that called decide(), skipping frames in this module, or None."""
    frame = sys._getframe(1)
    while frame is not None and os.path.abspath(frame.f_code.co_filename) == _THIS_FILE:
        frame = frame.f_back
    if frame is None:
        return None
    return guard_for(frame.f_code.co_filename, frame.f_code.co_name)


def _ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _state_digest(state: Any) -> tuple[str, int]:
    """(sha256 prefix, characters) of the state as sent, for records without its text."""
    try:
        text = json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = repr(state)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], len(text)


def decide(
    decision: str,
    state: Mapping[str, Any],
    *,
    fallback: Any,
    timeout_s: float = 2.0,
    ctx: Optional[dict] = None,
) -> DecisionResult:
    """Answer the declared closed question ``decision`` about ``state``.

    ``fallback`` is the answer today's code gives; the caller computes it
    first and acts on ``result.answer``. In off (the default for every
    decision) the fallback comes back and nothing else happens: no model, no
    network, no record. decide() never raises; any failure inside it returns
    the fallback with reason "error".
    """
    started = time.perf_counter()
    try:
        from backend.services.decision_data import DECISIONS
        from backend.services.decision_settings import get_decision_mode

        entry = DECISIONS.get(decision)
        if entry is None:
            logger.warning("decide: no decision named %r is declared", decision)
            return DecisionResult(decision, fallback, "fallback", "unknown", latency_ms=_ms(started))
        guard = _guard_caller()
        if guard:
            logger.error("decide(%s) was called from the guard %s; a guard never asks a model", decision, guard)
            return DecisionResult(decision, fallback, "fallback", "guard_caller", latency_ms=_ms(started))
        mode = get_decision_mode(decision)
        if mode == "off":
            return DecisionResult(decision, fallback, "fallback", "off", latency_ms=_ms(started))
        reason = "shadow" if mode == "shadow" else "below_floor"
        result = DecisionResult(decision, fallback, "fallback", reason, latency_ms=_ms(started))
        sha, chars = _state_digest(state)
        _record({
            "ts": time.time(), "decision": decision, "layer": entry["layer"], "mode": mode,
            "reason": result.reason, "source": result.source, "state_sha256": sha, "state_chars": chars,
            "fallback_answer": fallback, "used_answer": result.answer, "latency_ms": result.latency_ms,
            "request_id": (ctx or {}).get("request_id"),
        }, state)
        return result
    except Exception as e:  # noqa: BLE001 - decide() never raises
        logger.error("decide(%s) failed: %s", decision, e, exc_info=True)
        return DecisionResult(decision, fallback, "fallback", "error", latency_ms=_ms(started))
