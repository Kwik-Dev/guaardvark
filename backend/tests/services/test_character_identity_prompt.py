"""Unit tests: human class anchor + short marks for cast LoRA prompts/captions."""
from __future__ import annotations

from types import SimpleNamespace

from backend.services.character_captioner import compose_caption
from backend.services.character_generator_service import _compose_prompt
from backend.services.character_identity_prompt import (
    compose_identity_core,
    resolve_class_token,
    sanitize_class_token,
    short_marks_from_subject,
)
from backend.services.swarm.agents.character_designer import ShotVariation


def test_compose_identity_core_shape():
    core = compose_identity_core("sniffy_mcgee", "man", "shaved, sunglasses, athletic")
    assert core.startswith("a photo of sniffy_mcgee, man")
    assert "shaved" in core
    assert "sunglasses" in core


def test_resolve_class_token_from_settings_and_tags():
    s = SimpleNamespace(
        training_settings_json={"class_token": "man"},
        description="",
        bible="",
    )
    assert resolve_class_token(s) == "man"

    s2 = SimpleNamespace(
        training_settings_json={"bible_vision_tags": ["stubble", "beard", "40s"]},
        description="",
        bible="",
    )
    assert resolve_class_token(s2) == "man"

    s3 = SimpleNamespace(
        training_settings_json={},
        description="a woman with red hair",
        bible="",
    )
    assert resolve_class_token(s3) == "woman"

    assert resolve_class_token(None) == "person"


def test_sanitize_and_resolve_white_wolf():
    assert sanitize_class_token("White Wolf") == "white wolf"
    assert sanitize_class_token("a white wolf") == "white wolf"
    assert compose_identity_core("frost_tok", "white wolf", "amber eyes").startswith(
        "a photo of frost_tok, white wolf"
    )
    s = SimpleNamespace(
        training_settings_json={"class_token": "white wolf"},
        description="",
        bible="",
    )
    assert resolve_class_token(s) == "white wolf"
    assert resolve_class_token(
        None, bible="A large white wolf with thick fur and amber eyes."
    ) == "white wolf"


def test_short_marks_from_subject():
    s = SimpleNamespace(
        training_settings_json={
            "bible_identity_marks": "shaved, sunglasses, athletic",
        },
    )
    assert "sunglasses" in short_marks_from_subject(s)


def test_lora_compose_has_class_anchor_not_full_bible():
    v = ShotVariation(
        framing="medium shot",
        expression="neutral",
        lighting="soft key",
        scene="urban alleyway",
    )
    bible = (
        "Sniffy McGee: long brown hair, no glasses, heavy overweight build. "
        "Keep this exact appearance in every shot."
    )
    prompt = _compose_prompt(
        "sniffy_mcgee", bible, v, "face-forward",
        include_bible=False,
        class_token="man",
        identity_marks="shaved, sunglasses, athletic",
    )
    assert "a photo of sniffy_mcgee" in prompt
    assert ", man" in prompt or " man," in prompt
    assert "shaved" in prompt
    assert "sunglasses" in prompt
    assert "long brown hair" not in prompt
    assert "heavy overweight" not in prompt
    assert "urban alleyway" in prompt


def test_compose_caption_leads_with_class_anchor():
    cap = compose_caption(
        "sniffy_mcgee",
        "close-up, looking at viewer, black jacket, studio lighting",
        "shaved, sunglasses",
        class_token="man",
    )
    assert cap.startswith("a photo of sniffy_mcgee, man")
    assert "close-up" in cap
    assert "shaved" in cap
