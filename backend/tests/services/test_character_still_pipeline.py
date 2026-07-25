"""character_still_pipeline chokepoint — routing, trigger lock, no Comfy for Z-Image."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.character_still_pipeline import render_character_still
from backend.services.stills_pipeline import StillResult


@pytest.fixture
def zimage_route():
    return {
        "family": "zimage",
        "inference_engine": "offline",
        "offline_model_key": "zimage-turbo",
        "comfy_model_tag": None,
        "base_model_id": "zimage-turbo",
        "profile": {"vram_infer_mb": 11000},
    }


def test_render_injects_trigger_and_uses_offline_for_zimage(tmp_path, zimage_route):
    out = tmp_path / "out.png"
    out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    class Subj:
        lora_path = str(tmp_path / "char.safetensors")
        trigger_word = "hero_tok"
        name = "Hero"
        bible = "teal highlights"

    (tmp_path / "char.safetensors").write_bytes(b"x" * 200)

    offline_result = MagicMock()
    offline_result.success = True
    offline_result.image_path = str(out)
    offline_result.seed_used = 7
    offline_result.error = None

    with patch(
        "backend.services.media_model_registry.resolve_inference_for_loras",
        return_value=zimage_route,
    ), patch(
        "backend.services.offline_image_generator.get_image_generator"
    ) as get_gen, patch(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator"
    ) as Comfy:
        gen = MagicMock()
        gen.generate_image.return_value = offline_result
        get_gen.return_value = gen

        still = render_character_still(
            "neon alley at night",
            subjects=[Subj()],
            include_bible=True,
            source="cast",
            output_path=str(tmp_path / "dest.png"),
            width=512,
            height=512,
        )

        assert still.success is True
        assert "hero_tok" in still.prompt_used
        assert "teal highlights" in still.prompt_used
        assert still.metadata.get("family") == "zimage"
        assert still.metadata.get("engine") == "offline"
        assert abs(still.metadata.get("lora_strength") - 0.9) < 1e-6
        gen.generate_image.assert_called_once()
        req = gen.generate_image.call_args[0][0]
        assert req.loras
        assert abs(req.lora_scale - 0.9) < 1e-6
        Comfy.assert_not_called()


def test_render_refuses_mixed_family_error(tmp_path):
    with patch(
        "backend.services.media_model_registry.resolve_inference_for_loras",
        side_effect=ValueError("mixed LoRA families"),
    ):
        still = render_character_still(
            "portrait",
            lora_paths=[str(tmp_path / "a.safetensors")],
            source="batch",
        )
        assert still.success is False
        assert "family" in (still.error or "").lower() or "mixed" in (still.error or "").lower()


def test_render_without_lora_returns_still_result_type():
    # Empty path: still returns StillResult (may fail offline without GPU — mock).
    with patch(
        "backend.services.offline_image_generator.get_image_generator"
    ) as get_gen:
        gen = MagicMock()
        res = MagicMock()
        res.success = False
        res.error = "no gpu"
        res.image_path = None
        gen.generate_image.return_value = res
        get_gen.return_value = gen
        still = render_character_still("a cat", source="chat", apply_subject_loras=False)
        assert isinstance(still, StillResult)
        assert still.success is False
