"""Parity tests: chat/batch builders resolve the same request fields via stills_pipeline."""
from backend.services.stills_defaults import resolve_stills_defaults
from backend.services.stills_policy import resolve_enhance_mode
from backend.services.image_prompt_sanitize import sanitize_image_prompt


def test_sanitize_then_defaults_match_across_surfaces(monkeypatch):
    monkeypatch.setattr(
        "backend.services.media_director.verbatim_prompts_enabled",
        lambda: True,
    )
    raw = "generate an image of the joker in a rainy alley, cinematic"
    cleaned = sanitize_image_prompt(raw)
    assert cleaned == "the joker in a rainy alley, cinematic"

    chat = resolve_stills_defaults("zimage-turbo", width=1024, height=1024)
    batch = resolve_stills_defaults(
        "zimage-turbo", width=512, height=512, steps=20, guidance=7.5,
        replace_legacy_sd_markers=True,
    )
    assert chat["width"] == batch["width"] == 1024
    assert chat["steps"] == batch["steps"] == 8
    assert chat["guidance"] == batch["guidance"] == 1.0

    # Verbatim → same enhance mode for chat and batch
    assert resolve_enhance_mode(auto_enhance=True) == "none"
    assert resolve_enhance_mode(director=True) == "none"


def test_non_verbatim_default_is_offline_not_director(monkeypatch):
    monkeypatch.setattr(
        "backend.services.media_director.verbatim_prompts_enabled",
        lambda: False,
    )
    assert resolve_enhance_mode() == "offline"
    assert resolve_enhance_mode(director=False, auto_enhance=True) == "offline"


def test_batch_params_include_director_mode():
    """Regression: director_mode must not be stripped when building BatchImageRequest."""
    from backend.services.batch_image_generator import BatchImageGenerator
    gen = BatchImageGenerator()
    # Inspect create_batch_from_prompts kwargs filter via a dry construct
    src = gen.create_batch_from_prompts.__code__.co_consts
    # Safer: call with director_mode and inspect request
    req = gen.create_batch_from_prompts(
        ["a red sports car"],
        model="zimage-turbo",
        director_mode=True,
        director_guidance="noir lighting",
        auto_enhance=True,
    )
    assert req.director_mode is True
    assert req.director_guidance == "noir lighting"
    assert req.prompts[0].prompt == "a red sports car"
