"""Shared contract for agent memory.

Memory has several tiers:

- Turn context: transient prompt data for the current request.
- CLI working memory: session-scoped active files and follow-up targets.
- Session history: `LLMSession` / `LLMMessage` conversation records.
- Durable memory: `AgentMemory` rows recalled across sessions.
- Lessons: structured, user-reviewed procedures stored as durable memories.
- Belief updates: screen-agent contradictions that may later stage fixes.
- Rules/self-knowledge: separate prompt channels that outrank ordinary memory.

This module owns the values and normalization rules shared by APIs, tools,
prompt builders, and UI code so those tiers do not drift.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

MEMORY_TYPES = {
    "fact",
    "preference",
    "note",
    "lesson",
    "lesson_summary",
    "belief_update",
    "snippet",
}

MEMORY_TYPE_ALIASES = {
    "instruction": "note",
    "instructions": "note",
    "procedure": "lesson",
}

MEMORY_SOURCES = {
    "manual",
    "chat",
    "cli",
    "agent",
    "auto",
    "bundle",
    "lesson_summary",
    "learned_from_feedback",
    "candidate_recipe",
}

MEMORY_STATUSES = {"active", "archived", "wrong"}

DEFAULT_IMPORTANCE_BY_TYPE = {
    "fact": 0.85,
    "note": 0.80,
    "preference": 0.65,
    "lesson": 0.75,
    "lesson_summary": 0.75,
    "belief_update": 0.55,
    "snippet": 0.60,
}

SOURCE_TRUST_WEIGHTS = {
    "manual": 1.0,
    "cli": 0.95,
    "bundle": 0.93,
    "chat": 0.88,
    "lesson_summary": 0.86,
    "learned_from_feedback": 0.84,
    "agent": 0.82,
    "candidate_recipe": 0.76,
    "auto": 0.70,
}


def normalize_memory_type(value: Any, default: str = "note") -> str:
    raw = str(value or default).strip().lower()
    raw = MEMORY_TYPE_ALIASES.get(raw, raw)
    return raw if raw in MEMORY_TYPES else default


def normalize_memory_source(value: Any, default: str = "manual") -> str:
    raw = str(value or default).strip().lower()
    return raw if raw in MEMORY_SOURCES else default


def normalize_memory_status(value: Any, default: str = "active") -> str:
    raw = str(value or default).strip().lower()
    return raw if raw in MEMORY_STATUSES else default


def normalize_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        try:
            decoded = json.loads(tags)
            if isinstance(decoded, list):
                tags = decoded
            else:
                tags = tags.split(",")
        except (json.JSONDecodeError, TypeError):
            tags = tags.split(",")
    if not isinstance(tags, (list, tuple, set)):
        return []
    normalized: list[str] = []
    for tag in tags:
        item = str(tag or "").strip().lower()
        if item and item not in normalized:
            normalized.append(item[:80])
    return normalized


def coerce_importance(value: Any, memory_type: str) -> float:
    if value is None:
        return DEFAULT_IMPORTANCE_BY_TYPE.get(memory_type, 0.7)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = DEFAULT_IMPORTANCE_BY_TYPE.get(memory_type, 0.7)
    if math.isnan(numeric):
        numeric = DEFAULT_IMPORTANCE_BY_TYPE.get(memory_type, 0.7)
    return max(0.0, min(1.0, numeric))


def coerce_confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 1.0
    if math.isnan(numeric):
        numeric = 1.0
    return max(0.0, min(1.0, numeric))


def source_trust_weight(source: Any) -> float:
    return SOURCE_TRUST_WEIGHTS.get(str(source or "").lower(), 0.7)


# Words a recall question is built from rather than about: "What is my name?"
# is about "name". They are left out of the SQL prefilter and the match score.
QUERY_STOPWORDS = frozenset(
    "a about all also am an and any are as at be been but by can can't could did didn't do does doesn't "
    "don't for from get had has have how i i'd i'll i'm i've if in into is isn't it its just know let me "
    "my of on or our please remember should so tell than that the their them then there these they "
    "they're this those to us was wasn't we we're were what when where which who whom why will with "
    "won't would you you're your".split()
)

# How many keywords of a query the SQL prefilter ORs together.
MAX_PREFILTER_TERMS = 8

_QUERY_WORD = re.compile(r"[\w./'-]+")


def query_terms(text: str | None) -> list[str]:
    """The keywords of a recall query, lowercased, in order of first appearance.

    Punctuation around a word is dropped ("name?" is "name") and so is a
    possessive "'s" ("Sam's" is "sam"); dots, slashes and hyphens inside a
    word are kept so file names and paths stay whole ("start.sh"). Words under
    three characters and QUERY_STOPWORDS are left out.
    """
    if not text:
        return []
    terms: list[str] = []
    for raw in _QUERY_WORD.findall(text.lower().replace("’", "'")):
        word = raw.strip("./'-")
        if word.endswith("'s"):
            word = word[:-2]
        if len(word) < 3 or word in QUERY_STOPWORDS or word in terms:
            continue
        terms.append(word)
    return terms


def query_tokens(text: str | None) -> set[str]:
    return set(query_terms(text))


def memory_match_score(content: str, tags: list[str], query: str | None) -> float:
    tokens = query_tokens(query)
    if not tokens:
        return 0.0
    haystack = f"{content or ''} {' '.join(tags or [])}".lower()
    hits = sum(1 for token in tokens if token in haystack)
    if hits == 0:
        return 0.0
    return min(1.0, hits / max(3, len(tokens)))


def validate_lesson_payload(payload: Any) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "Lesson content must be a JSON object"
    title = str(payload.get("title") or "").strip()
    if not title:
        return False, "Lesson title is required"
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return False, "Lesson must include at least one step"
    for idx, step in enumerate(steps, start=1):
        if isinstance(step, dict):
            text = str(step.get("text") or step.get("step") or "").strip()
        else:
            text = str(step or "").strip()
        if not text:
            return False, f"Lesson step {idx} is empty"
    parameters = payload.get("parameters", [])
    if parameters is not None and not isinstance(parameters, list):
        return False, "Lesson parameters must be a list"
    return True, ""
