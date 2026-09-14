"""The stretch of a long text that is about a query, cut to a character budget."""

from __future__ import annotations

import bisect
import re
from typing import Iterable, List

from backend.utils.text_cut import cut_on_whitespace

_WORD = re.compile(r"[a-z0-9][a-z0-9'./-]*")
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its me my of on or our so than that "
    "the their them there these they this to was we what when where which who why will with you your".split()
)
# Bounds the scan on a very long page where a common term occurs thousands of times.
_MAX_HITS = 5000


def query_terms(query: str) -> List[str]:
    """The distinct words of ``query`` worth looking for: three characters or more, not a stopword."""
    words = {w.strip("'./-") for w in _WORD.findall((query or "").lower())}
    return sorted(w for w in words if len(w) >= 3 and w not in _STOPWORDS)


def focus_window(text: str, query: str, limit: int, anchors: Iterable[str] = ()) -> str:
    """The ``limit``-character stretch of ``text`` that holds the most distinct query terms and anchors.

    A page often opens with navigation, so its head can leave out the passage
    a question is about. Among windows with the same number of distinct terms,
    the one whose matches sit closest together wins (a menu repeats words that
    a passage uses once), then the earliest. The window starts a little before
    its first matching term and ends on whitespace; a window that does not
    start at the top of the text begins with "… ". The head of ``text`` (cut
    on whitespace) is returned when the text already fits, when there is
    nothing to look for, or when no term occurs.
    """
    if text is None:
        return ""
    if limit <= 0 or len(text) <= limit:
        return text
    terms = set(query_terms(query)) | {a.strip().lower() for a in anchors if a and a.strip()}
    lowered = text.lower()
    hits = sorted((m.start(), term) for term in terms for m in re.finditer(re.escape(term), lowered))[:_MAX_HITS]
    if not hits:
        return cut_on_whitespace(text, limit)

    positions = [position for position, _ in hits]
    lead = limit // 5
    best_key, best_start = None, 0
    for position, _ in hits:
        start = max(0, position - lead)
        low = bisect.bisect_left(positions, start)
        high = bisect.bisect_left(positions, start + limit)
        inside = hits[low:high]
        key = (len({term for _, term in inside}), -(inside[-1][0] - inside[0][0]), -start)
        if best_key is None or key > best_key:
            best_key, best_start = key, start
    if best_start == 0:
        return cut_on_whitespace(text, limit)
    space = text.find(" ", best_start, best_start + 40)
    start = space + 1 if space >= 0 else best_start
    return "… " + cut_on_whitespace(text[start:], limit - 2)
