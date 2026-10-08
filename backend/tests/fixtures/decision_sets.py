"""Labelled sets for the decision questions in ``backend/services/decision_data.py``.

One JSON file per decision under ``decisions/``, named by the entry's ``set`` field::

    {"decision": id, "set_version": int, "provenance": str,
     "options": [choice keys] or [true, false],
     "partial": bool (optional; true while the set does not yet carry every hard-negative tag),
     "items": [{"id", "state", "want", "tags", "note" (optional)}]}

``state`` holds exactly the decision's state fields. ``want`` is one of ``options``,
or null for an ambiguous item that is run for information and never scored.

Every item carries at least one of ``BASE_TAGS``, plus any of the decision's
``hard_negatives`` that apply:

    plain      an ordinary case of its label
    contrast   a near miss: shares words or form with a different answer
    idiom      a figure of speech that reads like a request
    code       about programming or configuration
    how_to     asks how to do something rather than asking for it to be done
    injection  a probe with instructions planted in the text, or the clean twin of one
    catch_all  belongs to the "anything else" answer
    ambiguous  either answer is defensible; want is null
    holdout    written after the wording was measured, so the wording was never tuned on it

Labels are synthetic and hand-written for the test, not human-rated.
"""
from __future__ import annotations

import json
from pathlib import Path

SETS_DIR = Path(__file__).resolve().parent / "decisions"

BASE_TAGS = frozenset({
    "plain", "contrast", "idiom", "code", "how_to", "injection", "catch_all", "ambiguous", "holdout",
})


def list_sets() -> list[str]:
    """Names of every labelled set, without the ``.json`` suffix."""
    return sorted(p.stem for p in SETS_DIR.glob("*.json"))


def load_set(name: str) -> dict:
    """Load one set by decision id or by file name (``accepted_offer`` or ``accepted_offer.json``)."""
    stem = name[: -len(".json")] if name.endswith(".json") else name
    return json.loads((SETS_DIR / f"{stem}.json").read_text(encoding="utf-8"))


def scored_items(data: dict) -> list[dict]:
    """The items that count toward accuracy: everything with a ``want``."""
    return [it for it in data["items"] if it["want"] is not None]
