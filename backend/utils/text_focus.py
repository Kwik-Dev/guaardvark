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
# Hits of one term in a window past this many add nothing: beyond it a word is
# being repeated (a menu, the page's own subject), not answered. Measured with
# the rarity weighting and the slide in focus_window on 14 questions over five
# Wikipedia pages (2026-09-27): the sentence that answers fell inside the
# 2,000-character excerpt for 10 (5 with substring matching, unweighted), and
# none of those 5 was lost.
_SATURATION = 3


def query_terms(query: str) -> List[str]:
    """The distinct words of ``query`` worth looking for: three characters or more, not a stopword."""
    words = {w.strip("'./-") for w in _WORD.findall((query or "").lower())}
    return sorted(w for w in words if len(w) >= 3 and w not in _STOPWORDS)


def focus_window(text: str, query: str, limit: int, anchors: Iterable[str] = ()) -> str:
    """The ``limit``-character stretch of ``text`` that holds the most distinct query terms and anchors.

    A page often opens with navigation, so its head can leave out the passage
    a question is about. A term matches at the start of a word ("eat" finds
    "eats" and "eaten", not "great"). Among windows with the same number of
    distinct terms, the one holding more of the page's rarer terms wins: each
    hit counts as one over its term's count on the page, at most
    ``_SATURATION`` hits per term, so the page's subject, which occurs
    everywhere, cannot outweigh the one passage that uses the question's
    other word, and a menu that repeats the question's words cannot outweigh
    the passage that uses them once. Ties go to the earliest window, which is
    then slid so its matches, weighted the same way, sit a third of the way
    in, as long as it keeps its score: the passage is not left at the far
    edge. The window ends on whitespace; one that does not start at the top
    of the text begins with "… ". The head of ``text`` (cut on whitespace) is
    returned when the text already fits, when there is nothing to look for,
    or when no term occurs.
    """
    if text is None:
        return ""
    if limit <= 0 or len(text) <= limit:
        return text
    terms = set(query_terms(query)) | {a.strip().lower() for a in anchors if a and a.strip()}
    lowered = text.lower()
    hits = sorted(
        (m.start(), term) for term in terms for m in re.finditer(r"(?<![a-z0-9])" + re.escape(term), lowered)
    )[:_MAX_HITS]
    if not hits:
        return cut_on_whitespace(text, limit)

    on_page: dict = {}
    for _, term in hits:
        on_page[term] = on_page.get(term, 0) + 1
    positions = [position for position, _ in hits]

    def inside(start: int) -> list:
        return hits[bisect.bisect_left(positions, start):bisect.bisect_left(positions, start + limit)]

    def score(window: list) -> tuple:
        per_term: dict = {}
        for _, term in window:
            per_term[term] = per_term.get(term, 0) + 1
        rarity = sum(min(n, _SATURATION) / on_page[term] for term, n in per_term.items())
        return len(per_term), round(rarity, 6)

    lead = limit // 5
    best_key, best_start = None, 0
    for position, _ in hits:
        start = max(0, position - lead)
        key = score(inside(start))
        if best_key is None or key > best_key:
            best_key, best_start = key, start

    window = inside(best_start)
    weights = [1.0 / on_page[term] for _, term in window]
    centre = sum(w * position for w, (position, _) in zip(weights, window)) / sum(weights)
    slid = min(max(0, int(centre) - limit // 3), len(text) - limit)
    if score(inside(slid)) >= best_key:
        best_start = slid

    if best_start == 0:
        return cut_on_whitespace(text, limit)
    space = text.find(" ", best_start, best_start + 40)
    start = space + 1 if space >= 0 else best_start
    return "… " + cut_on_whitespace(text[start:], limit - 2)
