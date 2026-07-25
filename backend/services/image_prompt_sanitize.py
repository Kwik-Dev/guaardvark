"""Strip chat/CLI chrome from image prompts so the generator sees pure visual text.

Users and NL routers often pass strings like::

    generate an image of the batmobile
    /imagine a sunset over mountains
    please draw a picture of batman standing on a rooftop

Those wrappers hurt still quality (especially with verbatim ON) and diverge
across chat / CLI / batch. Call ``sanitize_image_prompt`` at every stills
front door before enhance/director or the offline engine.

Idempotent: already-clean prompts pass through unchanged.
"""
from __future__ import annotations

import re
from typing import Optional


# Leading chrome: slash commands, polite verbs, "generate an image of …"
_LEADING_CHROME = re.compile(
    r"""
    ^\s*
    (?:
        /imagine\b\s*                                  # slash
      | /image\b\s*
      | (?:please\s+)?(?:can\s+you\s+)?(?:please\s+)?
        (?:
            (?:generate|create|make|draw|render|paint|produce|craft)
            \s+
            (?:me\s+)?
            (?:an?\s+)?
            (?:image|picture|photo|illustration|artwork|drawing|render)
            (?:\s+of)?
          | (?:draw|paint|sketch)\s+(?:me\s+)?(?:an?\s+)?
          | (?:i\s+want\s+(?:you\s+to\s+)?)
            (?:generate|create|make|draw)
            \s+(?:an?\s+)?
            (?:image|picture|photo)
            (?:\s+of)?
          | (?:show\s+me\s+(?:an?\s+)?)
            (?:image|picture|photo)
            (?:\s+of)?
        )
      | (?:use\s+the\s+)?generate_image\s+(?:tool\s+)?(?:with\s+prompt\s+)?
    )
    \s*
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Trailing chat fluff that is not visual content
_TRAILING_FLUFF = re.compile(
    r"""
    \s*
    (?:
        [,.]?\s*(?:please|thanks|thank\s+you|thx)\s*[.!]?\s*$
      | \s*[,.]?\s*(?:make\s+it\s+)?(?:high\s+quality|in\s+4k|hd)\s*[.!]?\s*$
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Collapse runs of whitespace
_WS = re.compile(r"\s+")

# Strip surrounding quotes the user/LLM wrapped around the whole prompt
_QUOTED = re.compile(r'''^([\'"])(.*)\1\s*$''', re.DOTALL)


def sanitize_image_prompt(prompt: Optional[str], *, max_len: int = 4000) -> str:
    """Return a pure visual prompt suitable for still generation.

    - Strips leading NL/slash chrome (``generate an image of``, ``/imagine``, …)
    - Strips light trailing politeness fluff
    - Collapses whitespace; optional outer quotes
    - Caps length (does not smart-truncate mid-word for short overruns)

    Never returns None; empty input → ``\"\"``.
    """
    if prompt is None:
        return ""
    text = str(prompt).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    # Outer quotes (once)
    m = _QUOTED.match(text)
    if m and m.group(2).strip():
        text = m.group(2).strip()

    # Apply leading chrome strip repeatedly (LLM may nest "please generate…")
    for _ in range(3):
        cleaned = _LEADING_CHROME.sub("", text, count=1).strip()
        if cleaned == text:
            break
        text = cleaned

    # Trailing fluff once
    text = _TRAILING_FLUFF.sub("", text).strip()

    # Whitespace
    text = _WS.sub(" ", text).strip(" \t,;:-")

    if max_len and len(text) > max_len:
        text = text[:max_len].rstrip()

    return text


def looks_like_image_gen_chrome(prompt: Optional[str]) -> bool:
    """True if prompt still starts with generation chrome (for tests/debug)."""
    if not prompt:
        return False
    return bool(_LEADING_CHROME.match(str(prompt).strip()))
