"""Tests for image prompt chrome stripping (chat / CLI / batch parity)."""
from backend.services.image_prompt_sanitize import (
    looks_like_image_gen_chrome,
    sanitize_image_prompt,
)


def test_strips_generate_an_image_of():
    assert sanitize_image_prompt("generate an image of the batmobile") == "the batmobile"


def test_strips_please_draw_a_picture_of():
    assert (
        sanitize_image_prompt("please draw a picture of batman on a rooftop")
        == "batman on a rooftop"
    )


def test_strips_slash_imagine():
    assert sanitize_image_prompt("/imagine a sunset over mountains") == "a sunset over mountains"
    assert sanitize_image_prompt("/imagine   cyberpunk alley") == "cyberpunk alley"


def test_strips_make_an_image():
    assert sanitize_image_prompt("make an image of a red sports car") == "a red sports car"


def test_idempotent_on_clean_prompt():
    clean = "batman standing on a gothic rooftop at night, rain, cinematic"
    assert sanitize_image_prompt(clean) == clean
    assert sanitize_image_prompt(sanitize_image_prompt(clean)) == clean


def test_preserves_mid_sentence_image_word():
    # Should not destroy legitimate visual text
    p = "a photo of an image projected on a wall in a dark museum"
    assert sanitize_image_prompt(p) == p


def test_strips_trailing_please():
    assert sanitize_image_prompt("a cat wearing sunglasses, please") == "a cat wearing sunglasses"


def test_outer_quotes():
    assert sanitize_image_prompt('"a lone lighthouse at dusk"') == "a lone lighthouse at dusk"


def test_empty_and_none():
    assert sanitize_image_prompt("") == ""
    assert sanitize_image_prompt("   ") == ""
    assert sanitize_image_prompt(None) == ""
    assert sanitize_image_prompt("generate an image of") == ""


def test_looks_like_chrome():
    assert looks_like_image_gen_chrome("generate an image of x") is True
    assert looks_like_image_gen_chrome("batman on rooftop") is False


def test_create_a_photo_of():
    assert sanitize_image_prompt("create a photo of the joker laughing") == "the joker laughing"


def test_can_you_generate():
    out = sanitize_image_prompt("can you generate an image of a cyber dragon")
    assert out == "a cyber dragon"
    assert not looks_like_image_gen_chrome(out)


def test_slash_resolve_uses_sanitize():
    from backend.services.slash_command_executor import resolve_slash_direct_tool

    tool, params = resolve_slash_direct_tool({
        "slash_command": "imagine",
        "slash_args": "generate an image of the batmobile",
    })
    assert tool == "generate_image"
    assert params["prompt"] == "the batmobile"
