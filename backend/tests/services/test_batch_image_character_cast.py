"""Batch image cast must pass subject_ids into character_still_pipeline."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.services.batch_image_generator import BatchImageGenerator, BatchPrompt
from backend.services.stills_pipeline import StillResult


def test_generate_with_character_lora_passes_subject_ids():
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    prompt = BatchPrompt(
        id="p1",
        prompt="walking through Gotham rain",
        subject_ids=[42],
        loras=["/tmp/batman.safetensors"],
        width=1024,
        height=1024,
        steps=9,
        guidance=0.0,
    )
    fake = StillResult(
        success=True,
        image_path="/tmp/out.png",
        prompt_used="a photo of bat_tok, man, cowl, walking through Gotham rain",
        model_used="zimage-turbo+lora",
        width=1024,
        height=1024,
        seed_used=1,
        metadata={
            "family": "zimage",
            "lora_strength": 0.9,
            "lock_prefix": "a photo of bat_tok, man, cowl",
        },
    )
    with patch(
        "backend.services.character_still_pipeline.render_character_still",
        return_value=fake,
    ) as render:
        result = gen._generate_with_character_lora(prompt)
    assert result is not None
    assert result.success is True
    kwargs = render.call_args.kwargs
    assert kwargs.get("subject_ids") == [42]
    # Paths always passed as fallback if worker subject resolve fails
    assert kwargs.get("lora_paths") == ["/tmp/batman.safetensors"]
    assert kwargs.get("source") == "batch"
    # Scene only — no bare trigger prepend
    assert render.call_args.args[0] == "walking through Gotham rain"


def test_apply_character_casting_stores_subject_ids():
    from backend.api.batch_image_generation_api import _apply_character_casting

    class Subj:
        def __init__(self, sid, path, name="Bat"):
            self.id = sid
            self.lora_path = path
            self.name = name
            self.trigger_word = "bat_tok"

    trained = Subj(7, "/data/lora/bat.safetensors")
    untrained = Subj(8, None, name="Extra")

    def fake_get(_model, sid):
        return {7: trained, 8: untrained}.get(int(sid))

    params: dict = {}
    with patch("backend.models.db") as db:
        db.session.get.side_effect = fake_get
        _apply_character_casting({"subject_ids": [7, 8]}, params)

    assert params["subject_ids"] == [7]
    assert params["loras"] == ["/data/lora/bat.safetensors"]
    assert params.get("_cast_warnings")
