"""A user-added model is validated against the shipped model its family clones,
not the SD 1.5 fallback (which hard-clamped a Z-Image model's guidance 0 to 1.0)."""

from backend.services.settings_validator import _user_model_like, get_settings_validator

UNKNOWN = "Unknown model"


def _validate(model, guidance, steps, width=1024, height=1024):
    return get_settings_validator().validate_settings(
        model=model, guidance=guidance, steps=steps, width=width, height=height, auto_correct=True
    )


def test_user_ids_resolve_to_their_family_like():
    assert _user_model_like("user-zimage-my-finetune") == "zimage-turbo"
    assert _user_model_like("user-qwen-image-21-uc-fp8") == "qwen-image-21"
    # "sdxl-" is matched before "sd-", so an SDXL id never lands on SD 1.5.
    assert _user_model_like("user-sdxl-juggernaut") == "sd-xl"
    assert _user_model_like("zimage-turbo") is None
    assert _user_model_like("user-unknownfamily-x") is None


def test_a_user_zimage_model_keeps_its_cfg_free_settings():
    r = _validate("user-zimage-my-finetune", guidance=0.0, steps=9)
    assert r.corrected_values == {}
    assert not any(UNKNOWN in w for w in r.warnings)


def test_a_user_qwen21_model_uses_the_qwen21_rules():
    r = _validate("user-qwen-image-21-uc-fp8", guidance=1.0, steps=25, width=2048, height=2048)
    assert r.corrected_values == {}
    assert not any(UNKNOWN in w for w in r.warnings)


def test_a_user_sdxl_model_gets_the_sdxl_hard_clamp():
    # SDXL renders black above guidance 9; its range is enforced for user copies too.
    r = _validate("user-sdxl-juggernaut", guidance=12.0, steps=25)
    assert r.corrected_values.get("guidance") == 9.0


def test_an_unknown_model_still_falls_back_to_sd15():
    r = _validate("someone/unknown-model", guidance=7.0, steps=25, width=512, height=512)
    assert any(UNKNOWN in w for w in r.warnings)
