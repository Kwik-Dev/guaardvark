"""Decision telemetry: one JSONL row per routed turn and per decision question.

Rows go to ``<LOG_DIR>/decision_telemetry.jsonl`` on this machine only; nothing
here sends anything anywhere. Two kinds share the file:

* ``turn``: written by ``turn_router.route_turn`` for each chat turn (the route
  taken, the triggers that fired, the facts code computed).
* ``question``: written for each declared decision question asked
  (``decision_data.DECISIONS``), with the model's answer beside the
  deterministic fallback, so shadow runs can be judged against today's rules.

Message and state text is stored only as a short SHA-256 prefix, unless the
llm_debug setting is on (Settings > LLM debug), which already logs full prompts.
Every writer swallows its own failures: telemetry never breaks a turn.

The file is capped at ``MAX_BYTES``; on reaching it the file moves to ``.1``
(and ``.1`` to ``.2``), so at most ``(BACKUP_COUNT + 1) * MAX_BYTES`` stay on
disk. A turn row is 1.1-1.3 KB, so each file holds about 8,000 turns and the
three together a 14-day shadow window at well over 1,000 turns a day.

Set ``GUAARDVARK_DECISION_TELEMETRY=false`` to write nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

TELEMETRY_FILE = "decision_telemetry.jsonl"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 2
ENV_SWITCH = "GUAARDVARK_DECISION_TELEMETRY"

# Tests point this at a temporary file; None means LOG_DIR/TELEMETRY_FILE.
TELEMETRY_PATH: Optional[str] = None

_write_lock = threading.Lock()


def telemetry_enabled() -> bool:
    """False when GUAARDVARK_DECISION_TELEMETRY is set to false, 0, off or no."""
    return os.environ.get(ENV_SWITCH, "true").strip().lower() not in ("false", "0", "off", "no")


def telemetry_path() -> str:
    if TELEMETRY_PATH:
        return TELEMETRY_PATH
    try:
        from backend.config import LOG_DIR
    except Exception:
        LOG_DIR = "logs"
    return os.path.join(LOG_DIR, TELEMETRY_FILE)


def text_sha256(text: Optional[str]) -> str:
    """The 16-hex prefix tier_telemetry.jsonl uses for the same message, so rows join."""
    from backend.services.step_budget import TierTelemetry
    return TierTelemetry.hash_message(text or "")


def state_sha256(state: Optional[Mapping[str, Any]]) -> str:
    """16-hex prefix of a question's state, independent of key order."""
    blob = json.dumps(dict(state or {}), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def llm_debug_on() -> bool:
    """The llm_debug setting; False when it cannot be read."""
    try:
        from backend.utils.settings_utils import get_llm_debug
        return bool(get_llm_debug())
    except Exception:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rotate(path: str) -> None:
    for i in range(BACKUP_COUNT - 1, 0, -1):
        older = f"{path}.{i}"
        if os.path.exists(older):
            os.replace(older, f"{path}.{i + 1}")
    os.replace(path, f"{path}.1")


def append_row(row: Dict[str, Any]) -> bool:
    """Append one row; True when written. Never raises."""
    if not telemetry_enabled():
        return False
    try:
        line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
        path = telemetry_path()
        with _write_lock:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) >= MAX_BYTES:
                _rotate(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        return True
    except Exception as e:
        logger.debug("decision telemetry write failed (non-critical): %s", e)
        return False


def write_question(
    decision: str,
    *,
    request_id: Optional[str] = None,
    mode: str = "off",
    model: Optional[str] = None,
    digest: Optional[str] = None,
    backend: Optional[str] = None,
    state: Optional[Mapping[str, Any]] = None,
    llm_answer: Any = None,
    llm_confidence: Optional[float] = None,
    fallback_answer: Any = None,
    used: Optional[str] = None,
    reason: Optional[str] = None,
    latency_ms: Optional[int] = None,
    llm_debug: Optional[bool] = None,
) -> bool:
    """One 'question' row for a decide() call; True when written. Never raises.

    ``used`` names the answer acted on ("llm" or "fallback"); ``reason`` is the
    outcome code (ok, off, shadow, not_resident, gpu_busy, below_floor,
    parse_fail, timeout, too_long, shadow_skipped). ``agree`` is None when the
    model gave no answer. ``llm_debug`` is the setting as the request thread
    saw it; None reads it here.
    """
    try:
        state = dict(state or {})
        debug = llm_debug_on() if llm_debug is None else bool(llm_debug)
        row: Dict[str, Any] = {
            "kind": "question",
            "ts": _now(),
            "request_id": request_id,
            "decision": decision,
            "mode": mode,
            "model": model,
            "digest": digest,
            "backend": backend,
            "state_sha256": state_sha256(state),
            "state_chars": sum(len(str(v)) for v in state.values()),
            "llm_answer": llm_answer,
            "llm_confidence": llm_confidence,
            "fallback_answer": fallback_answer,
            "agree": None if llm_answer is None else llm_answer == fallback_answer,
            "used": used,
            "reason": reason,
            "latency_ms": latency_ms,
        }
        if debug:
            row["state"] = state
        return append_row(row)
    except Exception as e:
        logger.debug("decision telemetry question row skipped: %s", e)
        return False
