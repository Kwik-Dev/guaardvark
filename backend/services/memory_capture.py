"""Deterministic capture of facts the user states in chat.

Explicit intents only — no LLM. A match writes an ``AgentMemory`` fact via
``add_memory``; questions and short utterances are skipped, and a fact already
saved where it would be recalled is not stored twice (``add_memory`` returns
the saved row instead).

A fact the person asks to be kept ("remember that …", "remember: …", "from now
on …", "for future reference …") is stored without the chat's session id, so
every later chat recalls it; in a project chat it keeps the project id and
stays in that project. "note that …" and "my X is Y" stay with the chat they
were said in.
"""

from __future__ import annotations

import re

from backend.api.memory_api import add_memory, _app_context

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

# Where a task starts after the fact on the brain paths: "remember that X. First
# do Y" or "remember that X, then do Y". A conditional ("if X, then Y") is one fact.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=\S)")
_THEN_CLAUSE = re.compile(r"\s*[,;]\s*(?:and\s+)?then\s+", re.IGNORECASE)
_CONDITIONAL = re.compile(r"^\s*(?:if|when|whenever|once)\s", re.IGNORECASE)

_WORD_RE = re.compile(r"\S+")


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _extract_fact(message: str, *, explicit_only: bool = False) -> str | None:
    """Return the fact to store, or None if the message is not a remember.

    explicit_only keeps just the forms that ask for the fact to be kept
    ("remember that …", "from now on …"); "note that …" and "my X is Y"
    are then left alone. Those turns are task requests, so the fact also
    ends where the task begins: at the first sentence end, or at ", then".
    """
    text = (message or "").strip()
    if not text or text.endswith("?"):
        return None
    if _word_count(text) < _MIN_WORDS:
        return None

    for pattern in _REMEMBER_PATTERNS if explicit_only else _PREFIX_PATTERNS:
        match = pattern.match(text)
        if match:
            fact = text[match.end():].strip()
            if explicit_only:
                fact = _SENTENCE_END.split(fact, 1)[0]
                if not _CONDITIONAL.match(fact):
                    fact = _THEN_CLAUSE.split(fact, 1)[0]
                fact = fact.strip()
            if len(fact) >= 2 and fact[0] == fact[-1] and fact[0] in "\"'":
                fact = fact[1:-1].strip()
            return fact or None

    if not explicit_only and _MY_OUR_IS.match(text):
        return text

    return None


def will_capture(message: str, *, explicit_only: bool = False) -> bool:
    """True when capture_from_message would store a fact from this message."""
    return isinstance(message, str) and _extract_fact(message, explicit_only=explicit_only) is not None


def _is_remember(message: str) -> bool:
    return any(pattern.match(message or "") for pattern in _REMEMBER_PATTERNS)


def capture_from_message(
    message: str,
    *,
    session_id=None,
    project_id=None,
    user_id=None,
    explicit_only: bool = False,
) -> str | None:
    """Store an explicit 'remember …' fact from a chat turn.

    A remember intent is stored without session_id so later chats recall it;
    project_id is kept either way. explicit_only stores only the forms that
    ask for the fact to be kept, so a path whose traffic is task requests
    ("my goal is to …") does not file those as facts. Returns the new memory
    id, the id of the row that already holds the fact where this one would be
    recalled, or None if the message is not a remember intent. A copy kept
    only for another chat or project does not count: this chat would not
    recall it.
    """
    if not isinstance(message, str):
        return None
    fact = _extract_fact(message, explicit_only=explicit_only)
    if not fact:
        return None
    across_chats = _is_remember(message)

    with _app_context():
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
