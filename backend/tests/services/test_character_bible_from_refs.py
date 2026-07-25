"""Unit tests: open vision consensus → bible; LoRA compose omits full bible."""
from __future__ import annotations

from backend.services.character_bible_from_refs import (
    _parse_consensus_json,
    consensus_identity_from_descriptions,
    marks_from_bible,
    merge_identity_tags,
    short_identity_marks,
    tags_to_bible,
)
from backend.services.character_generator_service import _compose_prompt
from backend.services.swarm.agents.character_designer import ShotVariation


def test_parse_consensus_json_batman_style():
    raw = """{
      "class_token": "costumed man",
      "marks": "black cowl with horns, white LED eyes, utility belt, cape, armored suit",
      "bible": "Batman 2: a man in a black bat cowl with pointed horns, glowing white LED eyes, armored suit, utility belt, cape, gloves and boots. Keep this exact appearance in every shot."
    }"""
    parsed = _parse_consensus_json(raw)
    assert parsed["class_token"] == "costumed man"
    assert "cowl" in parsed["marks"].lower()
    assert "LED" in parsed["bible"] or "led" in parsed["bible"].lower()


def test_consensus_identity_prefers_llm_json_over_tag_dump():
    descriptions = [
        "A figure in a black bat-like cowl with pointed horns and glowing white eyes, "
        "wearing armored plating, a utility belt, cape, gloves and boots.",
        "Same armored vigilante costume: black cowl, white eye lenses, spiked gauntlets, "
        "cape and detailed utility belt with pouches.",
        "Full-body black armored suit with cape and bat emblem; white glowing eyes in the cowl.",
    ]

    def fake_llm(*, system, user, model="gemma4:12b"):
        return (
            '{"class_token":"costumed man",'
            '"marks":"black cowl with horns, white LED eyes, utility belt, cape, armor",'
            '"bible":"A costumed man in a black bat cowl with horns, white LED eyes, '
            "armored suit, utility belt, cape, gloves and boots. "
            'Keep this exact appearance in every shot."}'
        )

    out = consensus_identity_from_descriptions(
        descriptions, name="Batman 2", llm=fake_llm,
    )
    assert "cowl" in out["bible"].lower()
    assert "shaved" not in out["bible"].lower()
    assert "none" not in out["marks"].lower()
    assert out["class_token"] == "costumed man"


def test_consensus_identity_white_wolf():
    descriptions = [
        "A large white wolf with thick fur, amber eyes, and a black nose, standing in snow.",
        "White wolf, dense winter coat, yellow-amber eyes, alert ears, bushy white tail.",
        "Arctic white wolf facing camera; pale fur, sharp muzzle, no collar or costume.",
    ]

    def fake_llm(*, system, user, model="gemma4:12b"):
        return (
            '{"class_token":"white wolf",'
            '"marks":"thick white fur, amber eyes, black nose, alert ears, bushy tail",'
            '"bible":"A white wolf with thick pale fur, amber eyes, black nose, '
            "alert ears and a bushy tail. Keep this exact appearance in every shot.\"}"
        )

    out = consensus_identity_from_descriptions(
        descriptions, name="Frost", llm=fake_llm,
    )
    assert "wolf" in out["class_token"].lower()
    assert "fur" in out["marks"].lower() or "fur" in out["bible"].lower()
    assert "man" not in out["class_token"]
    assert "shaved" not in out["bible"].lower()


def test_consensus_fallback_without_llm_json():
    def bad_llm(*, system, user, model="gemma4:12b"):
        raise RuntimeError("ollama down")

    out = consensus_identity_from_descriptions(
        ["A white wolf with thick fur and amber eyes."],
        name="Frost",
        llm=bad_llm,
    )
    assert "wolf" in out["bible"].lower()
    assert "Keep this exact appearance" in out["bible"]


def test_marks_from_bible():
    marks = marks_from_bible(
        "Frost: thick white fur, amber eyes, bushy tail. "
        "Keep this exact appearance in every shot."
    )
    assert "fur" in marks.lower()
    assert "Keep this" not in marks


def test_legacy_merge_and_tags_to_bible_still_work():
    lists = [
        ["shaved head", "sunglasses", "slim"],
        ["bald", "sunglasses"],
    ]
    merged = merge_identity_tags(lists)
    assert merged
    bible = tags_to_bible(merged, name="Dean")
    assert "Dean:" in bible
    assert short_identity_marks(merged)


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
    assert "a photo of ohxdean" in without
    assert "long brown hair" not in without


def test_compose_prompt_wolf_class_anchor():
    v = ShotVariation(framing="full body", expression="", lighting="daylight", scene="snow")
    prompt = _compose_prompt(
        "frost_tok", "ignored bible", v, "full-body front",
        include_bible=False, class_token="white wolf",
        identity_marks="thick white fur, amber eyes",
    )
    assert "a photo of frost_tok" in prompt
    assert "white wolf" in prompt
    assert "amber eyes" in prompt
    assert "ignored bible" not in prompt
