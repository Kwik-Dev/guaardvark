"""Shortcuts that act on a chat message with no model in the loop.

A shortcut fires only when the whole message asks for it. The same words inside
a longer message ("how do I check the logs in docker?", "python requests
timeout") are left to the model. A short polite lead-in and a trailing "now" /
"today" are allowed around each form.

Callers write their forms as a regex alternation, compile it with
:func:`whole_message_pattern` and match it against :func:`normalise_message`,
which lowercases the text, folds typographic punctuation and drops the closing
"?", "!" or ".".
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
