"""Class anchor + short vision marks for cast LoRA train/infer prompts.

Without a class token, Z-Image treats a name-like trigger + scene as free rein
(cartoon mascots for humans; wrong species for animals). Class comes from
vision consensus when available (man / woman / white wolf / robot / …) — not
from a hand-built feature checklist.
"""
from __future__ import annotations

import re
from typing import Any, Optional

_CLASS_HUMAN = frozenset({"man", "woman", "person", "boy", "girl"})
# Back-compat alias
_CLASS_OK = _CLASS_HUMAN

_MALE_RE = re.compile(
    r"\b(man|male|gentleman|guy|he\b|his\b|beard|stubble|mustache|moustache)\b",
    re.I,
)
_FEMALE_RE = re.compile(
    r"\b(woman|female|lady|she\b|her\b|girl)\b",
    re.I,
)
# Common non-human classes vision may name — prefer these over "person".
_CREATURE_RE = re.compile(
    r"\b((?:arctic |white |grey |gray |black |brown |red |timber )?"
    r"(?:wolf|wolves|dog|cat|fox|bear|horse|dragon|bird|tiger|lion|"
    r"rabbit|deer|owl|raven|eagle|shark|dinosaur|robot|android|mechs?|"
    r"alien|monster|creature|golem|skeleton|zombie|vampire|witch|"
    r"knight|samurai|ninja))\b",
    re.I,
)
_REJECT_CLASS = frozenset({
    "", "none", "null", "unknown", "n/a", "na", "character", "subject",
    "person?", "thing", "object", "image", "photo",
})


def sanitize_class_token(raw: str | None) -> str:
    """Normalize a vision/operator class phrase for prompt anchoring.

    Allows multi-word species like ``white wolf`` (not only man/woman/person).
    """
    t = (raw or "").strip().lower()
    t = t.replace("_", " ")
    t = re.sub(r"[^a-z0-9\s\-]", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -")
    if t in _REJECT_CLASS:
        return "person"
    # Drop leading articles
    t = re.sub(r"^(a|an|the)\s+", "", t)
    words = [w for w in t.split() if w][:4]
    if not words:
        return "person"
    out = " ".join(words)[:40].strip()
    return out or "person"


def resolve_class_token(
    subject: Any = None,
    *,
    class_token: str | None = None,
    tags: list[str] | None = None,
    description: str | None = None,
    bible: str | None = None,
) -> str:
    """Return class anchor: vision-stored token, creature noun, or man/woman/person."""
    explicit = (class_token or "").strip()
    if not explicit and subject is not None:
        cfg = getattr(subject, "training_settings_json", None) or {}
        explicit = (cfg.get("class_token") or "").strip()
    if explicit:
        return sanitize_class_token(explicit)

    blob_parts: list[str] = []
    if tags:
        blob_parts.extend(str(t) for t in tags)
    if subject is not None:
        cfg = getattr(subject, "training_settings_json", None) or {}
        blob_parts.extend(str(t) for t in (cfg.get("bible_vision_tags") or []))
        blob_parts.append(getattr(subject, "description", None) or "")
        blob_parts.append(getattr(subject, "bible", None) or "")
    if description:
        blob_parts.append(description)
    if bible:
        blob_parts.append(bible)
    blob = " ".join(blob_parts)

    creature = _CREATURE_RE.search(blob)
    male = bool(_MALE_RE.search(blob))
    female = bool(_FEMALE_RE.search(blob))
    # Creature wins when clearly named and not also a clear gendered human
    if creature and not (male or female):
        return sanitize_class_token(creature.group(1))
    if male and not female:
        return "man"
    if female and not male:
        return "woman"
    if creature:
        return sanitize_class_token(creature.group(1))
    return "person"


def short_marks_from_subject(subject: Any = None, *, max_chars: int = 120) -> str:
    """Compact identity marks from vision-grounded subject settings (not full bible)."""
    if subject is None:
        return ""
    cfg = getattr(subject, "training_settings_json", None) or {}
    marks = (cfg.get("bible_identity_marks") or "").strip()
    if not marks and cfg.get("bible_vision_tags"):
        marks = ", ".join(str(t) for t in cfg["bible_vision_tags"][:12])
    marks = marks.strip().strip(",")
    if len(marks) > max_chars:
        marks = marks[:max_chars].rsplit(",", 1)[0].strip()
    return marks


def compose_identity_core(
    trigger: str,
    class_token: str = "person",
    marks: str = "",
) -> str:
    """Build ``a photo of {trigger}, {class}[, marks]`` — train/infer shared shape."""
    token = (trigger or "").strip().strip(",")
    cls = sanitize_class_token(class_token)
    m = (marks or "").strip().strip(",")
    if not token:
        parts = [f"a photo of a {cls}"]
        if m:
            parts.append(m)
        return ", ".join(parts)
    core = f"a photo of {token}, {cls}"
    if m:
        rest = m
        for prefix in (f"a photo of {token}", token, cls):
            if rest.lower().startswith(prefix.lower()):
                rest = rest[len(prefix):].strip().strip(",").strip()
        if rest and rest.lower() not in core.lower():
            core = f"{core}, {rest}"
    return core
