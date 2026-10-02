"""generate_animation picks a downloaded image model that can run img2img.

Frame 1 is txt2img and every later frame img2img, so the model must support
both. Nothing here loads a pipeline: the generator is a stand-in, or built
without __init__ so no CUDA probe runs.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.services.animation_generator import AnimationGenerator, AnimationRequest

CATALOG = {
    "zimage-turbo": "Tongyi-MAI/Z-Image-Turbo",
    "flux-dev": "comfy:flux-dev",
    "krea2-turbo": "krea/Krea-2-Turbo",
    "sd-xl": "stabilityai/stable-diffusion-xl-base-1.0",
    "realistic-vision": "SG161222/Realistic_Vision_V5.1_noVAE",
}
IMG2IMG = {"zimage-turbo", "sd-xl", "realistic-vision"}


def _generator(downloaded, auto_pick):
    return SimpleNamespace(
        available_models=CATALOG,
        supports_img2img=lambda key: key in IMG2IMG,
        _auto_select_model=lambda prompt, style: auto_pick,
        _is_model_downloaded=lambda model_id: model_id in {CATALOG[k] for k in downloaded},
    )


def test_the_default_model_is_auto_and_sampling_follows_the_family():
    request = AnimationRequest(prompt="a fox")
    assert request.model == "auto"
    assert request.num_inference_steps is None and request.guidance_scale is None


def test_auto_uses_the_auto_router_pick_when_it_can_img2img():
    gen = _generator({"zimage-turbo"}, "zimage-turbo")
    assert AnimationGenerator._pick_model(gen, AnimationRequest(prompt="a fox")) == ("zimage-turbo", None)


def test_auto_skips_a_pick_without_img2img_for_a_downloaded_one_with_it():
    gen = _generator({"krea2-turbo", "sd-xl"}, "krea2-turbo")
    assert AnimationGenerator._pick_model(gen, AnimationRequest(prompt="a fox")) == ("sd-xl", None)


def test_no_img2img_model_on_disk_is_a_clear_error():
    gen = _generator({"krea2-turbo"}, "krea2-turbo")
    model, error = AnimationGenerator._pick_model(gen, AnimationRequest(prompt="a fox"))
    assert model is None and "Z-Image Turbo" in error


def test_a_named_model_is_kept_or_refused_but_never_swapped():
    gen = _generator({"zimage-turbo"}, "zimage-turbo")
    assert AnimationGenerator._pick_model(gen, AnimationRequest(prompt="x", model="sd-xl")) == ("sd-xl", None)
    model, error = AnimationGenerator._pick_model(gen, AnimationRequest(prompt="x", model="sd-1.5"))
    assert model is None and "'sd-1.5' cannot animate" in error


def test_supports_img2img_follows_the_families_img2img_can_build():
    from backend.services import offline_image_generator as oig

    gen = oig.OfflineImageGenerator.__new__(oig.OfflineImageGenerator)
    gen.available_models = dict(CATALOG)
    gen.comfy_only_models = {"flux-dev"}
    gen.family_overrides = {}
    assert gen.supports_img2img("zimage-turbo") == (oig.ZImageImg2ImgPipeline is not None)
    assert gen.supports_img2img("sd-xl") and gen.supports_img2img("realistic-vision")
    assert not gen.supports_img2img("krea2-turbo")
    assert not gen.supports_img2img("flux-dev")
    assert not gen.supports_img2img("sd-1.5")
