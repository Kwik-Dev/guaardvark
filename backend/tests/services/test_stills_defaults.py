"""Unit tests for shared stills family defaults (chat / batch / CLI parity)."""
from backend.services.stills_defaults import (
    model_family,
    resolve_stills_defaults,
)


def test_zimage_family_defaults():
    d = resolve_stills_defaults("zimage-turbo")
    assert d["family"] == "zimage"
    assert d["width"] == 1024 and d["height"] == 1024
    assert d["steps"] == 8
    assert d["guidance"] == 1.0


def test_legacy_sd_markers_replaced_for_modern():
    d = resolve_stills_defaults(
        "zimage-turbo",
        width=512,
        height=512,
        steps=20,
        guidance=7.5,
        replace_legacy_sd_markers=True,
    )
    assert d["width"] == 1024
    assert d["steps"] == 8
    assert d["guidance"] == 1.0


def test_explicit_overrides_kept_when_not_legacy():
    d = resolve_stills_defaults(
        "zimage-turbo",
        width=832,
        height=1216,
        steps=12,
        guidance=1.0,
        replace_legacy_sd_markers=True,
    )
    assert d["width"] == 832
    assert d["height"] == 1216
    assert d["steps"] == 12


def test_classic_sd_keeps_512():
    d = resolve_stills_defaults("sd-1.5", width=512, height=512, steps=20, guidance=7.5)
    assert d["family"] == "sd"
    assert d["width"] == 512
    assert d["steps"] == 20
    assert d["guidance"] == 7.5


def test_krea_raw_vs_turbo():
    assert model_family("krea2-raw") == "krea2-raw"
    assert model_family("krea2-turbo") == "krea2-turbo"
    raw = resolve_stills_defaults("krea2-raw")
    turbo = resolve_stills_defaults("krea2-turbo")
    assert raw["steps"] == 52 and raw["guidance"] == 3.5
    assert turbo["steps"] == 8 and turbo["guidance"] == 0.0


def test_flux_and_sdxl():
    flux = resolve_stills_defaults("flux-dev")
    assert flux["family"] == "flux"
    assert flux["steps"] == 28
    sdxl = resolve_stills_defaults("sd-xl")
    assert sdxl["family"] == "sdxl"
    assert sdxl["width"] == 1024
    assert sdxl["steps"] == 25


def test_csv_form_merge_via_parse(tmp_path=None):
    from backend.services.batch_image_generator import BatchImageGenerator
    gen = BatchImageGenerator()
    csv = "prompt,width\nhero on rooftop,\n"
    rows = gen._parse_csv_prompts(
        csv,
        form_model="zimage-turbo",
        form_width=None,
        form_height=None,
        form_steps=None,
        form_guidance=None,
    )
    assert len(rows) == 1
    assert rows[0].model == "zimage-turbo"
    assert rows[0].width == 1024
    assert rows[0].steps == 8
    assert rows[0].guidance == 1.0
