"""Media model registry — stills / cast train base / LoRA sidecar contract."""
import json
from pathlib import Path

import pytest

from backend.services import media_model_registry as mmr


def test_default_stills_is_zimage():
    assert mmr.DEFAULT_STILLS_MODEL == mmr.ZIMAGE_TURBO


def test_default_cast_train_is_zimage():
    assert mmr.DEFAULT_CAST_TRAIN_BASE == mmr.ZIMAGE_TURBO


def test_flux_is_max_quality_default():
    assert mmr.DEFAULT_MAX_QUALITY_MODEL == mmr.FLUX_DEV


def test_list_train_profiles_includes_zimage_and_legacy():
    ids = {p["id"] for p in mmr.list_profiles(role="lora_train")}
    assert mmr.ZIMAGE_TURBO in ids
    assert mmr.FLUX_DEV in ids
    assert mmr.SDXL_LEGACY in ids


def test_get_profile_aliases():
    assert mmr.get_profile("sd-xl")["id"] == mmr.SDXL_LEGACY
    assert mmr.get_profile("flux")["id"] == mmr.FLUX_DEV
    assert mmr.get_profile("z-image-turbo")["id"] == mmr.ZIMAGE_TURBO


def test_assert_train_ready_sdxl_ok():
    p = mmr.assert_train_ready(mmr.SDXL_LEGACY)
    assert p["train_ready"] is True
    assert p["train_backend"] == "peft_sdxl"


def test_assert_train_ready_zimage_ok():
    p = mmr.assert_train_ready(mmr.ZIMAGE_TURBO)
    assert p["train_ready"] is True
    assert p["train_backend"] == "peft_zimage"


def test_assert_train_ready_flux_raises():
    with pytest.raises(ValueError, match="not ready"):
        mmr.assert_train_ready(mmr.FLUX_DEV)


def test_lora_compatible_same_family(monkeypatch):
    monkeypatch.setattr(mmr, "get_stills_model_setting", lambda: mmr.ZIMAGE_TURBO)
    assert mmr.lora_compatible_with_inference(mmr.ZIMAGE_TURBO, "zimage-turbo") is True
    assert mmr.lora_compatible_with_inference(mmr.ZIMAGE_TURBO, "sd-xl") is False
    assert mmr.lora_compatible_with_inference(mmr.SDXL_LEGACY, "sdxl") is True


def test_write_and_resolve_sidecar(tmp_path):
    lora = tmp_path / "hero_v1.safetensors"
    lora.write_bytes(b"x" * 200)
    mmr.write_lora_sidecar(
        lora,
        subject_id=7,
        subject_name="Hero",
        trigger_word="sks_hero",
        base_model_id=mmr.SDXL_LEGACY,
        ref_count=12,
        steps=800,
        mock=False,
    )
    meta = mmr.read_lora_sidecar(str(lora))
    assert meta["base_model_id"] == mmr.SDXL_LEGACY
    assert meta["lora_format"] == "kohya_sdxl"
    assert meta["schema_version"] == 2
    assert meta["mock"] is False

    route = mmr.resolve_inference_for_loras([str(lora)])
    assert route["family"] == "sdxl"
    assert route["comfy_model_tag"] == "sdxl"


def test_resolve_mixed_bases_raises(tmp_path):
    a = tmp_path / "a.safetensors"
    b = tmp_path / "b.safetensors"
    a.write_bytes(b"a" * 200)
    b.write_bytes(b"b" * 200)
    mmr.write_lora_sidecar(
        a, subject_id=1, subject_name="A", trigger_word="a",
        base_model_id=mmr.SDXL_LEGACY, ref_count=1,
    )
    mmr.write_lora_sidecar(
        b, subject_id=2, subject_name="B", trigger_word="b",
        base_model_id=mmr.ZIMAGE_TURBO, ref_count=1,
    )
    with pytest.raises(ValueError, match="mix"):
        mmr.resolve_inference_for_loras([str(a), str(b)])


def test_legacy_path_without_sidecar_assumes_sdxl(tmp_path):
    lora = tmp_path / "old.safetensors"
    lora.write_bytes(b"x" * 200)
    route = mmr.resolve_inference_for_loras([str(lora)])
    assert route["base_model_id"] == mmr.SDXL_LEGACY
