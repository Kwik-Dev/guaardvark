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
        bible = "long invented paragraph about teal highlights"  # must NOT dump full bible
        training_settings_json = {
            "base_model_id": "zimage-turbo",
            "class_token": "man",
            "bible_identity_marks": "teal highlights, cowl",
        }

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
            include_bible=False,
            source="cast",
            output_path=str(tmp_path / "dest.png"),
            width=512,
            height=512,
        )

        assert still.success is True
        assert "hero_tok" in still.prompt_used
        assert "a photo of hero_tok" in still.prompt_used
        assert "man" in still.prompt_used
        assert "teal highlights" in still.prompt_used
        assert "long invented paragraph" not in still.prompt_used
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


def test_cast_requested_but_empty_paths_fails_loudly():
    """Do not silently render generic T2I when cast was requested but nothing resolved."""
    still = render_character_still(
        "batman in the rain",
        subject_ids=[999999],  # missing — resolve yields no paths
        lora_paths=[],
        source="batch",
    )
    assert still.success is False
    assert "no LoRA" in (still.error or "").lower() or "cast" in (still.error or "").lower()


class TestResolveCastFromModel:
    """LLMs often pass a cast trigger as the `model` name instead of subject_ids
    (e.g. model=\"starship_captain_lora\"). _resolve_cast_from_model must map those
    aliases back to trained Subject ids so the LoRA actually loads."""

    @staticmethod
    def _run(model, rows):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from backend.tools import image_tools as it

        fake_query = MagicMock()
        fake_query.filter.return_value = fake_query
        fake_query.all.return_value = rows

        fake_subject = type(
            "Subject",
            (),
            {
                "query": fake_query,
                "kind": "character",
                "lora_path": MagicMock(),  # provides .isnot() and != for the filter chain
            },
        )()
        fake_db = SimpleNamespace(session=SimpleNamespace(remove=lambda: None))
        ctx = MagicMock()  # MagicMock is a context manager
        fake_app = SimpleNamespace(app_context=lambda: ctx)
        with (
            patch("backend.app.get_or_create_app", return_value=fake_app),
            patch("backend.models.Subject", fake_subject),
            patch("backend.models.db", fake_db),
        ):
            return it._resolve_cast_from_model(model)

    @staticmethod
    def _subject(sid, trigger, name):
        return type("S", (), {
            "id": sid, "trigger_word": trigger, "name": name,
            "lora_path": f"/fake/{trigger}_v1.safetensors",
        })()

    def test_lora_model_alias_resolves(self):
        subj = self._subject(12, "starship_captain", "Starship Captain")
        for model in (
            "starship_captain_lora",
            "starship_captain",
            "[starship_captain]",
            "Starship Captain",
        ):
            assert self._run(model, [subj]) == [12], model

    def test_plain_base_models_do_not_resolve(self):
        subj = self._subject(12, "starship_captain", "Starship Captain")
        for model in ("flux-dev", "zimage-turbo", "auto", "krea2-turbo", ""):
            assert self._run(model, [subj]) == [], model

    def test_no_matching_subjects_returns_empty(self):
        assert self._run("starship_captain_lora", []) == []


class TestResolveCastFromPrompt:
    """_resolve_cast_from_prompt must catch the trigger in the prompt OR the original
    user message, in bracket / underscore / plain-name forms."""

    @staticmethod
    def _run(text, rows):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from backend.tools import image_tools as it

        fake_query = MagicMock()
        fake_query.filter.return_value = fake_query
        fake_query.all.return_value = rows
        fake_subject = type(
            "Subject", (), {"query": fake_query, "kind": "character", "lora_path": MagicMock()}
        )()
        fake_db = SimpleNamespace(session=SimpleNamespace(remove=lambda: None))
        fake_app = SimpleNamespace(app_context=lambda: MagicMock())
        with (
            patch("backend.app.get_or_create_app", return_value=fake_app),
            patch("backend.models.Subject", fake_subject),
            patch("backend.models.db", fake_db),
        ):
            return it._resolve_cast_from_prompt(text)

    @staticmethod
    def _subject(sid, trigger, name):
        return type("S", (), {
            "id": sid, "trigger_word": trigger, "name": name, "lora_path": "/fake/lora.safetensors"
        })()

    def test_prompt_forms_resolve(self):
        subj = self._subject(12, "starship_captain", "Starship Captain")
        for text in (
            "generate [starship_captain] on the bridge",
            "generate starship_captain on the bridge",
            "generate Starship Captain on the bridge",
            "character reference sheet of [starship_captain] using his trained LoRA",
            "character reference sheet of Starship Captain using his trained LoRA",
        ):
            assert self._run(text, [subj]) == [12], text

    def test_unrelated_text_does_not_resolve(self):
        subj = self._subject(12, "starship_captain", "Starship Captain")
        for text in ("generate a spaceship bridge", "draw a captain hat"):
            assert self._run(text, [subj]) == [], text
