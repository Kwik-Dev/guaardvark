"""Written-to-spoken text layer for training narration.

Procedure guides are authored the way a specification is written — fractions,
part callouts, code citations. This module turns that into speakable text.

What lives here is the machinery that is true of any trade: numbers, fractions,
measurements, pitches, regulatory citations. What does NOT live here is any
particular trade's vocabulary; a project supplies that through
``Project.terms`` and ``Project.spelled_acronyms``.

The narrator backend is Chatterbox, which has no inline phoneme syntax. The
only control surfaces are respelling and letter-spacing, so every deviation
from the written form is a table entry rather than script-level workaround.
On-screen card text always keeps the written spelling.

Verify every table addition with ``produce.py voicetest``, which whisper-
transcribes each synthesized line and reports what was actually heard.
"""

from __future__ import annotations

import re

# Abbreviations any technical document uses. Trade-specific terms belong in the
# project's own table.
COMMON_TERMS: dict[str, str] = {
    "e.g.": "for example",
    "i.e.": "that is",
    "approx.": "approximately",
    "min.": "minimum",
    "max.": "maximum",
    "vs.": "versus",
    "etc.": "and so on",
}

# Written fraction -> spoken form. Conversational register: "a half inch",
# not "one half inch".
_FRACTIONS: dict[str, str] = {
    "1/2": "a half",
    "1/3": "a third",
    "2/3": "two thirds",
    "1/4": "a quarter",
    "3/4": "three quarters",
    "1/8": "an eighth",
    "3/8": "three eighths",
    "5/8": "five eighths",
    "7/8": "seven eighths",
    "1/16": "a sixteenth",
    "3/16": "three sixteenths",
    "7/16": "seven sixteenths",
    "9/16": "nine sixteenths",
    "11/16": "eleven sixteenths",
    "13/16": "thirteen sixteenths",
    "15/16": "fifteen sixteenths",
    "15/32": "fifteen thirty-seconds",
    "1/12": "a twelfth",
}

# Bare-fraction form used after a whole number ("1-1/2" -> "one and a half").
_FRACTIONS_MIXED: dict[str, str] = {
    "1/2": "a half",
    "1/4": "a quarter",
    "3/4": "three quarters",
    "1/3": "a third",
    "2/3": "two thirds",
    "1/8": "an eighth",
    "3/8": "three eighths",
    "5/8": "five eighths",
    "7/8": "seven eighths",
    "7/16": "seven sixteenths",
    "9/16": "nine sixteenths",
    "11/16": "eleven sixteenths",
    "15/16": "fifteen sixteenths",
    "15/32": "fifteen thirty-seconds",
}

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven",
         "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
         "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]

# Generic words that already identify what a ratio measures. A project adds
# its own trade's terms through Project.ratio_context.
_RATIO_CONTEXT = {"ratio", "grade", "gradient", "scale"}


def _int_words(n: int) -> str:
    """Spell an integer under 100. Larger values are left as digits."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    return str(n)


def _digit_string(digits: str) -> str:
    """Read a code number digit by digit, with 'oh' for zero."""
    return " ".join("oh" if d == "0" else _ONES[int(d)] for d in digits)


def _code_citation(match: re.Match) -> str:
    """Render 'R905.10.4' / '1926.501' as a spoken code reference."""
    letter = match.group("letter") or ""
    parts = match.group("num").split(".")
    head = _digit_string(parts[0])
    spoken_code = f"{letter} {head}" if letter else head
    for part in parts[1:]:
        spoken_code += (
            f" point {_int_words(int(part)) if len(part) <= 2 else _digit_string(part)}")
    return spoken_code.strip()


def _make_ratio(noun: str | None, context: frozenset[str]):
    """Build the ratio renderer for one project's vocabulary.

    A ratio like '4:12' is spoken 'four in twelve'. Bare, that is heard as
    'four AND twelve'; a trailing noun is what makes it unambiguous, so one is
    supplied when the surrounding text lacks it. Which noun — and which words
    already imply it — is the trade's business, not the engine's. A project
    that sets no noun gets the digits read plainly.
    """
    def _ratio(match: re.Match) -> str:
        rise = _int_words(int(match.group(1)))
        run_ = _int_words(int(match.group(2)))
        spoken_ratio = f"{rise} in {run_}"
        if not noun:
            return spoken_ratio
        before = match.string[:match.start()].lower().split()[-3:]
        after = match.string[match.end():].lower().split()[:1]
        if set(before + after) & context:
            return spoken_ratio
        return f"{spoken_ratio} {noun}"
    return _ratio


def _mixed_number(match: re.Match) -> str:
    whole, frac = match.group(1), match.group(2)
    words = _FRACTIONS_MIXED.get(frac)
    if words is None:
        return match.group(0)
    return f"{_int_words(int(whole))} and {words}"


def _bare_fraction(match: re.Match) -> str:
    return _FRACTIONS.get(match.group(0), match.group(0))


def _decimal_measure(match: re.Match) -> str:
    """'0.030-inch' -> 'point oh three oh inch'; leading zero is dropped."""
    whole, frac = match.group(1), match.group(2)
    if frac == "5" and whole != "0":
        return f"{_int_words(int(whole))} and a half"
    lead = "" if whole == "0" else f"{_int_words(int(whole))} "
    return f"{lead}point {_digit_string(frac)}"


def spoken(text: str, terms: dict[str, str] | None = None,
           spelled_acronyms: tuple[str, ...] = (),
           backend: str = "chatterbox",
           ratio_noun: str | None = None,
           ratio_context: tuple[str, ...] = ()) -> str:
    """Render written guide text as speakable text for `backend`.

    `terms` and `spelled_acronyms` come from the active project and are applied
    on top of the common table.

    Letter-spacing an acronym helps Chatterbox and Piper, which otherwise read
    it as a word. It actively harms Kokoro: its frontend reads all-caps
    acronyms correctly on its own, and the inserted spaces destroy the
    sentence's pause structure.
    """
    # Codes before any other numeric rule — they contain periods and digits
    # that the fraction and decimal rules would otherwise claim.
    text = re.sub(r"\b(?P<letter>[A-Z])?(?P<num>\d{3,4}(?:\.\d+){1,3})\b",
                  _code_citation, text)
    text = re.sub(r"\b(\d{1,2}):(\d{1,2})\b",
                  _make_ratio(ratio_noun,
                              _RATIO_CONTEXT | set(ratio_context)), text)
    text = re.sub(r"\b(\d{1,2})-(\d{1,2}/\d{1,2})\b", _mixed_number, text)
    text = re.sub(r"\b(\d+)\.(\d+)(?=\s*-?\s*inch)", _decimal_measure, text)
    text = re.sub(r"\b\d{1,2}/\d{1,2}\b", _bare_fraction, text)

    # Part callouts: '#10 x 1.5"' -> 'number ten by inch and a half'
    text = text.replace("#", "number ")
    text = re.sub(r'(\d)\s*[x×]\s*(\d)', r"\1 by \2", text)
    text = re.sub(r'(\d)\s*"', r"\1 inch", text)
    text = re.sub(r"(\d)\s*'", r"\1 foot", text)
    text = text.replace("°", " degrees")
    text = text.replace("±", "plus or minus ")

    # A spelled-out quantity leaves the source's hyphen stranded before its
    # unit ("fifteen thirty-seconds-inch").
    text = re.sub(r"(?<=[a-z])-(?=inch|foot|feet|pound|gauge|thick)", " ", text)

    # Fractions read conversationally ("a half"), which collides with a
    # determiner already in the sentence. Which article survives depends on
    # which determiner it was: an article defers to the fraction's own, so
    # "a 1/8 inch" reads "an eighth inch" and not "a eighth inch"; any other
    # determiner keeps itself and drops the fraction's.
    _frac_head = r"(?:half|quarter|third|eighth|sixteenth|twelfth)"
    text = re.sub(rf"\ban?\s+(?=an?\s+{_frac_head})", "", text,
                  flags=re.IGNORECASE)
    text = re.sub(
        rf"\b(the|this|that|those|these|its|their|each|every|one)\s+an?\s+"
        rf"(?={_frac_head})",
        r"\1 ", text, flags=re.IGNORECASE)

    # Keys carry '.' and '-', so \b cannot delimit them: an explicit
    # non-word-or-hyphen boundary keeps 'high-temp' out of 'high-temperature'.
    table = {**COMMON_TERMS, **(terms or {})}
    for written, said in table.items():
        text = re.sub(rf"(?<![\w-]){re.escape(written)}(?![\w-])", said, text)

    if backend != "kokoro":
        for acronym in spelled_acronyms:
            text = re.sub(rf"\b{acronym}\b", " ".join(acronym), text)

    return re.sub(r"[ \t]{2,}", " ", text).strip()
