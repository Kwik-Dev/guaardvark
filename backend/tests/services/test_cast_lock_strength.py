"""Family-aware character LoRA strength (Z-Image / SDXL / FLUX)."""
from backend.services.cast_lock import (
    DEFAULT_FLUX_DEV_STRENGTH,
    DEFAULT_SDXL_STRENGTH,
    DEFAULT_ZIMAGE_STRENGTH,
    apply_lock,
    resolve_lora_strength,
    subjects_to_lock,
)


def test_resolve_lora_strength_zimage_default():
    assert resolve_lora_strength("zimage-turbo") == DEFAULT_ZIMAGE_STRENGTH
    assert resolve_lora_strength("zimage") == DEFAULT_ZIMAGE_STRENGTH


def test_resolve_lora_strength_sdxl_and_flux():
    assert resolve_lora_strength("sdxl") == DEFAULT_SDXL_STRENGTH
    assert resolve_lora_strength("flux-dev") == DEFAULT_FLUX_DEV_STRENGTH


def test_resolve_lora_strength_override_wins():
    assert resolve_lora_strength("zimage-turbo", 0.55) == 0.55
    assert resolve_lora_strength("sdxl", 1.2) == 1.2


def test_apply_lock_and_subjects_lora_omits_full_bible():
    class S:
        def __init__(self):
            self.lora_path = "/tmp/x.safetensors"
            self.trigger_word = "tok"
            self.name = "Hero"
            self.bible = "long invented paragraph about hazel eyes and auburn hair"
            self.training_settings_json = {
                "class_token": "man",
                "bible_identity_marks": "shaved head, sunglasses",
            }

    paths, lock = subjects_to_lock([S()], include_bible=True)
    assert paths == ["/tmp/x.safetensors"]
    assert "tok" in lock
    assert "a photo of tok" in lock
    assert "shaved head" in lock
    # Full bible must not fight the LoRA at render time
    assert "auburn hair" not in lock
    assert "long invented paragraph" not in lock
    assert apply_lock("neon alley", lock).startswith("a photo of tok")
