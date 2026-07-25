"""Family-aware 2K / Flux~2MP resolution clamps."""
from backend.services.image_resolution_limits import (
    clamp_image_dimensions,
    family_limits,
    resolve_family,
)


def test_resolve_family():
    assert resolve_family("zimage-turbo") == "zimage"
    assert resolve_family("krea2-raw") == "krea2"
    assert resolve_family("flux-dev") == "flux"
    assert resolve_family("sd-xl") == "sdxl"


def test_zimage_allows_2048_square():
    w, h, warns = clamp_image_dimensions(2048, 2048, "zimage")
    assert (w, h) == (2048, 2048)


def test_zimage_allows_2688x1472_area():
    w, h, warns = clamp_image_dimensions(2688, 1472, "krea2")
    assert w == 2688 and h == 1472
    assert w * h <= 2048 * 2048


def test_zimage_clamps_oversize_area():
    w, h, warns = clamp_image_dimensions(3000, 3000, "zimage")
    assert w * h <= 2048 * 2048
    assert max(w, h) <= 2688
    assert any("exceeds" in m or "scaling" in m for m in warns)


def test_flux_allows_1920x1088():
    w, h, warns = clamp_image_dimensions(1920, 1088, "flux-dev")
    assert (w, h) == (1920, 1088)


def test_flux_allows_1408_square():
    w, h, _ = clamp_image_dimensions(1408, 1408, "flux")
    assert (w, h) == (1408, 1408)


def test_flux_clamps_2048_square():
    w, h, warns = clamp_image_dimensions(2048, 2048, "flux")
    assert w * h <= 2_100_000
    assert max(w, h) <= 1920
    assert warns


def test_sdxl_still_1536():
    max_side, _ = family_limits("sdxl")
    assert max_side == 1536
    w, h, _ = clamp_image_dimensions(2048, 2048, "sd-xl")
    assert max(w, h) <= 1536


def test_validator_flux_clamps_4mp():
    from backend.services.settings_validator import SettingsValidator
    v = SettingsValidator()
    r = v.validate_settings(
        "flux-dev", guidance=3.5, steps=28, width=2048, height=2048, auto_correct=True
    )
    assert "width" in r.corrected_values or any("2" in w or "clamp" in w.lower() or "exceed" in w.lower() for w in r.warnings)
    if "width" in r.corrected_values:
        assert r.corrected_values["width"] * r.corrected_values["height"] <= 2_100_000


def test_validator_zimage_2048_ok():
    from backend.services.settings_validator import SettingsValidator
    v = SettingsValidator()
    r = v.validate_settings(
        "zimage-turbo", guidance=0.0, steps=8, width=2048, height=2048, auto_correct=True
    )
    assert "width" not in r.corrected_values
