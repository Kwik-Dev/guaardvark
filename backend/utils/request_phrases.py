"""Phrasing shared by the chat shortcuts that act with no model in the loop.

Whole-message shortcuts fire only when the whole message asks for them. The
same words inside a longer message ("how do I check the logs in docker?",
"python requests timeout") are left to the model. A short polite lead-in and a
trailing "now" / "today" are allowed around each form. Callers write their
forms as a regex alternation, compile it with :func:`whole_message_pattern` and
match it against :func:`normalise_message`, which lowercases the text, folds
typographic punctuation and drops the closing "?", "!" or ".".

Generation shortcuts (image, video, music video) start a job from a request
found anywhere in the message, and stand down for :data:`HOW_TO_QUESTION_RE`,
for a request turned down with :func:`declines_media` and, for video,
:data:`VIDEO_SOFTWARE_PATTERN`. :func:`asks_for_writing` marks a request for
text, which the chat model answers without its media tools, and
:func:`asks_for_rewrite` the kind of it that reworks text already given.
"""

from __future__ import annotations

import re

# "hey", "ok,", "so", "please" in front of the request.
POLITE_OPENER = r"(?:(?:hey|hi|ok|okay|so|please)[,!]?\s+)*"

# Lead-ins to a question: "can you tell me", "do you know", "i want to know".
QUESTION_LEAD_IN = (
    POLITE_OPENER
    + r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:tell\s+me|check|look\s+up|find\s+out)\s+"
    r"|(?:do|does)\s+(?:you|anyone)\s+know\s+"
    r"|(?:please\s+)?tell\s+me\s+"
    r"|i\s+(?:want|need|would\s+like)\s+to\s+know\s+)?"
)

# Lead-ins to a command: "please", "can you", "could you please".
COMMAND_LEAD_IN = POLITE_OPENER + r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"

# "now", "right now", "today", "please" after the request.
TRAILING_WHEN = r"(?:\s+(?:now|right\s+now|today|tonight|currently|at\s+the\s+moment|please))*"

# Typographic apostrophe, multiplication, division and minus signs.
_CHARACTERS = str.maketrans({"’": "'", "×": "*", "÷": "/", "−": "-"})


def normalise_message(text: str) -> str:
    """``text`` lowercased, with single spaces and without its closing punctuation."""
    folded = (text or "").translate(_CHARACTERS)
    return re.sub(r"\s+", " ", folded).strip().lower().rstrip("?!. ")


def whole_message_pattern(forms: str, lead_in: str = COMMAND_LEAD_IN) -> "re.Pattern[str]":
    """A pattern that matches a normalised message made of one of ``forms``,
    optionally after ``lead_in`` and before :data:`TRAILING_WHEN`."""
    return re.compile("^" + lead_in + "(?:" + forms + ")" + TRAILING_WHEN + "$")


# "How do I ...", "how to ...": asks how something is done, not for it to be done.
HOW_TO_QUESTION_RE = re.compile(r"^\s*how\s+(?:do|can|could|should|would|does|to)\b", re.IGNORECASE)

# Video software named in the message: making a video there is the user's own
# job ("how do I make a music video in Premiere?"). Matched against lowercase text.
VIDEO_SOFTWARE_PATTERN = (
    r"\b(?:premiere\s+pro|after\s+effects|davinci|final\s+cut|imovie|capcut|camtasia|filmora"
    r"|kdenlive|shotcut|openshot|obs\s+studio|clipchamp|ffmpeg|handbrake|movie\s+maker"
    r"|vegas\s+pro)\b"
    r"|\b(?:in|with|using)\s+(?:adobe\s+)?(?:premiere|resolve|obs|canva|blender|powerpoint)\b"
)

# Turning media down: "do not generate an image", "don't make a video yet",
# "without rendering a picture", "no image, just the text", "the words, not an
# image". The noun ends its clause, so an instruction about the picture being
# asked for ("draw a cat, but don't make the picture too dark") is not a refusal,
# and a scene's own content ("a gallery wall with no pictures") is not either.
_MEDIA_NOUN = (
    r"(?:image|picture|photo|pic|render|rendering|illustration|drawing|artwork|video|clip"
    r"|gif|animation)s?"
)
_CLAUSE_END = (
    r"(?=\s*(?:$|[.,;:!?)\]—–-]"
    r"|(?:yet|now|just|instead|until|unless|for|of|and|but|please|this\s+time|i|we)\b))"
)
_DECLINES_MEDIA_RE = re.compile(
    r"\b(?:do\s+not|don['’]?t|never|no\s+need\s+to)\s+(?:\w+\s+){0,3}?"
    r"(?:generate|create|make|render|draw|produce|paint|sketch|animate)\s+(?:me\s+|us\s+)?"
    r"(?:(?:an?|any|the|another|more|new|other)\s+)?(?:[\w-]+\s+){0,2}?" + _MEDIA_NOUN + _CLAUSE_END
    + r"|\b(?:without|stop)\s+(?:generating|creating|making|rendering|drawing|producing)\s+"
    r"(?:(?:an?|any|the|more)\s+)?" + _MEDIA_NOUN + r"\b"
    r"|(?:^|[.,;:!?]\s*)no\s+(?:images?|pictures?|photos?|renders?|videos?)(?:\s+please)?\s*(?:[.,;:!?]|$)"
    r"|\bno\s+image[\s-]gen(?:eration)?\b"
    r"|\b(?:words|text|prose|description|prompt)\s*,?\s+not\s+(?:an?\s+)?" + _MEDIA_NOUN + r"\b"
    r"|\b(?:do\s+not|don['’]?t)\s+(?:use|call|run)\s+(?:the\s+)?"
    r"(?:image(?:[\s-]gen(?:eration)?)?\s+tools?|generate_image)\b",
    re.IGNORECASE,
)


def declines_media(message: str) -> bool:
    """True when the message turns down a generated image, video or animation."""
    return bool(_DECLINES_MEDIA_RE.search(message or ""))


_REWRITE_VERB = r"(?:re-?write|revise)"

# A request for text: a sentence that opens with write, draft, describe and the
# like ("please write 300 words describing ...", "No. Write the words.", "rewrite
# this so ..."), or asks for a word count, a prompt for some model, or a
# description of something.
_WRITING_REQUEST_RE = re.compile(
    r"(?:^|[.!?]\s+)" + COMMAND_LEAD_IN
    + r"(?:write|draft|compose|describe|outline|brainstorm|come\s+up\s+with|craft|"
    + _REWRITE_VERB + r")\b"
    r"|\b\d+\s*\+?\s*(?:or\s+more\s+)?words\b"
    r"|\b(?:an?|the|your)\s+(?:[\w-]+\s+){0,2}?prompts?\s+(?:for|to\s+use|i\s+can\s+use)\b"
    r"|\b(?:an?|the)\s+(?:\w+\s+)?description\s+of\b",
    re.IGNORECASE,
)


def asks_for_writing(message: str) -> bool:
    """True when the message asks for written text rather than a rendered result."""
    return bool(_WRITING_REQUEST_RE.search(message or ""))


_REWRITE_REQUEST_RE = re.compile(r"(?:^|[.!?]\s+)" + COMMAND_LEAD_IN + _REWRITE_VERB + r"\b",
                                 re.IGNORECASE)


def asks_for_rewrite(message: str) -> bool:
    """True when the message asks for text it pastes or points at to be rewritten."""
    return bool(_REWRITE_REQUEST_RE.search(message or ""))
