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
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)


def json_chat(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    temperature: float = 0.6,
    on_error: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """One-shot Ollama chat that returns parsed JSON. Best-effort, never raises.

    Forces format="json" so the model emits valid JSON; still guards the parse
    because models occasionally wrap or truncate — falls back to the first
    brace-balanced span. Returns `on_error` (default {}) on any failure.
    """
    import ollama

    if model is None:
        from backend.config import get_default_llm
        model = get_default_llm()

    err = dict(on_error) if on_error else {}
    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format="json",
            options={"temperature": temperature},
        )
    except Exception as e:  # noqa: BLE001 — degrade, don't crash the caller
        logger.error("nl_control_plane.json_chat: ollama.chat failed: %s", e)
        return err

    msg = getattr(response, "message", None)
    if msg is None and isinstance(response, dict):
        msg = response.get("message")
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content", "")
    raw = (content or "").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    logger.warning("nl_control_plane.json_chat: non-JSON response: %s", raw[:200])
    return err


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
