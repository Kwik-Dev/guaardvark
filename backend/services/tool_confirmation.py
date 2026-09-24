"""Human-in-the-loop approval for tools that require confirmation.

Flow (streaming chat, web UI or ``llx`` CLI):

1. The chat engine sees a call to a tool with ``requires_confirmation`` and
   calls ``broker.request(...)``, which emits ``chat:confirm_request`` to the
   session room and blocks the chat worker thread (not the server).
2. The client answers with Socket.IO ``chat:confirm_response`` or
   ``POST /api/chat/unified/confirm``; ``broker.resolve(...)`` wakes the waiter.
3. On approval the broker records a single-use *grant* for that exact
   (tool, params) pair. ``ToolRegistry.execute_tool`` consumes the grant; any
   call to a confirmation-requiring tool without a grant is refused. Timeouts,
   aborts and disconnects all resolve to *deny*.

Code paths without a human in the loop (sync chat, agent executor, Celery)
therefore fail closed automatically. Deliberately trusted callers (a human
using the Tools page REST endpoint, the opt-in self-improvement loop) wrap
execution in ``trusted_caller(...)``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

GRANT_TTL_SECONDS = 300
_MAX_PARAM_PREVIEW = 4000

_thread_state = threading.local()


def _params_key(tool_name: str, params: Dict[str, Any]) -> str:
    try:
        canon = json.dumps(params or {}, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        canon = repr(sorted((params or {}).items()))
    return tool_name + ":" + hashlib.sha256(canon.encode()).hexdigest()


def preview_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Params safe to show the user: long strings truncated."""
    out: Dict[str, Any] = {}
    for k, v in (params or {}).items():
        if isinstance(v, str) and len(v) > _MAX_PARAM_PREVIEW:
            out[k] = v[:_MAX_PARAM_PREVIEW] + f"…[{len(v) - _MAX_PARAM_PREVIEW} more chars]"
        else:
            out[k] = v
    return out


@dataclass
class PendingConfirmation:
    confirmation_id: str
    session_id: str
    tool_name: str
    params: Dict[str, Any]
    reason: str
    created_at: float
    timeout: float
    event: threading.Event = field(default_factory=threading.Event)
    approved: Optional[bool] = None
    resolved_by: Optional[str] = None

    def public(self) -> Dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "session_id": self.session_id,
            "tool": self.tool_name,
            "params": preview_params(self.params),
            "reason": self.reason,
            "timeout_s": int(self.timeout),
            "expires_at": self.created_at + self.timeout,
        }


class ConfirmationBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Dict[str, PendingConfirmation] = {}
        self._grants: Dict[str, List[float]] = {}
        self._history: List[Dict[str, Any]] = []

    # ---- asking -------------------------------------------------------
    def request(
        self,
        session_id: str,
        tool_name: str,
        params: Dict[str, Any],
        reason: str,
        emit_fn: Callable[[str, Dict[str, Any]], None],
        timeout: float = 120.0,
        extra: Optional[Dict[str, Any]] = None,
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> Tuple[bool, str]:
        """Ask the user; block until answered. Returns (approved, note)."""
        pending = PendingConfirmation(
            confirmation_id=uuid.uuid4().hex,
            session_id=session_id,
            tool_name=tool_name,
            params=dict(params or {}),
            reason=reason,
            created_at=time.time(),
            timeout=float(timeout),
        )
        with self._lock:
            self._pending[pending.confirmation_id] = pending

        payload = pending.public()
        if extra:
            payload.update(extra)
        try:
            emit_fn("chat:confirm_request", payload)
        except Exception as e:
            logger.error(f"Could not emit confirmation request: {e}")
            self._finish(pending, False, "emit-failed")
            return False, "could not reach the client to ask for approval"

        deadline = pending.created_at + pending.timeout
        while not pending.event.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            if abort_check and abort_check():
                self._finish(pending, False, "aborted")
                break
            pending.event.wait(timeout=min(0.5, remaining))

        if not pending.event.is_set():
            self._finish(pending, False, "timeout")
        approved = bool(pending.approved)
        note = {
            "timeout": "no answer before the approval timed out",
            "aborted": "the chat was stopped",
            "disconnect": "the client disconnected",
        }.get(pending.resolved_by or "", "approved by user" if approved else "denied by user")

        try:
            emit_fn("chat:confirm_resolved", {
                "confirmation_id": pending.confirmation_id,
                "approved": approved,
                "note": note,
            })
        except Exception:
            pass

        if approved:
            self.grant(tool_name, params)
        return approved, note

    # ---- answering ----------------------------------------------------
    def resolve(self, confirmation_id: str, approved: bool,
                allowed_sessions: Optional[set] = None, source: str = "client") -> Tuple[bool, str]:
        """Record the user's answer. ``allowed_sessions`` (if given) must contain
        the confirmation's session id: the responder has to be in that room."""
        with self._lock:
            pending = self._pending.get(confirmation_id or "")
        if not pending:
            return False, "unknown or already-resolved confirmation"
        if allowed_sessions is not None and pending.session_id not in allowed_sessions:
            logger.warning(f"[CONFIRM] Rejected answer for {confirmation_id[:8]} from outside session room")
            return False, "not allowed to answer for this session"
        self._finish(pending, bool(approved), source)
        return True, "ok"

    def cancel_session(self, session_id: str, why: str = "aborted") -> int:
        with self._lock:
            victims = [p for p in self._pending.values() if p.session_id == session_id]
        for p in victims:
            self._finish(p, False, why)
        return len(victims)

    def pending_for(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [p.public() for p in self._pending.values() if p.session_id == session_id]

    def _finish(self, pending: PendingConfirmation, approved: bool, by: str) -> None:
        with self._lock:
            if pending.event.is_set():
                return
            pending.approved = approved
            pending.resolved_by = by
            self._pending.pop(pending.confirmation_id, None)
            self._history.append({
                "ts": time.time(), "session_id": pending.session_id, "tool": pending.tool_name,
                "approved": approved, "by": by,
            })
            del self._history[:-200]
            pending.event.set()
        logger.info(f"[CONFIRM] {pending.tool_name} {'APPROVED' if approved else 'DENIED'} ({by})")

    # ---- grants -------------------------------------------------------
    def grant(self, tool_name: str, params: Dict[str, Any]) -> None:
        key = _params_key(tool_name, params)
        with self._lock:
            self._grants.setdefault(key, []).append(time.time() + GRANT_TTL_SECONDS)

    def consume(self, tool_name: str, params: Dict[str, Any]) -> bool:
        key = _params_key(tool_name, params)
        now = time.time()
        with self._lock:
            live = [t for t in self._grants.get(key, []) if t > now]
            if not live:
                self._grants.pop(key, None)
                return False
            live.pop(0)
            if live:
                self._grants[key] = live
            else:
                self._grants.pop(key, None)
            return True

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._history[-limit:])


_broker: Optional[ConfirmationBroker] = None
_broker_lock = threading.Lock()


def get_confirmation_broker() -> ConfirmationBroker:
    global _broker
    if _broker is None:
        with _broker_lock:
            if _broker is None:
                _broker = ConfirmationBroker()
    return _broker


@contextlib.contextmanager
def trusted_caller(label: str):
    """Run tools without per-call approval (human-initiated or opted-in automation)."""
    prev = getattr(_thread_state, "trusted", None)
    _thread_state.trusted = label
    try:
        yield
    finally:
        _thread_state.trusted = prev


def current_trusted_caller() -> Optional[str]:
    return getattr(_thread_state, "trusted", None)


def approval_required_message(tool_name: str) -> str:
    return (
        f"'{tool_name}' requires the user's approval before it can run, and this "
        f"request path cannot ask for it. Use the streaming chat (web UI or `llx chat`) "
        f"to be prompted, or run it yourself from the Tools page."
    )
