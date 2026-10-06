"""Deterministic capture of facts the user states in chat.

Explicit intents only — no LLM. A match writes an ``AgentMemory`` fact via
``add_memory``; questions, short utterances, and duplicates are skipped.

A fact the person asks to be kept ("remember that …", "remember: …", "from now
on …", "for future reference …") is stored without the chat's session id, so
every later chat recalls it; in a project chat it keeps the project id and
stays in that project. "note that …" and "my X is Y" stay with the chat they
were said in.
"""

from __future__ import annotations

import re

from sqlalchemy import or_

from backend.api.memory_api import add_memory, _app_context
from backend.models import AgentMemory

_MIN_WORDS = 4

# Prefix intents are stripped; the remainder is the stored fact.
# These are recalled in every later chat, not only this one.
_REMEMBER_PATTERNS = (
    re.compile(r"^\s*remember\s+that\s+", re.IGNORECASE),
    re.compile(r"^\s*remember:\s*", re.IGNORECASE),
    re.compile(r"^\s*from\s+now\s+on[,:]?\s+", re.IGNORECASE),
    re.compile(r"^\s*for\s+future\s+reference[,:]?\s+", re.IGNORECASE),
)
_PREFIX_PATTERNS = _REMEMBER_PATTERNS + (
    re.compile(r"^\s*note\s+that\s+", re.IGNORECASE),
)

# Whole-message fact: "my/our <short noun phrase> is <value>".
# Noun phrase is 1–4 simple tokens so this stays a statement of identity.
_MY_OUR_IS = re.compile(
    r"^\s*(?:my|our)\s+"
    r"[A-Za-z][A-Za-z0-9_-]*(?:\s+[A-Za-z][A-Za-z0-9_-]*){0,3}"
    r"\s+is\s+\S",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"\S+")


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _normalize_content(text: str) -> str:
    collapsed = " ".join((text or "").strip().lower().split())
    return collapsed.rstrip(".,;:!")


def _extract_fact(message: str) -> str | None:
    """Return the fact to store, or None if the message is not an explicit remember."""
    text = (message or "").strip()
    if not text or text.endswith("?"):
        return None
    if _word_count(text) < _MIN_WORDS:
        return None

    for pattern in _PREFIX_PATTERNS:
        match = pattern.match(text)
        if match:
            fact = text[match.end():].strip()
            if len(fact) >= 2 and fact[0] == fact[-1] and fact[0] in "\"'":
                fact = fact[1:-1].strip()
            return fact or None

    if _MY_OUR_IS.match(text):
        return text

    return None


def _is_remember(message: str) -> bool:
    return any(pattern.match(message or "") for pattern in _REMEMBER_PATTERNS)


def _existing_id(content: str, *, across_chats: bool = False, project_id=None) -> str | None:
    """Id of an active row with the same normalised text, or None.

    across_chats counts only rows every later chat in project_id recalls:
    no session id, and no project or that project. A copy scoped to one
    other chat does not stop a remembered fact from being stored for all.
    """
    norm = _normalize_content(content)
    if not norm:
        return None
    query = AgentMemory.query.filter(AgentMemory.status == "active")
    if across_chats:
        query = query.filter(AgentMemory.session_id == None)
        if project_id is None:
            query = query.filter(AgentMemory.project_id == None)
        else:
            query = query.filter(
                or_(AgentMemory.project_id == project_id, AgentMemory.project_id == None)
            )
    rows = query.all()
    for row in rows:
        if _normalize_content(row.content or "") == norm:
            return row.id
    return None


def capture_from_message(
    message: str,
    *,
    session_id=None,
    project_id=None,
    user_id=None,
) -> str | None:
    """Store an explicit 'remember …' fact from a chat turn.

    A remember intent is stored without session_id so later chats recall it;
    project_id is kept either way. Returns the new (or existing duplicate)
    memory id, or None if the message is not a remember intent.
    """
    if not isinstance(message, str):
        return None
    fact = _extract_fact(message)
    if not fact:
        return None
    across_chats = _is_remember(message)

    with _app_context():
        existing = _existing_id(fact, across_chats=across_chats, project_id=project_id)
        if existing:
            return existing
        memory = add_memory(
            content=fact,
            memory_type="fact",
            source="chat",
            importance=0.7,
            session_id=None if across_chats else session_id,
            project_id=project_id,
            user_id=user_id,
        )
        return memory.id if memory is not None else None
