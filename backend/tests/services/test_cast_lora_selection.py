"""cast_lora_selection — which LoRA each Cast member renders with (#245 phase 2).

Rows are passed in directly, so nothing here reads a database.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services import cast_lora_selection as sel
from backend.services.cast_lora_selection import (
    CastLoraRefusal,
    select_cast_loras,
    selected_lora_paths,
)

ZIMAGE, FLUX, SDXL = "zimage-turbo", "flux-dev", "sdxl-legacy"


def member(sid, name, lora_path, base, trigger=None, **extra):
    settings = {"base_model_id": base, "class_token": "woman"}
    settings.update(extra.pop("settings", {}))
    return SimpleNamespace(
        id=sid,
        name=name,
        kind="character",
        lora_path=lora_path,
        trigger_word=trigger or name.lower(),
        bible=extra.pop("bible", None),
        training_settings_json=settings,
        **extra,
    )


def row(base, path, version=1, trigger=None, source="imported"):
    return {
        "base_model_id": base,
        "lora_path": path,
        "version": version,
        "trigger_word": trigger,
        "source": source,
    }


@pytest.fixture
def ivy():
    return member(1, "Ivy", "/loras/ivy_zimage.safetensors", ZIMAGE, trigger="ivyx")


@pytest.fixture
def max_():
    return member(2, "Max", "/loras/max_flux.safetensors", FLUX, trigger="maxx")


@pytest.fixture
def ivy_rows():
    return {
        1: [
            row(ZIMAGE, "/loras/ivy_zimage.safetensors", trigger="ivyx", source="trained"),
            row(FLUX, "/loras/ivy_flux.safetensors", trigger="ivy_flux"),
        ]
    }


def _bases(selection):
    return [s.training_settings_json["base_model_id"] for s in selection.subjects]


def test_members_without_rows_are_untouched_on_auto_or_a_matching_model(ivy):
    for model in (None, "auto", "comfyui", ZIMAGE):
        result = select_cast_loras([ivy], model, rows_by_subject={})
        assert result.legacy is True
        assert result.base_model_id is None
        assert result.subjects[0] is ivy


@pytest.mark.parametrize("model", [FLUX, "krea2-turbo", "sd-xl"])
def test_a_member_without_rows_is_refused_a_model_its_lora_cannot_render(ivy, model):
    with pytest.raises(CastLoraRefusal) as err:
        select_cast_loras([ivy], model, rows_by_subject={})
    assert "Ivy has a LoRA for Z-Image Turbo only" in str(err.value)


def test_stand_ins_never_reach_the_database(ivy, monkeypatch):
    loader = MagicMock(return_value={})
    monkeypatch.setattr(sel, "load_subject_lora_rows", loader)
    select_cast_loras([ivy], ZIMAGE)
    loader.assert_called_once_with([])


def test_auto_renders_with_the_default_lora(ivy, ivy_rows):
    result = select_cast_loras([ivy], "auto", rows_by_subject=ivy_rows)
    assert result.legacy is False
    assert result.base_model_id == ZIMAGE
    assert selected_lora_paths(result) == ["/loras/ivy_zimage.safetensors"]
    assert result.subjects[0].trigger_word == "ivyx"


def test_comfyui_backend_selector_is_treated_as_auto(ivy, ivy_rows):
    result = select_cast_loras([ivy], "comfyui", rows_by_subject=ivy_rows)
    assert result.base_model_id == ZIMAGE


def test_a_picked_model_uses_the_lora_for_its_base(ivy, ivy_rows):
    result = select_cast_loras([ivy], FLUX, rows_by_subject=ivy_rows)
    view = result.subjects[0]
    assert result.base_model_id == FLUX
    assert view.lora_path == "/loras/ivy_flux.safetensors"
    assert view.trigger_word == "ivy_flux"
    assert view.training_settings_json["base_model_id"] == FLUX


def test_a_model_of_the_same_family_uses_that_family_lora(ivy, ivy_rows):
    result = select_cast_loras([ivy], "flux-schnell", rows_by_subject=ivy_rows)
    assert selected_lora_paths(result) == ["/loras/ivy_flux.safetensors"]


def test_a_picked_model_without_a_lora_is_refused_naming_what_the_member_has(ivy, ivy_rows):
    with pytest.raises(CastLoraRefusal) as err:
        select_cast_loras([ivy], "sd-xl", rows_by_subject=ivy_rows)
    msg = str(err.value)
    assert "Ivy has LoRAs for Z-Image Turbo and FLUX.1 Dev only" in msg
    assert "SDXL" in msg


def test_a_single_lora_member_is_refused_in_the_singular(ivy):
    rows = {1: [row(ZIMAGE, "/loras/ivy_zimage.safetensors", trigger="ivyx", source="trained")]}
    with pytest.raises(CastLoraRefusal) as err:
        select_cast_loras([ivy], FLUX, rows_by_subject=rows)
    assert "Ivy has a LoRA for Z-Image Turbo only, not for FLUX.1 Dev." in str(err.value)


def test_the_highest_version_of_a_base_is_current(ivy, ivy_rows):
    ivy_rows[1].append(row(FLUX, "/loras/ivy_flux_v2.safetensors", version=2, trigger="ivy_flux"))
    result = select_cast_loras([ivy], FLUX, rows_by_subject=ivy_rows)
    assert selected_lora_paths(result) == ["/loras/ivy_flux_v2.safetensors"]


def test_the_default_lora_stands_for_its_base_without_a_row(ivy):
    rows = {1: [row(FLUX, "/loras/ivy_flux.safetensors")]}
    result = select_cast_loras([ivy], ZIMAGE, rows_by_subject=rows)
    assert selected_lora_paths(result) == ["/loras/ivy_zimage.safetensors"]
    assert result.subjects[0].trigger_word == "ivyx"


def test_several_members_render_on_a_base_they_share(ivy, ivy_rows, max_):
    rows = dict(ivy_rows)
    rows[2] = [row(FLUX, "/loras/max_flux.safetensors", trigger="maxx", source="trained")]
    result = select_cast_loras([ivy, max_], None, rows_by_subject=rows)
    assert result.base_model_id == FLUX
    assert _bases(result) == [FLUX, FLUX]
    assert selected_lora_paths(result) == [
        "/loras/ivy_flux.safetensors",
        "/loras/max_flux.safetensors",
    ]


def test_several_members_prefer_the_first_members_default_base(ivy, ivy_rows, max_):
    rows = dict(ivy_rows)
    rows[2] = [
        row(FLUX, "/loras/max_flux.safetensors", trigger="maxx", source="trained"),
        row(ZIMAGE, "/loras/max_zimage.safetensors", trigger="maxx"),
    ]
    result = select_cast_loras([ivy, max_], "auto", rows_by_subject=rows)
    assert result.base_model_id == ZIMAGE


def test_members_with_no_shared_base_are_refused(ivy, max_):
    rows = {
        1: [row(ZIMAGE, "/loras/ivy_zimage.safetensors", trigger="ivyx", source="trained")],
        2: [row(FLUX, "/loras/max_flux.safetensors", trigger="maxx", source="trained")],
    }
    with pytest.raises(CastLoraRefusal) as err:
        select_cast_loras([ivy, max_], None, rows_by_subject=rows)
    msg = str(err.value)
    assert "Ivy (Z-Image Turbo) and Max (FLUX.1 Dev)" in msg
    assert "can't be rendered in one image" in msg


def test_a_member_without_rows_still_counts_in_a_mixed_cast(ivy, max_):
    rows = {1: [row(ZIMAGE, "/loras/ivy_zimage.safetensors", trigger="ivyx", source="trained")]}
    with pytest.raises(CastLoraRefusal):
        select_cast_loras([ivy, max_], None, rows_by_subject=rows)


def test_a_picked_model_refusal_names_each_member_lacking_it(ivy, ivy_rows, max_):
    rows = dict(ivy_rows)
    rows[2] = [row(FLUX, "/loras/max_flux.safetensors", trigger="maxx", source="trained")]
    with pytest.raises(CastLoraRefusal) as err:
        select_cast_loras([ivy, max_], ZIMAGE, rows_by_subject=rows)
    msg = str(err.value)
    assert "Max has a LoRA for FLUX.1 Dev only" in msg
    assert "Ivy" not in msg


def test_a_pinned_non_default_path_fixes_the_base_on_auto(ivy, ivy_rows):
    result = select_cast_loras(
        [ivy], None, pin_paths=["/loras/ivy_flux.safetensors"], rows_by_subject=ivy_rows,
    )
    assert result.base_model_id == FLUX


def test_pinning_the_default_path_changes_nothing(ivy, ivy_rows):
    result = select_cast_loras(
        [ivy], None, pin_paths=["/loras/ivy_zimage.safetensors"], rows_by_subject=ivy_rows,
    )
    assert result.base_model_id == ZIMAGE


def test_member_paths_cover_every_lora_the_members_hold(ivy, ivy_rows):
    result = select_cast_loras([ivy], None, rows_by_subject=ivy_rows)
    assert result.member_paths == {
        "/loras/ivy_zimage.safetensors",
        "/loras/ivy_flux.safetensors",
    }


def test_the_view_reads_through_and_leaves_the_member_alone(ivy, ivy_rows):
    ivy.bible = "silver hair, green coat"
    ivy.training_settings_json["bible_identity_marks"] = "scar over left eye"
    view = select_cast_loras([ivy], FLUX, rows_by_subject=ivy_rows).subjects[0]
    assert view.id == 1 and view.name == "Ivy" and view.bible == "silver hair, green coat"
    assert view.training_settings_json["bible_identity_marks"] == "scar over left eye"
    assert ivy.lora_path == "/loras/ivy_zimage.safetensors"
    assert ivy.training_settings_json["base_model_id"] == ZIMAGE


def test_untrained_members_pass_through(ivy, ivy_rows):
    extra = member(3, "Extra", None, ZIMAGE)
    result = select_cast_loras([ivy, extra], FLUX, rows_by_subject=ivy_rows)
    assert result.subjects[1] is extra


def test_the_lock_uses_the_chosen_trigger(ivy, ivy_rows):
    from backend.services.cast_lock import subjects_to_lock

    view_members = select_cast_loras([ivy], FLUX, rows_by_subject=ivy_rows).subjects
    paths, lock = subjects_to_lock(view_members, include_bible=False)
    assert paths == ["/loras/ivy_flux.safetensors"]
    assert "ivy_flux" in lock and "ivyx" not in lock


def _with_rows(rows):
    real = sel.select_cast_loras

    def fake(subjects, image_model=None, *, pin_paths=(), rows_by_subject=None):
        return real(subjects, image_model, pin_paths=pin_paths, rows_by_subject=rows)

    return patch("backend.services.cast_lora_selection.select_cast_loras", side_effect=fake)


def test_render_refuses_a_picked_model_the_member_has_no_lora_for(ivy, ivy_rows):
    from backend.services.character_still_pipeline import render_character_still

    with _with_rows(ivy_rows), patch(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator"
    ) as Comfy, patch(
        "backend.services.offline_image_generator.get_image_generator"
    ) as get_gen:
        still = render_character_still("a portrait", subjects=[ivy], image_model="sd-xl")
    assert still.success is False
    assert "Ivy has LoRAs for Z-Image Turbo and FLUX.1 Dev only" in still.error
    Comfy.assert_not_called()
    get_gen.assert_not_called()


def test_render_refuses_a_member_without_rows_on_a_model_its_lora_cannot_render(ivy):
    from backend.services.character_still_pipeline import render_character_still

    with _with_rows({}), patch(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator"
    ) as Comfy, patch(
        "backend.services.offline_image_generator.get_image_generator"
    ) as get_gen:
        still = render_character_still("a portrait", subjects=[ivy], image_model=FLUX)
    assert still.success is False
    assert "Ivy has a LoRA for Z-Image Turbo only, not for FLUX.1 Dev." in still.error
    Comfy.assert_not_called()
    get_gen.assert_not_called()


def test_render_on_a_picked_model_loads_that_base_lora(tmp_path, ivy, ivy_rows):
    from backend.services.character_still_pipeline import render_character_still

    out = tmp_path / "out.png"
    out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    with _with_rows(ivy_rows), patch(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator"
    ) as Comfy, patch(
        "backend.services.offline_image_generator.get_image_generator"
    ) as get_gen:
        comfy_gen = MagicMock()
        comfy_gen.generate_image.return_value = str(out)
        Comfy.return_value = comfy_gen
        still = render_character_still(
            "a portrait",
            subjects=[ivy],
            lora_paths=["/loras/ivy_zimage.safetensors"],
            image_model=FLUX,
            output_path=str(tmp_path / "dest.png"),
            width=1024,
            height=1024,
        )
    assert still.success is True, still.error
    assert still.metadata["base_model_id"] == FLUX
    kwargs = comfy_gen.generate_image.call_args[1]
    assert kwargs["loras"] == ["/loras/ivy_flux.safetensors"]
    assert kwargs["model"] == "flux-dev"
    assert "ivy_flux" in still.prompt_used
    get_gen.assert_not_called()
