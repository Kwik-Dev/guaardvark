"""Unit tests for the casting script-markup parser.

Pure-function tests — no DB, no LLM, no Flask app context required.
Covers the happy paths AND the negative cases (zero-placebo): default kind
gating, props NOT requiring casting, pins overriding kind defaults, and
malformed markup being left untouched.
"""
from backend.services.swarm.script_markup import (
    parse_markup,
    apply_intents,
    default_cast_required,
    effective_cast_required,
    normalize_name,
)


# --- kind defaults ---------------------------------------------------------

def test_only_characters_are_cast_required_by_default():
    assert default_cast_required("character") is True
    assert default_cast_required("prop") is False
    assert default_cast_required("environment") is False
    assert default_cast_required(None) is False
    assert default_cast_required("CHARACTER") is True  # case-insensitive


def test_effective_falls_back_to_kind_for_legacy_null():
    # Legacy rows predating the column store NULL → fall back to kind default.
    assert effective_cast_required(None, "character") is True
    assert effective_cast_required(None, "prop") is False
    # An explicit value always wins over the kind default.
    assert effective_cast_required(True, "prop") is True
    assert effective_cast_required(False, "character") is False


# --- parsing ---------------------------------------------------------------

def test_no_markup_returns_text_unchanged_and_no_intents():
    text = "Serenity walks into the booth and grabs the microphone."
    res = parse_markup(text)
    assert res.cleaned_text == text
    assert res.intents == {}


def test_pin_marks_cast_required_and_strips_syntax():
    res = parse_markup("She grabs the [[golden microphone]] and sings.")
    assert "[[" not in res.cleaned_text and "]]" not in res.cleaned_text
    assert res.cleaned_text == "She grabs the golden microphone and sings."
    intent = res.intents[normalize_name("golden microphone")]
    assert intent.pinned is True
    assert intent.resolved_cast_required() is True  # pinned prop still needs a LoRA


def test_pin_with_explicit_kind():
    res = parse_markup("The [[Red Guitar:prop]] is iconic.")
    assert res.cleaned_text == "The Red Guitar is iconic."
    intent = res.intents[normalize_name("Red Guitar")]
    assert intent.kind == "prop"
    assert intent.pinned is True


def test_kind_override_without_pin_follows_kind_default():
    # {{Black Lipstick:prop}} reclassifies it so it is generated inline, NOT
    # trained as a character — the exact over-extraction bug from production 5.
    res = parse_markup("She wears {{Black Lipstick:prop}}.")
    assert res.cleaned_text == "She wears Black Lipstick."
    intent = res.intents[normalize_name("black lipstick")]
    assert intent.pinned is False
    assert intent.kind == "prop"
    assert intent.resolved_cast_required() is False


def test_malformed_markup_left_untouched():
    text = "A single [bracket] and a {single brace} stay literal."
    res = parse_markup(text)
    assert res.cleaned_text == text
    assert res.intents == {}


# --- reconciliation with extracted subjects --------------------------------

def test_apply_intents_default_gating_without_markup():
    subjects = [
        {"name": "Serenity", "kind": "character", "description": "the singer"},
        {"name": "Microphone", "kind": "prop", "description": "vocal mic"},
        {"name": "Booth", "kind": "environment", "description": "recording booth"},
    ]
    out = apply_intents(subjects, {})
    by_name = {s["name"]: s for s in out}
    assert by_name["Serenity"]["cast_required"] is True
    assert by_name["Microphone"]["cast_required"] is False
    assert by_name["Booth"]["cast_required"] is False


def test_apply_intents_kind_override_reclassifies_extracted_subject():
    # Gemma mis-guessed "Black Lipstick" as a character; markup forces prop.
    subjects = [{"name": "Black Lipstick", "kind": "character", "description": "lipstick"}]
    res = parse_markup("{{Black Lipstick:prop}}")
    out = apply_intents(subjects, res.intents)
    assert out[0]["kind"] == "prop"
    assert out[0]["cast_required"] is False


def test_apply_intents_pin_promotes_prop_to_cast():
    subjects = [{"name": "Golden Microphone", "kind": "prop", "description": "mic"}]
    res = parse_markup("[[Golden Microphone]]")
    out = apply_intents(subjects, res.intents)
    assert out[0]["cast_required"] is True


def test_apply_intents_injects_unmatched_pin():
    # Operator pinned a name Gemma never surfaced → injected as a cast member.
    res = parse_markup("[[The Narrator:character]]")
    out = apply_intents([], res.intents)
    assert len(out) == 1
    assert out[0]["name"] == "The Narrator"
    assert out[0]["kind"] == "character"
    assert out[0]["cast_required"] is True


def test_apply_intents_does_not_inject_unmatched_kind_override():
    # A kind-only override for a non-extracted name has nothing to render.
    res = parse_markup("{{Ghost Prop:prop}}")
    out = apply_intents([], res.intents)
    assert out == []
