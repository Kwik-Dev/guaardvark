"""Unit tests: vision tag merge → bible; LoRA compose omits full bible."""
from __future__ import annotations

from backend.services.character_bible_from_refs import (
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
    without = _compose_prompt("ohxdean", bible, v, "face-forward", include_bible=False)
    assert "long brown hair" in with_bible
    assert "heavy overweight" in with_bible
    assert "ohxdean" in without
    assert "long brown hair" not in without
    assert "heavy overweight" not in without
    assert "studio backdrop" in without
    assert "face-forward" in without


def test_compose_prompt_full_body_lora_keeps_lead_without_bible():
    v = ShotVariation(framing="full body", expression="smile", lighting="daylight", scene="park")
    bible = "character with long flowing hair and no sunglasses"
    prompt = _compose_prompt("tok", bible, v, "full-body front", include_bible=False)
    assert "long flowing hair" not in prompt
    assert "tok" in prompt
    assert "park" in prompt
