"""split_inline_reasoning and InlineReasoningStream, on the content forms Ollama
0.33.3 returned with think:false (2026-09-26)."""
import pytest

from backend.utils.inline_reasoning import (
    REASONING, RETRACT, VISIBLE, InlineReasoningStream, split_inline_reasoning,
)

R = "3:40 plus 95 minutes is 5:15."
A = "The train arrives at 5:15."


@pytest.mark.parametrize("text, expected", [
    (A, ("", A)),
    (f"<think>\n{R}\n</think>\n{A}", (R, A)),          # lfm2.5
    (f"{R}\n</think>\n{A}", (R, A)),                   # granite4.2: no opening tag
    (f"<THINK>{R}</Think>{A}", (R, A)),                # tag case
    (f"<think>{R}", (R, "")),                          # never closed: all reasoning
    (f"<think>{R}</think>{A} <think>more</think> end", (R + "more", A + " end")),
    (f"<think>{R}</think>{A} and </think> stays", (R, f"{A} and </think> stays")),
    ("a < b and x <th y", ("", "a < b and x <th y")),  # tag-like text that is not a tag
    ("", ("", "")),
])
def test_split(text, expected):
    assert split_inline_reasoning(text) == expected


def _events(text, size):
    stream = InlineReasoningStream()
    out = []
    for i in range(0, len(text), size):
        out += stream.feed(text[i:i + size])
    return out + stream.finish()


@pytest.mark.parametrize("size", [1, 2, 3, 7, 1000])
@pytest.mark.parametrize("text", [
    A, f"<think>\n{R}\n</think>\n{A}", f"{R}\n</think>\n{A}", f"<think>{R}",
])
def test_stream_matches_split_at_any_chunk_size(text, size):
    reasoning, answer = [], []
    for kind, piece in _events(text, size):
        if kind == VISIBLE:
            answer.append(piece)
        elif kind == REASONING:
            reasoning.append(piece)
        else:
            answer.clear()
            reasoning.append(piece)
    assert ("".join(reasoning).strip(), "".join(answer).strip()) == split_inline_reasoning(text)


def test_stream_never_shows_a_tag():
    for kind, piece in _events(f"<think>{R}</think>{A}", 1):
        if kind == VISIBLE:
            assert "<" not in piece and ">" not in piece


def test_lone_closing_tag_retracts_what_was_shown():
    events = _events(f"{R}</think>{A}", 4)
    kinds = [k for k, _ in events]
    assert RETRACT in kinds
    retracted = next(p for k, p in events if k == RETRACT)
    assert R.startswith(retracted) and retracted


# ── the tag pairs are data ───────────────────────────────────────────────────

from backend.services.model_capability_data import INLINE_REASONING_TAGS  # noqa: E402


@pytest.mark.parametrize("open_tag, close_tag", INLINE_REASONING_TAGS)
def test_every_default_pair(open_tag, close_tag):
    assert split_inline_reasoning(f"{open_tag}{R}{close_tag}{A}") == (R, A)
    assert split_inline_reasoning(f"{R}{close_tag}{A}") == (R, A)


@pytest.mark.parametrize("size", [1, 3, 1000])
def test_longest_default_pair_streams(size):
    text = f"<|begin_of_thought|>{R}<|end_of_thought|>{A}"
    reasoning, answer = [], []
    for kind, piece in _events_with(text, size, None):
        (answer if kind == VISIBLE else reasoning).append(piece)
        assert "<|" not in piece or kind != VISIBLE
    assert ("".join(reasoning).strip(), "".join(answer).strip()) == (R, A)


def test_a_model_row_replaces_the_default_pairs():
    granite32 = [("Here is my thought process:", "Here is my response:")]
    text = f"Here is my thought process: {R} Here is my response: {A}"
    assert split_inline_reasoning(text, granite32) == (R, A)
    # The defaults no longer apply to that model.
    assert split_inline_reasoning(f"<think>{R}</think>{A}", granite32) == ("", f"<think>{R}</think>{A}")


def test_near_miss_tags_stay_in_the_answer():
    text = f"{A} Use <thinkpad> or <reasons> freely."
    assert split_inline_reasoning(text) == ("", text)


def _events_with(text, size, tags):
    stream = InlineReasoningStream(tags)
    out = []
    for i in range(0, len(text), size):
        out += stream.feed(text[i:i + size])
    return out + stream.finish()


# ── leading_only: models not known to think ──────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    (f"\n<think>{R}</think>\n{A}", (R, A)),                       # opens the answer
    (f"<thinking>{R}</thinking>{A}", (R, A)),
    (f"{A} The <think> tag marks reasoning.", ("", f"{A} The <think> tag marks reasoning.")),
    (f"{R} </think> {A}", ("", f"{R} </think> {A}")),              # lone close ignored
    (f"<think>{R}</think>{A} <think>x</think>", (R, f"{A} <think>x</think>")),  # one block only
    (f"<think>{R}", (R, "")),
    (A, ("", A)),
])
def test_leading_only(text, expected):
    assert split_inline_reasoning(text, leading_only=True) == expected


@pytest.mark.parametrize("size", [1, 2, 5, 1000])
@pytest.mark.parametrize("text", [
    f"\n<think>{R}</think>\n{A}", f"{A} and <think>", f"<thi{A}", f"{R} </think> {A}",
])
def test_leading_only_stream_matches_split(text, size):
    stream = InlineReasoningStream(leading_only=True)
    reasoning, answer = [], []
    events = []
    for i in range(0, len(text), size):
        events += stream.feed(text[i:i + size])
    for kind, piece in events + stream.finish():
        assert kind != RETRACT
        (answer if kind == VISIBLE else reasoning).append(piece)
    assert ("".join(reasoning).strip(), "".join(answer).strip()) == \
        split_inline_reasoning(text, leading_only=True)


# ── a quoted tag is a mention, not a marker ──────────────────────────────────

QUOTED = [
    ("<think>is it the `</think>` tag? yes</think>" + A, ("is it the `</think>` tag? yes", A)),
    (f"{A} The `<think>` tag marks reasoning.", ("", f"{A} The `<think>` tag marks reasoning.")),
    (f'{A} Write "</think>" to end it.', ("", f'{A} Write "</think>" to end it.')),
    (f"<think>{R}</think>`code` {A}", (R, f"`code` {A}")),
    (f"{A} The \u201c<think>\u201d tag.", ("", f"{A} The \u201c<think>\u201d tag.")),
    (f"x`y</think>{A}", ("x`y", A)),  # a quote on one side only is not a mention
]


@pytest.mark.parametrize("text, expected", QUOTED)
def test_quoted_tags(text, expected):
    assert split_inline_reasoning(text) == expected


@pytest.mark.parametrize("size", [1, 2, 3, 1000])
@pytest.mark.parametrize("text", [t for t, _ in QUOTED])
def test_quoted_tags_stream_like_split(text, size):
    stream = InlineReasoningStream()
    reasoning, answer = [], []
    events = []
    for i in range(0, len(text), size):
        events += stream.feed(text[i:i + size])
    for kind, piece in events + stream.finish():
        if kind == RETRACT:
            answer.clear()
            reasoning.append(piece)
        else:
            (answer if kind == VISIBLE else reasoning).append(piece)
    assert ("".join(reasoning).strip(), "".join(answer).strip()) == split_inline_reasoning(text)
