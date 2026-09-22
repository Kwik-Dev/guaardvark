"""The thumbs teach.

A thumb names one reply (its database row). The reply carries its provenance:
the memories its prompt was built from, the recipe a screen task used, the rule
that shaped the persona. A verdict flows back to those sources through levers
the system already has (memory confidence and status, recipe matching, lesson
distillation, recipe induction), and every effect is written to a ledger on
the feedback row so an un-thumb reverses exactly what the thumb did.

The "why" (reason text and tags) is part of the same channel: stored on the
row, read by the strong-positive detector, and turned into a memory the next
prompt sees. The input UI comes later; nothing here waits for it.

Thresholds live in THRESHOLDS with the reasoning next to each number.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

VERDICTS = ("up", "down", "none")

# One table, one place. Change a number here with a new measurement or a new
# operator decision, never at a call site.
THRESHOLDS: Dict[str, Any] = {
    # Memory blame is an exponential move of `confidence` toward 0: one
    # unanswered thumbs-down leaves 1.0 at 0.75 (above the archive floor, so a
    # mis-click buries nothing); four in a row reach 0.32.
    "MEM_ALPHA_DOWN": 0.25,
    # Credit moves toward 1.0 in smaller steps: it is frequent and cheap, and a
    # blamed memory should recover gradually, not in one click.
    "MEM_ALPHA_UP": 0.10,
    # A thumb judges the prompt as a whole. With n memories behind it each one
    # takes alpha * min(1, SPREAD / n), so the total blame budget per thumb is
    # constant instead of growing with the memory block.
    "MEM_BLAME_SPREAD": 3,
    # Archive only when confidence has fallen this low AND the net verdict is
    # this negative. Both gates: repeated blame, never one bad turn.
    "MEM_ARCHIVE_CONF": 0.35,
    "MEM_ARCHIVE_NET": 3,
    # `wrong` is a human verdict (the memory page). Automatic action stops at
    # `archived`, which the same page restores in one click.
    "MEM_NEVER_WRONG": True,
    # A correction written from a why-text ranks with a user-typed note (the
    # note default importance) and starts a little below full confidence so
    # later blame has room to act on it too.
    "MEM_CORRECTION_IMPORTANCE": 0.80,
    "MEM_CORRECTION_CONFIDENCE": 0.90,
    # Operator decision 2026-09-22: a thumbs-down on a run that executed a
    # recipe disables that recipe. Un-thumb re-enables it.
    "RECIPE_DISABLE_ON_DOWN": 1,
    # A recipe that fell back to the vision loop this many times is a bad
    # shortcut even without a thumb; a thumbs-down on a fallback run counts
    # here rather than disabling outright, because the loop produced the
    # outcome, not the recipe.
    "RECIPE_DISABLE_ON_FALLBACKS": 2,
    # An auto-induced recipe stays provisional until two clean thumbs-up.
    "RECIPE_GRADUATE_UPS": 2,
    # How long the "taught" line stays under a message, in milliseconds.
    "TAUGHT_NOTE_MS": 5000,
}


# ---------------------------------------------------------------------------
# Resolution: which reply is this thumb about
# ---------------------------------------------------------------------------

def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def resolve_message(data: Dict[str, Any]) -> Tuple[Optional[Any], str]:
    """Find the LLMMessage a thumb refers to.

    By id first; then by the turn's request id inside the session's recent
    assistant rows (the client may thumb before chat:message_saved lands);
    then the legacy content-prefix match, which is what every thumb used
    before rows carried ids. Returns (row or None, how it was resolved).
    """
    from backend.models import LLMMessage

    session_id = data.get("session_id") or None
    mid = data.get("message_id")
    if mid not in (None, ""):
        try:
            row = LLMMessage.query.get(int(mid))
        except (TypeError, ValueError):
            row = None
        if row is not None and (not session_id or row.session_id == session_id):
            return row, "message_id"

    rid = (data.get("request_id") or "").strip()
    if rid and session_id:
        recent = (
            LLMMessage.query
            .filter(LLMMessage.session_id == session_id, LLMMessage.role == "assistant")
            .order_by(LLMMessage.timestamp.desc())
            .limit(30)
            .all()
        )
        for row in recent:
            prov = ((row.extra_data or {}).get("provenance") or {}) if isinstance(row.extra_data, dict) else {}
            if prov.get("request_id") == rid:
                return row, "request_id"

    task = (data.get("task") or "").strip()
    if task and session_id:
        kind = (data.get("kind") or "response")
        role = "assistant"
        if kind.startswith("tool:") and data.get("type") == "tool_action_user":
            role = "user"
        row = (
            LLMMessage.query
            .filter(
                LLMMessage.session_id == session_id,
                LLMMessage.role == role,
                LLMMessage.content.like(_escape_like(task[:100]) + "%", escape="\\"),
            )
            .order_by(LLMMessage.timestamp.desc())
            .first()
        )
        if row is not None:
            return row, "prefix"
    return None, "none"


def preceding_user_message(row) -> Optional[Any]:
    """The user turn this reply answered."""
    from backend.models import LLMMessage
    if row is None:
        return None
    return (
        LLMMessage.query
        .filter(
            LLMMessage.session_id == row.session_id,
            LLMMessage.role == "user",
            LLMMessage.timestamp <= row.timestamp,
            LLMMessage.id != row.id,
        )
        .order_by(LLMMessage.timestamp.desc(), LLMMessage.id.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# The feedback row
# ---------------------------------------------------------------------------

def normalise_verdict(data: Dict[str, Any]) -> Optional[str]:
    v = data.get("verdict")
    if v is None and "positive" in data:
        v = "up" if data.get("positive") else "down"
    v = str(v or "").strip().lower()
    return v if v in VERDICTS else None


def _tags(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    out = []
    for t in value:
        t = str(t or "").strip().lower()[:40]
        if t and t not in out:
            out.append(t)
    return out[:12]


def upsert_feedback(row, data: Dict[str, Any], verdict: str) -> Tuple[Any, Optional[str]]:
    """One feedback row per (message, kind). Returns (row, previous verdict)."""
    from backend.models import db, ToolFeedback

    kind = (data.get("kind") or "response").strip()[:120]
    fb = None
    if row is not None:
        fb = ToolFeedback.query.filter_by(message_id=row.id, kind=kind).first()
    previous = None
    if fb is None:
        fb = ToolFeedback(
            session_id=data.get("session_id") or (row.session_id if row is not None else None),
            lesson_id=data.get("lesson_id") or None,
            tool_name=(data.get("tool_name") or (data.get("task") or "")[:100] or kind)[:100],
            task=data.get("task") or (row.content[:200] if row is not None else ""),
            positive=(verdict == "up"),
            steps=data.get("steps"),
            time_seconds=data.get("time_seconds"),
            model=(data.get("model") or "")[:100] or None,
            message_id=(row.id if row is not None else None),
            request_id=((data.get("request_id") or "") or _request_id_of(row))[:64] or None,
            kind=kind,
            provenance=_provenance_of(row),
            applied=[],
        )
        db.session.add(fb)
    else:
        previous = fb.verdict or ("up" if fb.positive else "down")
        if fb.retracted_at is not None:
            previous = "none"
    fb.verdict = verdict
    if verdict != "none":
        fb.positive = (verdict == "up")
        fb.retracted_at = None
    else:
        fb.retracted_at = datetime.now()
    if data.get("lesson_id"):
        fb.lesson_id = data.get("lesson_id")
    if "why_text" in data:
        fb.why_text = (data.get("why_text") or "").strip() or None
    if "why_tags" in data:
        fb.why_tags = _tags(data.get("why_tags"))
    fb.updated_at = datetime.now()
    db.session.commit()
    return fb, previous


def _request_id_of(row) -> str:
    if row is None or not isinstance(row.extra_data, dict):
        return ""
    return str(((row.extra_data.get("provenance") or {}).get("request_id")) or "")


def _provenance_of(row) -> Dict[str, Any]:
    if row is None or not isinstance(row.extra_data, dict):
        return {}
    prov = row.extra_data.get("provenance") or {}
    return dict(prov) if isinstance(prov, dict) else {}


def stamp_message(row, verdict: str, feedback_id: Optional[int]) -> None:
    """The icon state the client hydrates from after a refresh."""
    if row is None:
        return
    from backend.models import db
    try:
        extra = dict(row.extra_data or {})
        if verdict == "none":
            extra.pop("feedback", None)
            extra.pop("feedback_id", None)
        else:
            extra["feedback"] = verdict
            if feedback_id is not None:
                extra["feedback_id"] = feedback_id
        row.extra_data = extra
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[FEEDBACK] could not stamp message {getattr(row, 'id', '?')}: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass


def feedback_log_path():
    from pathlib import Path
    from backend.config import GUAARDVARK_ROOT
    return Path(GUAARDVARK_ROOT) / "data" / "training" / "knowledge" / "feedback.jsonl"


def append_jsonl(entry: Dict[str, Any]) -> None:
    """Append-only log; one line per event, the same fields the row has."""
    try:
        path = feedback_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[FEEDBACK] jsonl append failed: {e}")


def log_entry(fb, event: str, data: Dict[str, Any], taught: List[Dict[str, Any]],
              resolved_by: str) -> Dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(),
        "epoch": time.time(),
        "event": event,
        "feedback_id": getattr(fb, "id", None),
        "message_id": getattr(fb, "message_id", None),
        "request_id": getattr(fb, "request_id", None),
        "kind": getattr(fb, "kind", None),
        "verdict": getattr(fb, "verdict", None),
        "positive": bool(getattr(fb, "positive", False)),
        "task": getattr(fb, "task", "") or "",
        "type": data.get("type", "tool_action" if str(getattr(fb, "kind", "")).startswith("tool:") else "response"),
        "session_id": getattr(fb, "session_id", None),
        "lesson_id": getattr(fb, "lesson_id", None),
        "steps": getattr(fb, "steps", None),
        "time_seconds": getattr(fb, "time_seconds", None),
        "why_text": getattr(fb, "why_text", None),
        "why_tags": getattr(fb, "why_tags", None) or [],
        "comment": getattr(fb, "why_text", None) or "",
        "model": getattr(fb, "model", None) or "",
        "provenance": getattr(fb, "provenance", None) or {},
        "resolved_by": resolved_by,
        "taught": taught,
    }


# ---------------------------------------------------------------------------
# Teaching
# ---------------------------------------------------------------------------

def _norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _memory_counter(mem, field: str, delta: int) -> None:
    extra = dict(mem.extra_data or {})
    fbk = dict(extra.get("feedback") or {})
    fbk[field] = max(0, int(fbk.get(field) or 0) + delta)
    fbk["last_feedback_at"] = datetime.now().isoformat()
    extra["feedback"] = fbk
    mem.extra_data = extra


def _teach_memories(fb, verdict: str, ledger: List[Dict[str, Any]], taught: List[Dict[str, Any]]) -> None:
    """Credit or blame the memories the reply's prompt was built from."""
    from backend.models import db, AgentMemory
    from backend.api.memory_api import _write_memory_audit

    ids = [i for i in ((fb.provenance or {}).get("memory_ids") or []) if i]
    if not ids:
        return
    rows = [m for m in (AgentMemory.query.filter(AgentMemory.id.in_(ids)).all()) if m.status == "active"]
    if not rows:
        return
    n = len(rows)
    alpha = THRESHOLDS["MEM_ALPHA_UP"] if verdict == "up" else THRESHOLDS["MEM_ALPHA_DOWN"]
    alpha_eff = alpha * min(1.0, THRESHOLDS["MEM_BLAME_SPREAD"] / n)
    target = 1.0 if verdict == "up" else 0.0
    counter = "ups" if verdict == "up" else "downs"
    archived = []
    for m in rows:
        before = {"confidence": m.confidence, "status": m.status}
        conf = float(m.confidence if m.confidence is not None else 1.0)
        delta = alpha_eff * (target - conf)
        m.confidence = max(0.0, min(1.0, conf + delta))
        _memory_counter(m, counter, 1)
        fbk = (m.extra_data or {}).get("feedback") or {}
        fbk["last_verdict"] = verdict
        ledger.append({"kind": "memory_confidence", "ref": m.id, "delta": round(delta, 6)})
        ledger.append({"kind": "memory_counter", "ref": m.id, "field": counter, "delta": 1})
        if (verdict == "down"
                and m.confidence < THRESHOLDS["MEM_ARCHIVE_CONF"]
                and int(fbk.get("downs") or 0) - int(fbk.get("ups") or 0) >= THRESHOLDS["MEM_ARCHIVE_NET"]):
            ledger.append({"kind": "memory_archived", "ref": m.id, "prev_status": m.status})
            m.status = "archived"
            archived.append(m)
        _write_memory_audit("feedback_" + verdict, m.id, before=before,
                            after={"confidence": m.confidence, "status": m.status}, actor="feedback")
    db.session.commit()
    word = "credited" if verdict == "up" else "blamed"
    taught.append({"kind": "memory_credit" if verdict == "up" else "memory_blame",
                   "ref": ",".join(m.id for m in rows),
                   "label": f"{n} memor{'y' if n == 1 else 'ies'} {word}"})
    for m in archived:
        taught.append({"kind": "memory_archived", "ref": m.id,
                       "label": f"memory archived: {(m.content or '')[:40]}"})


def _teach_recipe(fb, verdict: str, ledger: List[Dict[str, Any]], taught: List[Dict[str, Any]]) -> None:
    from backend.services import recipe_stats
    rec = (fb.provenance or {}).get("recipe") or {}
    name = rec.get("name")
    if not name:
        return
    fallback = bool(rec.get("fallback"))
    counter = "ups" if verdict == "up" else "downs"
    entry = recipe_stats.bump(name, **{counter: 1})
    ledger.append({"kind": "recipe_counter", "ref": name, "field": counter, "delta": 1})
    taught.append({"kind": "recipe_stat", "ref": name,
                   "label": f"recipe {name}: {entry['ups']} up / {entry['downs']} down"})
    if verdict == "down":
        should_disable = (not fallback and THRESHOLDS["RECIPE_DISABLE_ON_DOWN"] <= 1) or (
            fallback and int(entry.get("fallbacks") or 0) >= THRESHOLDS["RECIPE_DISABLE_ON_FALLBACKS"])
        if should_disable and not entry.get("disabled"):
            recipe_stats.set_disabled(name, True, reason=f"thumbs_down:{fb.id}")
            ledger.append({"kind": "recipe_disabled", "ref": name, "prev": False})
            taught.append({"kind": "recipe_disabled", "ref": name,
                           "label": f"recipe {name} disabled (un-thumb to restore)"})
    elif entry.get("provisional") and entry["ups"] >= THRESHOLDS["RECIPE_GRADUATE_UPS"] and entry["downs"] == 0:
        recipe_stats.set_provisional(name, False)
        ledger.append({"kind": "recipe_graduated", "ref": name})
        taught.append({"kind": "recipe_graduated", "ref": name, "label": f"recipe {name} is no longer provisional"})


def _teach_from_why(fb, row, user_row, verdict: str, ledger: List[Dict[str, Any]], taught: List[Dict[str, Any]]) -> None:
    """A stated reason becomes a memory the next prompt sees."""
    from backend.models import db, AgentMemory
    from backend.api.memory_api import add_memory
    why = (fb.why_text or "").strip()
    if not why:
        return
    label = "correction" if verdict == "down" else "keep"
    norm = _norm_text(why)
    for m in AgentMemory.query.filter_by(source="learned_from_feedback", status="active").all():
        if _norm_text(m.content) == norm:
            _memory_counter(m, "ups", 1)
            db.session.commit()
            ledger.append({"kind": "memory_counter", "ref": m.id, "field": "ups", "delta": 1})
            taught.append({"kind": "memory_reinforced", "ref": m.id, "label": f"{label} reinforced"})
            return
    mem = add_memory(
        content=why,
        memory_type="note",
        source="learned_from_feedback",
        importance=THRESHOLDS["MEM_CORRECTION_IMPORTANCE"],
        confidence=THRESHOLDS["MEM_CORRECTION_CONFIDENCE"],
        tags=["feedback", label] + list(fb.why_tags or []),
        session_id=None,
        project_id=getattr(row, "project_id", None),
        metadata={
            "feedback_id": fb.id, "message_id": fb.message_id, "request_id": fb.request_id,
            "verdict": verdict, "excerpt": (getattr(row, "content", "") or "")[:200],
            "task": (getattr(user_row, "content", "") or "")[:200],
        },
    )
    if mem is not None:
        ledger.append({"kind": "memory_created", "ref": mem.id})
        taught.append({"kind": "memory_created", "ref": mem.id, "label": f"{label} saved: {why[:50]}"})


def apply(fb, row, user_row, app=None) -> List[Dict[str, Any]]:
    """Make the verdict count. Returns the `taught` list for the client and
    records each effect in fb.applied so retract() can undo it."""
    from backend.models import db
    verdict = fb.verdict
    if verdict not in ("up", "down"):
        return []
    ledger: List[Dict[str, Any]] = list(fb.applied or [])
    taught: List[Dict[str, Any]] = []
    for step in (_teach_memories, _teach_recipe):
        try:
            step(fb, verdict, ledger, taught)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[FEEDBACK] {step.__name__} failed (non-fatal): {e}", exc_info=True)
            try:
                db.session.rollback()
            except Exception:
                pass
    try:
        _teach_from_why(fb, row, user_row, verdict, ledger, taught)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[FEEDBACK] why-text memory failed (non-fatal): {e}", exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
    fb.applied = ledger
    db.session.commit()
    return taught


def retract(fb) -> List[Dict[str, Any]]:
    """Undo everything a previous verdict applied, in reverse order."""
    from backend.models import db, AgentMemory
    from backend.services import recipe_stats
    taught: List[Dict[str, Any]] = []
    kept = []
    for entry in reversed(list(fb.applied or [])):
        kind = entry.get("kind")
        ref = entry.get("ref")
        try:
            if kind == "memory_confidence":
                m = db.session.get(AgentMemory, ref)
                if m is not None:
                    m.confidence = max(0.0, min(1.0, float(m.confidence or 0.0) - float(entry.get("delta") or 0.0)))
            elif kind == "memory_counter":
                m = db.session.get(AgentMemory, ref)
                if m is not None:
                    _memory_counter(m, entry.get("field") or "ups", -int(entry.get("delta") or 0))
            elif kind == "memory_archived":
                m = db.session.get(AgentMemory, ref)
                if m is not None and m.status == "archived":
                    m.status = entry.get("prev_status") or "active"
            elif kind == "memory_created":
                m = db.session.get(AgentMemory, ref)
                if m is not None:
                    untouched = (m.source == "learned_from_feedback"
                                 and (m.updated_at is None or m.created_at is None
                                      or (m.updated_at - m.created_at).total_seconds() < 1.0))
                    if untouched:
                        db.session.delete(m)
                    else:
                        kept.append(ref)
            elif kind == "recipe_counter":
                recipe_stats.bump(ref, **{entry.get("field") or "ups": -int(entry.get("delta") or 0)})
            elif kind == "recipe_disabled":
                recipe_stats.set_disabled(ref, bool(entry.get("prev")), reason=None)
            elif kind == "recipe_graduated":
                recipe_stats.set_provisional(ref, True)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[FEEDBACK] retract {kind} {ref} failed: {e}")
    fb.applied = []
    db.session.commit()
    taught.append({"kind": "retracted", "ref": str(fb.id), "label": "feedback withdrawn"})
    for ref in kept:
        taught.append({"kind": "memory_kept", "ref": ref, "label": "correction kept (edited)"})
    return taught
