"""Human class anchor + short vision marks for cast LoRA train/infer prompts.

Without a class token (man/woman/person), Z-Image treats a name-like trigger
(e.g. sniffy_mcgee) + scene as free rein → cartoon animals / mascots.
"""
from __future__ import annotations

import re
from typing import Any, Optional

_CLASS_OK = frozenset({"man", "woman", "person", "boy", "girl"})

_MALE_RE = re.compile(
    r"\b(man|male|gentleman|guy|he\b|his\b|beard|stubble|mustache|moustache)\b",
    re.I,
)
_FEMALE_RE = re.compile(
    r"\b(woman|female|lady|she\b|her\b|girl)\b",
    re.I,
)


def resolve_class_token(
    subject: Any = None,
    *,
    class_token: str | None = None,
    tags: list[str] | None = None,
    description: str | None = None,
    bible: str | None = None,
) -> str:
    """Return man / woman / person for prompt class anchoring."""
    explicit = (class_token or "").strip().lower()
    if not explicit and subject is not None:
        cfg = getattr(subject, "training_settings_json", None) or {}
        explicit = (cfg.get("class_token") or "").strip().lower()
    if explicit in _CLASS_OK:
        return explicit

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

    male = bool(_MALE_RE.search(blob))
    female = bool(_FEMALE_RE.search(blob))
    if male and not female:
        return "man"
    if female and not male:
        return "woman"
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
    cls = (class_token or "person").strip().lower()
    if cls not in _CLASS_OK:
        cls = "person"
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
