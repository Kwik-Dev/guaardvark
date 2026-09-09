"""User image catalog: entry shapes, family-safe ids, generator registration, LoRA resolution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import user_image_models as uim


def _generator(tmp_path):
    return SimpleNamespace(
        models_dir=tmp_path / "stable_diffusion",
        available_models={"zimage-turbo": "Tongyi-MAI/Z-Image-Turbo", "sd-xl": "stabilityai/stable-diffusion-xl-base-1.0"},
        model_meta={},
        hidden_models=set(),
    )


@pytest.fixture
def catalog_dir(tmp_path, monkeypatch):
    path = tmp_path / "user_image_models.json"
    monkeypatch.setattr(uim, "_CATALOG_PATH_OVERRIDE", path)
    return path


def test_ids_carry_the_family_and_scrub_other_markers():
    # The page and stills_defaults read the family off the id string, so a slug
    # must not carry another family's marker.
    assert uim.user_model_id("Juggernaut XL v9", "sdxl") == "user-sdxl-juggernaut-xl-v9"
    assert uim.user_model_id("krea xl mix", "sd") == "user-sd-mix"
    assert uim.user_model_id("flux style", "zimage") == "user-zimage-style"
    assert uim.user_model_id("thing", "sd", taken={"user-sd-thing"}) == "user-sd-thing-2"


def test_suggest_role_and_family():
    assert uim.suggest_role_and_family([], "my_lora.safetensors", "x/y", False) == ("lora", "zimage")
    assert uim.suggest_role_and_family([], None, "RunDiffusion/Juggernaut-XL-v9", True) == ("generation", "sdxl")
    assert uim.suggest_role_and_family([{"src": "model.safetensors"}], None, "someone/realism-sd15", False) == ("generation", "sd")


def test_snapshot_entry_registers_as_a_repo(catalog_dir, tmp_path):
    gen = _generator(tmp_path)
    mid, entry = uim.add_user_model(
        gen, role="generation", family="sdxl", hf_repo="RunDiffusion/Juggernaut-XL-v9",
        files=[], has_model_index=True, name="Juggernaut XL",
    )
    assert entry["kind"] == "snapshot" and entry["files"] == []
    assert gen.available_models[mid] == "RunDiffusion/Juggernaut-XL-v9"
    assert gen.family_overrides[mid] == "sdxl"
    assert gen.family_overrides["RunDiffusion/Juggernaut-XL-v9"] == "sdxl"
    assert gen.model_meta[mid]["user"] is True and mid not in gen.hidden_models
    saved = json.loads(catalog_dir.read_text())
    assert mid in saved["models"]
    uim.remove_user_model(gen, mid)
    assert mid not in gen.available_models and mid not in json.loads(catalog_dir.read_text())["models"]


def test_single_file_only_for_sd_families(catalog_dir, tmp_path):
    gen = _generator(tmp_path)
    with pytest.raises(ValueError, match="diffusers repo"):
        uim.add_user_model(
            gen, role="generation", family="zimage", hf_repo="x/y",
            files=[{"src": "merged.safetensors"}], has_model_index=False,
        )
    mid, entry = uim.add_user_model(
        gen, role="generation", family="sd", hf_repo="x/y",
        files=[{"src": "merged.safetensors", "size": 2_000_000_000}], has_model_index=False, name="merged",
    )
    assert entry["kind"] == "single_file"
    sentinel = uim.user_sentinel(mid)
    assert gen.available_models[mid] == sentinel
    assert gen.user_files[sentinel]["files"] == [{"src": "merged.safetensors", "dst": "merged.safetensors"}]
    assert uim.user_files_present(gen, sentinel) is False
    target = gen.models_dir / mid / "merged.safetensors"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    assert uim.user_files_present(gen, sentinel) is True


def test_lora_entry_is_hidden_from_the_picker_and_resolves_to_a_path(catalog_dir, tmp_path):
    gen = _generator(tmp_path)
    with pytest.raises(ValueError, match="not stacked"):
        uim.add_user_model(gen, role="lora", family="sdxl", hf_repo="x/y", files=[{"src": "l.safetensors"}])
    mid, entry = uim.add_user_model(gen, role="lora", family="zimage", hf_repo="x/y", files=[{"src": "l.safetensors"}], name="Realism")
    assert entry["applies_to"] == ["zimage"] and mid in gen.hidden_models
    rows = uim.catalog_rows(gen)
    assert rows[0]["id"] == mid and rows[0]["is_downloaded"] is False

    paths, scale, err = uim.resolve_user_loras(gen, "zimage-turbo", [{"id": mid}])
    assert paths == [] and "not installed" in err
    target = gen.models_dir / mid / "l.safetensors"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    paths, scale, err = uim.resolve_user_loras(gen, "zimage-turbo", [{"id": mid, "strength": 0.6}])
    assert err is None and paths == [str(target)] and scale == 0.6
    paths, scale, err = uim.resolve_user_loras(gen, "sd-xl", [mid])
    assert paths == [] and "does not apply" in err
    paths, scale, err = uim.resolve_user_loras(gen, "zimage-turbo", [{"id": "zimage-turbo"}])
    assert "not a LoRA" in err
    assert uim.resolve_user_loras(gen, "auto", []) == ([], None, None)


def test_load_user_catalog_skips_bad_rows(catalog_dir, tmp_path):
    catalog_dir.write_text(json.dumps({"models": {
        "user-sd-good": {"name": "g", "role": "generation", "family": "sd", "kind": "snapshot",
                         "hf_repo": "a/b", "files": [], "user": True},
        "sd-xl": {"name": "shipped id must not be overridden"},
    }}))
    gen = _generator(tmp_path)
    assert uim.load_user_catalog(gen) == 1
    assert gen.available_models["user-sd-good"] == "a/b"
    assert gen.available_models["sd-xl"] == "stabilityai/stable-diffusion-xl-base-1.0"


def test_cannot_remove_shipped_key(catalog_dir, tmp_path):
    with pytest.raises(ValueError, match="shipped"):
        uim.remove_user_model(_generator(tmp_path), "sd-xl")
