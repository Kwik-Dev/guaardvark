"""Auto renders with the Stills model chosen in Settings > Generation.

Settings says "the image models Guaardvark defaults to", and Tutorial 2 narrates
picking the picture model used by default. Before this, the Image Gen page's
Auto, the chat tool and the MCP tool (both reach the same façade / batch intake)
ignored that choice and picked by card size. These pin:

  1. "chosen" is distinct from "defaulted": nothing saved, or "auto", is no choice,
     so stock behaviour with no choice is exactly the per-card routing.
  2. A chosen, installed model wins in the offline router, the stills façade and
     batch intake; a choice that is not installed or will not fit falls back to
     the per-card pick (with the reason logged).
  3. A ComfyUI-only choice (FLUX) is routed by the façade and batch intake, which
     the offline router cannot do itself.

Nothing here loads a pipeline or touches a GPU.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from backend.services import media_model_registry as mmr
from backend.services import offline_image_generator as offline
from backend.services import stills_pipeline
from backend.services.batch_image_generator import (
    BatchImageGenerator,
    BatchImageRequest,
    BatchPrompt,
)


# --- the setting: chosen vs. defaulted -------------------------------------

def _settings(monkeypatch, stored, env=None):
    import backend.utils.settings_utils as su
    monkeypatch.setattr(su, "get_setting", lambda key, default=None, **kw: stored if key == mmr.SETTING_STILLS_MODEL else default)
    if env is None:
        monkeypatch.delenv(mmr.ENV_STILLS, raising=False)
    else:
        monkeypatch.setenv(mmr.ENV_STILLS, env)


@pytest.mark.parametrize("stored", [None, "", "auto", "AUTO"])
def test_nothing_chosen_is_no_choice_even_though_the_default_reads_zimage(monkeypatch, stored):
    _settings(monkeypatch, stored)
    assert mmr.get_stills_model_setting() == mmr.DEFAULT_STILLS_MODEL
    assert mmr.configured_stills_default() is None


def test_a_saved_choice_is_returned_lower_cased(monkeypatch):
    _settings(monkeypatch, "SDXL-Legacy")
    assert mmr.configured_stills_default() == "sdxl-legacy"


def test_the_environment_choice_counts_when_nothing_is_saved(monkeypatch):
    _settings(monkeypatch, None, env="krea2-turbo")
    assert mmr.configured_stills_default() == "krea2-turbo"


@pytest.mark.parametrize("profile_id, key", [
    ("zimage-turbo", "zimage-turbo"),
    ("krea2-turbo", "krea2-turbo"),
    ("sdxl-legacy", "sd-xl"),
    ("flux-dev", "flux-dev"),
    ("realistic-vision", "realistic-vision"),   # outside the registry: passes through
])
def test_profile_ids_map_to_the_catalog_key_that_renders(profile_id, key):
    assert mmr.stills_default_catalog_key(profile_id) == key


# --- the offline router ------------------------------------------------------

def _gen(monkeypatch, *, downloaded, total_vram_gb=16.0, chosen=None):
    gen = offline.OfflineImageGenerator.__new__(offline.OfflineImageGenerator)
    gen.available_models = {
        "zimage-turbo": "Tongyi-MAI/Z-Image-Turbo",
        "flux-dev": "comfy:flux-dev",
        "comfyui": "comfy:comfyui",
        "krea2-turbo": "krea/Krea-2-Turbo",
        "sd-xl": "stabilityai/stable-diffusion-xl-base-1.0",
        "realistic-vision": "SG161222/Realistic_Vision_V5.1_noVAE",
    }
    gen.comfy_only_models = {"flux-dev", "comfyui"}
    gen.user_entries = {}
    gen.user_files = {}
    gen._device = "cuda"
    on_disk = {gen.available_models[k] for k in downloaded}
    monkeypatch.setattr(gen, "_is_model_downloaded", lambda model_id: model_id in on_disk)
    monkeypatch.setattr(gen, "_cuda_total_vram_gb", lambda: total_vram_gb)
    monkeypatch.setattr(gen, "detect_content_type", lambda prompt: {})
    monkeypatch.setattr(mmr, "configured_stills_default", lambda: chosen)
    return gen


def test_with_nothing_chosen_the_router_picks_by_card_size_as_before(monkeypatch):
    small = _gen(monkeypatch, downloaded={"zimage-turbo", "krea2-turbo"}, total_vram_gb=16.0)
    assert small.configured_default_for_auto() is None
    assert small._auto_select_model("a lighthouse") == "zimage-turbo"
    roomy = _gen(monkeypatch, downloaded={"zimage-turbo", "krea2-turbo"}, total_vram_gb=24.0)
    assert roomy._auto_select_model("a lighthouse") == "krea2-turbo"


def test_a_chosen_installed_model_wins_over_the_card_size_pick(monkeypatch):
    gen = _gen(monkeypatch, downloaded={"zimage-turbo", "krea2-turbo", "sd-xl"},
               total_vram_gb=24.0, chosen="sdxl-legacy")
    monkeypatch.setattr(gen, "_vram_estimate_mb", lambda model_id, w=None, h=None: 9000)
    assert gen.configured_default_for_auto() == "sd-xl"
    assert gen._auto_select_model("a lighthouse") == "sd-xl"


def test_a_choice_that_is_not_installed_falls_back_and_says_so(monkeypatch, caplog):
    gen = _gen(monkeypatch, downloaded={"zimage-turbo"}, chosen="krea2-turbo")
    with caplog.at_level(logging.WARNING, logger=offline.__name__):
        assert gen.configured_default_for_auto() is None
        assert gen._auto_select_model("a lighthouse") == "zimage-turbo"
    assert any("not installed" in r.getMessage() and "krea2-turbo" in r.getMessage()
               for r in caplog.records)


def test_a_choice_outside_the_catalog_falls_back_and_says_so(monkeypatch, caplog):
    gen = _gen(monkeypatch, downloaded={"zimage-turbo"}, chosen="no-such-model")
    with caplog.at_level(logging.WARNING, logger=offline.__name__):
        assert gen.configured_default_for_auto() is None
    assert any("not in the image catalog" in r.getMessage() for r in caplog.records)


def test_a_choice_that_will_not_fit_the_card_falls_back_and_says_so(monkeypatch, caplog):
    gen = _gen(monkeypatch, downloaded={"zimage-turbo", "krea2-turbo"},
               total_vram_gb=8.0, chosen="krea2-turbo")
    monkeypatch.setattr(gen, "_vram_estimate_mb", lambda model_id, w=None, h=None: 14000)
    with caplog.at_level(logging.WARNING, logger=offline.__name__):
        assert gen.configured_default_for_auto() is None
        assert gen._auto_select_model("a lighthouse") == "zimage-turbo"
    assert any("VRAM" in r.getMessage() and "picking by card size" in r.getMessage()
               for r in caplog.records)


def test_a_comfy_only_choice_is_reported_by_the_router_but_not_rendered_by_it(monkeypatch):
    """flux-dev is installed (ComfyUI assets): the façade and batch intake route
    it; the offline router itself still picks a model it can load."""
    gen = _gen(monkeypatch, downloaded={"zimage-turbo", "flux-dev"}, chosen="flux-dev")
    assert gen.configured_default_for_auto() == "flux-dev"
    assert gen._auto_select_model("a lighthouse") == "zimage-turbo"


def test_an_unreadable_setting_never_breaks_auto(monkeypatch):
    gen = _gen(monkeypatch, downloaded={"zimage-turbo"})

    def boom():
        raise RuntimeError("no app context")

    monkeypatch.setattr(mmr, "configured_stills_default", boom)
    assert gen.configured_default_for_auto() is None
    assert gen._auto_select_model("a lighthouse") == "zimage-turbo"


# --- the stills façade (chat tool, MCP generate_image, batch rows) -----------

def _run_facade(monkeypatch, *, model, preferred):
    seen = {}

    def generate_one(**kw):
        seen.update(kw)
        return stills_pipeline.StillResult(success=True, image_path="x.png", prompt_used=kw["prompt"])

    monkeypatch.setattr(stills_pipeline, "configured_auto_model", lambda: preferred)
    monkeypatch.setattr(stills_pipeline, "_generate_one", generate_one)
    results = stills_pipeline.run_stills_pipeline(
        ["a brass key"], model=model, verbatim=True, hold_gpu=False,
    )
    assert results[0].success
    return seen


@pytest.mark.parametrize("model", ["auto", "", "AUTO"])
def test_the_facade_renders_auto_with_the_chosen_model(monkeypatch, model):
    seen = _run_facade(monkeypatch, model=model, preferred="sd-xl")
    assert seen["model"] == "sd-xl"


def test_the_facade_leaves_auto_alone_when_nothing_is_chosen(monkeypatch):
    seen = _run_facade(monkeypatch, model="auto", preferred=None)
    assert seen["model"] == "auto"


def test_the_facade_keeps_an_explicit_model(monkeypatch):
    seen = _run_facade(monkeypatch, model="krea2-turbo", preferred="sd-xl")
    assert seen["model"] == "krea2-turbo"


def test_a_flux_choice_gets_flux_sampling_and_the_flux_route(monkeypatch):
    """The family defaults and the engine dispatch follow the model that renders:
    Auto with a FLUX default samples at FLUX's 28 steps, not Z-Image's 9, and
    _generate_one sees "flux-dev", which it routes to ComfyUI."""
    seen = _run_facade(monkeypatch, model="auto", preferred="flux-dev")
    assert seen["model"] == "flux-dev"
    assert seen["steps"] == 28


def test_the_facade_survives_a_generator_without_the_hook(monkeypatch):
    monkeypatch.setattr(offline, "get_image_generator", lambda: SimpleNamespace())
    assert stills_pipeline.configured_auto_model() is None


# --- batch intake (Image Gen page, chat/MCP queued prompts) -----------------

def _batch(preferred):
    bg = BatchImageGenerator()
    bg.image_generator = SimpleNamespace(configured_default_for_auto=lambda: preferred)
    return bg


def _request(*prompts):
    return BatchImageRequest(batch_id="b1", prompts=list(prompts), output_dir="unused")


def test_batch_intake_gives_auto_rows_the_chosen_model_only():
    plain = BatchPrompt(id="p1", prompt="a key", model="auto")
    blank = BatchPrompt(id="p2", prompt="a cup", model="")
    explicit = BatchPrompt(id="p3", prompt="a hat", model="krea2-turbo")
    cast = BatchPrompt(id="p4", prompt="[hero] waves", model="auto", subject_ids=[7])
    bg = _batch("sd-xl")
    assert bg._apply_stills_default_to_auto(_request(plain, blank, explicit, cast)) == "sd-xl"
    assert plain.model == "sd-xl"
    assert blank.model == "sd-xl"
    assert explicit.model == "krea2-turbo"
    assert cast.model == "auto"


def test_batch_intake_changes_nothing_when_nothing_is_chosen():
    plain = BatchPrompt(id="p1", prompt="a key", model="auto")
    assert _batch(None)._apply_stills_default_to_auto(_request(plain)) is None
    assert plain.model == "auto"


def test_batch_intake_tolerates_a_generator_without_the_hook():
    bg = BatchImageGenerator()
    bg.image_generator = None
    plain = BatchPrompt(id="p1", prompt="a key", model="auto")
    assert bg._apply_stills_default_to_auto(_request(plain)) is None
    assert plain.model == "auto"


def test_a_flux_choice_routes_batch_rows_to_comfy_like_an_explicit_pick():
    plain = BatchPrompt(id="p1", prompt="a key", model="auto")
    bg = _batch("flux-dev")
    bg._apply_stills_default_to_auto(_request(plain))
    assert plain.model == "flux-dev"
    assert bg._should_use_comfy_stills(plain) is True


# --- batch intake re-resolves sampling sent for Auto's family --------------
#
# The page sends Z-Image's recipe for Auto (9 steps, guidance 0). Under a chosen
# model of another family that is a bad knob, so an untyped step count takes the
# chosen family's default and an out-of-range guidance its default; a typed step
# count and an in-range guidance stand.

def _auto_row(**kw):
    meta = {"steps_explicit": kw.pop("steps_explicit", False)}
    return BatchPrompt(id="p1", prompt="a key", model="auto", steps=kw.pop("steps", 9),
                       guidance=kw.pop("guidance", 0.0), metadata=meta)


def test_a_flux_default_gets_flux_sampling_not_zimages_recipe():
    row = _auto_row()
    req = _request(row)
    req.max_workers = 2
    _batch("flux-dev")._apply_stills_default_to_auto(req)
    assert (row.model, row.steps, row.guidance) == ("flux-dev", 28, 3.5)
    assert "FLUX" in row.metadata["steps_notice"] and "steps 9 → 28" in row.metadata["steps_notice"]
    assert req.max_workers == 1          # FLUX renders one image at a time


def test_an_sdxl_default_gets_sdxl_sampling():
    row = _auto_row()
    _batch("sd-xl")._apply_stills_default_to_auto(_request(row))
    assert (row.model, row.steps, row.guidance) == ("sd-xl", 25, 7.0)


def test_a_typed_step_count_and_an_in_range_guidance_stand():
    row = _auto_row(steps=12, steps_explicit=True, guidance=5.0)
    _batch("flux-dev")._apply_stills_default_to_auto(_request(row))
    assert (row.steps, row.guidance) == (12, 5.0)
    assert "steps_notice" not in row.metadata


def test_a_typed_step_count_keeps_but_a_zero_guidance_still_moves_for_flux():
    row = _auto_row(steps=12, steps_explicit=True, guidance=0.0)
    _batch("flux-dev")._apply_stills_default_to_auto(_request(row))
    assert (row.steps, row.guidance) == (12, 3.5)
    assert "guidance 0.0 → 3.5" in row.metadata["steps_notice"]


def test_a_zimage_default_leaves_the_pages_zimage_presets_alone():
    row = _auto_row(steps=6)          # the page's "Fast" preset for Auto
    req = _request(row)
    req.max_workers = 2
    _batch("zimage-turbo")._apply_stills_default_to_auto(req)
    assert (row.model, row.steps, row.guidance) == ("zimage-turbo", 6, 0.0)
    assert "steps_notice" not in row.metadata
    assert req.max_workers == 2


def test_an_earlier_notice_is_kept_in_front_of_the_resample_note():
    row = _auto_row()
    row.metadata["steps_notice"] = "earlier note."
    _batch("flux-dev")._apply_stills_default_to_auto(_request(row))
    assert row.metadata["steps_notice"].startswith("earlier note. Auto renders with")


# --- the other Auto paths the router change covers -------------------------
#
# generate_animation's first frame, the text-to-video first frame
# (offline_video_generator builds an ImageGenerationRequest with no model) and
# the module-level generate_image() all reach the offline router with "auto".

def test_animation_renders_with_the_chosen_model_when_it_can_img2img(monkeypatch):
    from backend.services.animation_generator import AnimationGenerator, AnimationRequest

    gen = _gen(monkeypatch, downloaded={"zimage-turbo", "sd-xl"}, chosen="sdxl-legacy")
    monkeypatch.setattr(gen, "_vram_estimate_mb", lambda model_id, w=None, h=None: 8000)
    monkeypatch.setattr(gen, "supports_img2img", lambda key: key in {"zimage-turbo", "sd-xl"})
    assert AnimationGenerator._pick_model(gen, AnimationRequest(prompt="a fox")) == ("sd-xl", None)


def test_animation_falls_back_to_an_img2img_model_when_the_choice_cannot(monkeypatch):
    """Krea 2 cannot img2img, so the chosen default is skipped for a downloaded
    model that can, exactly as an Auto pick without a choice is."""
    from backend.services.animation_generator import AnimationGenerator, AnimationRequest

    gen = _gen(monkeypatch, downloaded={"zimage-turbo", "krea2-turbo"}, chosen="krea2-turbo")
    monkeypatch.setattr(gen, "_vram_estimate_mb", lambda model_id, w=None, h=None: 10000)
    monkeypatch.setattr(gen, "supports_img2img", lambda key: key == "zimage-turbo")
    assert AnimationGenerator._pick_model(gen, AnimationRequest(prompt="a fox")) == ("zimage-turbo", None)


def test_a_request_without_a_model_is_auto_and_the_module_function_sends_one(monkeypatch):
    assert offline.ImageGenerationRequest(prompt="a fox").model == "auto"
    seen = {}

    def render(request):
        seen["model"] = request.model
        return offline.ImageGenerationResult(success=True, image_path="x.png")

    monkeypatch.setattr(offline, "get_image_generator", lambda: SimpleNamespace(generate_image=render))
    assert offline.generate_image("a fox").success
    assert seen["model"] == "auto"
