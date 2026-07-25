"""Unit tests: vision tag merge → bible; LoRA compose omits full bible."""
from __future__ import annotations

from backend.services.character_bible_from_refs import (
    _clean_tags,
    merge_identity_tags,
    short_identity_marks,
    tags_to_bible,
)
from backend.services.character_generator_service import _compose_prompt
from backend.services.swarm.agents.character_designer import ShotVariation


def test_merge_identity_tags_prefers_sunglasses_and_shaved():
    lists = [
        ["shaved head", "sunglasses", "slim build", "40s", "light skin"],
        ["bald", "sunglasses", "average build", "40s"],
        ["buzz cut", "dark sunglasses", "slim", "stubble"],
    ]
    merged = merge_identity_tags(lists)
    blob = " ".join(merged).lower()
    assert "sunglass" in blob or "sun glass" in blob
    assert any(k in blob for k in ("shaved", "bald", "buzz"))
    bible = tags_to_bible(merged, name="Dean")
    assert "Dean:" in bible
    assert "sunglass" in bible.lower() or "sun glass" in bible.lower()
    marks = short_identity_marks(merged)
    assert marks
    assert len(marks) <= 200


def test_clean_tags_drops_none_and_color_slash_noise():
    tags = _clean_tags(
        "shaved, athletic, none, clean-shaven, 30s, medium, "
        "black/dark gray, black/grey, utility belt, white LED eyes, cowl with horns"
    )
    blob = " ".join(tags)
    assert "none" not in tags
    assert "clean-shaven" not in tags
    assert "black/dark gray" not in tags
    assert "utility belt" in blob
    assert "white led eyes" in blob or "led eyes" in blob
    assert "cowl" in blob


def test_merge_costume_tags_beats_false_shaved_head():
    """Masked/cowled characters must not become 'shaved, none, 30s'."""
    lists = [
        [
            "black cowl with horns", "white LED eyes", "utility belt",
            "spiked wrist braces", "armored suit", "cape", "boots", "gloves",
            "shaved", "none", "athletic",
        ],
        [
            "cowl", "horns", "glowing white eyes", "utility belt with pouches",
            "gauntlets", "armor plating", "black cape", "rivets",
        ],
        [
            "bat cowl", "LED eyes", "belt", "boots", "gloves", "muscular",
        ],
    ]
    merged = merge_identity_tags(lists)
    blob = " ".join(merged).lower()
    assert "cowl" in blob
    assert "belt" in blob
    assert "shaved" not in blob  # head covered → drop hair false-positives
    bible = tags_to_bible(merged, name="Batman 2")
    assert "costumed figure" in bible.lower()
    assert "cowl" in bible.lower()
    assert "belt" in bible.lower()
    assert ", none," not in bible.lower()
    marks = short_identity_marks(merged)
    assert "cowl" in marks.lower() or "belt" in marks.lower()


def test_compose_prompt_lora_mode_omits_bible():
    v = ShotVariation(
        framing="medium shot",
        expression="neutral",
        lighting="soft key",
        scene="studio backdrop",
    )
    bible = (
        "Dean: long brown hair, no glasses, heavy overweight build. "
        "Keep this exact appearance."
    )
    with_bible = _compose_prompt("ohxdean", bible, v, "face-forward", include_bible=True)
    without = _compose_prompt(
        "ohxdean", bible, v, "face-forward",
        include_bible=False, class_token="man", identity_marks="shaved",
    )
    assert "long brown hair" in with_bible
    assert "heavy overweight" in with_bible
    assert "a photo of ohxdean" in without
    assert "man" in without
    assert "long brown hair" not in without
    assert "heavy overweight" not in without
    assert "studio backdrop" in without
    assert "face-forward" in without


def test_compose_prompt_full_body_lora_keeps_lead_without_bible():
    v = ShotVariation(framing="full body", expression="smile", lighting="daylight", scene="park")
    bible = "character with long flowing hair and no sunglasses"
    prompt = _compose_prompt(
        "tok", bible, v, "full-body front",
        include_bible=False, class_token="person",
    )
    assert "long flowing hair" not in prompt
    assert "a photo of tok" in prompt
    assert "park" in prompt
