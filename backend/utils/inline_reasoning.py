"""Reasoning a model writes into its answer text, told apart from the answer.

Ollama moves reasoning into ``message.thinking`` when it can. Some models write
it into ``message.content`` instead, and do so most often when asked not to
think (``think: false``). The forms seen on a live Ollama 0.33.3, all asked the
same one-line question with ``think: false`` (2026-09-26):

* ``lfm2.5:8b``: ``<think>`` reasoning ``</think>`` answer.
* ``granite4.2:8b``: reasoning ``</think>`` answer, with no opening tag.
* ``magistral:24b``: untagged reasoning and no answer. Nothing in the text
  marks it, so no splitter can separate it; that model needs thinking left on.

Which tag pairs count is data: ``model_capability_data.INLINE_REASONING_TAGS``,
or a model's own ``reasoning_tags`` row (see ``model_capabilities``).

:class:`InlineReasoningStream` sorts a content stream token by token;
:func:`split_inline_reasoning` does the same for a finished text. Both treat
text before a closing tag that has no opening tag as reasoning, and an opening
tag that is never closed as reasoning to the end. Tags match case-insensitively.

``leading_only`` is for models not known to think: only a block that opens the
answer (nothing but whitespace before it) counts, and a lone closing tag does
not, so an answer that merely mentions a tag is left alone.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

TagPairs = Sequence[Tuple[str, str]]

# Events returned by InlineReasoningStream.feed / finish.
VISIBLE = "visible"      # answer text, safe to show
REASONING = "reasoning"  # reasoning text, for the reasoning channel
RETRACT = "retract"      # the answer text already shown was reasoning; the
                         # payload is that text, now to be treated as reasoning


def default_tags() -> TagPairs:
    from backend.services.model_capability_data import INLINE_REASONING_TAGS
    return INLINE_REASONING_TAGS


def _normalise(tags: Optional[TagPairs]) -> List[Tuple[str, str]]:
    pairs = [(str(o).lower(), str(c).lower()) for o, c in (tags or default_tags()) if o and c]
    return pairs or [("<think>", "</think>")]


# A tag with one of these right before and after it is being written about
# (`<think>`, "</think>", “<think>”), not used as a marker.
_QUOTES = "`'\"\u2018\u2019\u201c\u201d"


def _find_tag(lower: str, needle: str) -> int:
    """First position of ``needle`` used as a tag, skipping quoted mentions."""
    pos = lower.find(needle)
    while pos >= 0:
        after = pos + len(needle)
        quoted_before = pos > 0 and lower[pos - 1] in _QUOTES
        if not (quoted_before and after < len(lower) and lower[after] in _QUOTES):
            if quoted_before and after == len(lower):
                return -1  # the closing quote may still be on its way
            return pos
        pos = lower.find(needle, after)
    return -1


def _earliest(lower: str, needles: Sequence[str]) -> Tuple[int, int]:
    """(position, index into needles) of the first needle found, or (-1, -1)."""
    best = (-1, -1)
    for i, needle in enumerate(needles):
        pos = _find_tag(lower, needle)
        if pos >= 0 and (best[0] < 0 or pos < best[0]):
            best = (pos, i)
    return best


class InlineReasoningStream:
    """Sort streamed answer text into visible text and reasoning.

    ``feed`` takes each content token and returns ``(kind, text)`` events.
    Only a tail that could be the start of a tag is held back, so visible text
    streams without delay. ``finish`` releases whatever is still held.
    """

    def __init__(self, tags: Optional[TagPairs] = None, *, leading_only: bool = False) -> None:
        self._pairs = _normalise(tags)
        self._opens = [o for o, _ in self._pairs]
        self._closes = [c for _, c in self._pairs]
        self._longest = max(len(t) for t in self._opens + self._closes)
        self._buf = ""
        self._close: Optional[str] = None  # the closing tag awaited while inside
        self._seen_tag = False
        self._shown = ""  # visible text released so far, for RETRACT
        self._leading_only = leading_only
        self._passthrough = False  # leading_only, and the answer has started

    def _partial_tag_len(self, text: str) -> int:
        """Length of the longest tail of ``text`` that could still grow into a
        tag, or that is a quoted tag still waiting for its closing quote."""
        lower = text.lower()
        for tag in self._opens + self._closes:
            if lower.endswith(tag) and len(lower) > len(tag) and lower[-len(tag) - 1] in _QUOTES:
                return len(tag) + 1
        n = next(
            (n for n in range(min(len(lower), self._longest - 1), 0, -1)
             if any(t.startswith(lower[-n:]) for t in self._opens + self._closes)),
            0,
        )
        # Keep a quote just before (or at the end) with it, so a quoted
        # mention is still recognised as one when the tag arrives.
        if len(lower) > n and lower[-n - 1] in _QUOTES:
            n += 1
        return n

    def _release(self, events, kind: str) -> None:
        hold = self._partial_tag_len(self._buf)
        self._emit(events, kind, self._buf[: len(self._buf) - hold])
        self._buf = self._buf[len(self._buf) - hold:]

    def feed(self, token: str) -> List[Tuple[str, str]]:
        self._buf += token or ""
        events: List[Tuple[str, str]] = []
        while True:
            if self._passthrough:
                self._emit(events, VISIBLE, self._buf)
                self._buf = ""
                return events
            lower = self._buf.lower()
            if self._close is not None:
                end = _find_tag(lower, self._close)
                if end < 0:
                    self._release(events, REASONING)
                    return events
                self._emit(events, REASONING, self._buf[:end])
                self._buf = self._buf[end + len(self._close):].lstrip()
                self._close = None
                continue

            if self._leading_only:
                if self._seen_tag:
                    self._passthrough = True  # one leading block, then the answer
                    continue
                body = self._buf.lstrip()
                low = body.lower()
                opened = next((i for i, t in enumerate(self._opens) if low.startswith(t)), None)
                if opened is not None:
                    self._seen_tag = True
                    self._buf = body[len(self._opens[opened]):]
                    self._close = self._closes[opened]
                    continue
                if not body or any(t.startswith(low) for t in self._opens):
                    return events  # whitespace, or a tag still arriving
                self._passthrough = True
                continue

            start, which = _earliest(lower, self._opens)
            end, close_which = _earliest(lower, self._closes) if not self._seen_tag else (-1, -1)
            if end >= 0 and (start < 0 or end < start):
                # A closing tag with no opening one: everything before it,
                # including what was already shown, was reasoning.
                self._seen_tag = True
                if self._shown:
                    events.append((RETRACT, self._shown))
                    self._shown = ""
                self._emit(events, REASONING, self._buf[:end])
                self._buf = self._buf[end + len(self._closes[close_which]):].lstrip()
                continue
            if start >= 0:
                self._seen_tag = True
                self._emit(events, VISIBLE, self._buf[:start])
                self._buf = self._buf[start + len(self._opens[which]):]
                self._close = self._closes[which]
                continue

            self._release(events, VISIBLE)
            return events

    def finish(self) -> List[Tuple[str, str]]:
        events: List[Tuple[str, str]] = []
        self._emit(events, REASONING if self._close is not None else VISIBLE, self._buf)
        self._buf = ""
        return events

    def _emit(self, events: List[Tuple[str, str]], kind: str, text: str) -> None:
        if not text:
            return
        if kind == VISIBLE:
            self._shown += text
        events.append((kind, text))


def split_inline_reasoning(
    text: str, tags: Optional[TagPairs] = None, *, leading_only: bool = False,
) -> Tuple[str, str]:
    """``(reasoning, answer)`` from a finished text, both stripped."""
    stream = InlineReasoningStream(tags, leading_only=leading_only)
    reasoning: List[str] = []
    answer: List[str] = []
    for kind, piece in stream.feed(text or "") + stream.finish():
        if kind == VISIBLE:
            answer.append(piece)
        elif kind == REASONING:
            reasoning.append(piece)
        else:  # RETRACT
            answer.clear()
            reasoning.append(piece)
    return "".join(reasoning).strip(), "".join(answer).strip()
