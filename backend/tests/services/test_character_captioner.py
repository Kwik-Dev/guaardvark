"""ensure_subject_image_captions keeps captions that anchor on the subject's own class."""
from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from backend.services.character_captioner import ensure_subject_image_captions


class _StubAnalyzer:
    """Stands in for VisionAnalyzer; counts calls so a skip is observable."""

    def __init__(self, description="full body, standing in snow, soft daylight"):
        self.description = description
        self.calls = 0

    def analyze(self, img, prompt, think=False, **kw):
        self.calls += 1
        return SimpleNamespace(success=True, description=self.description)


def _image(tmp_path, name="ref_01.png"):
    path = tmp_path / name
    Image.new("RGB", (8, 8), (200, 200, 200)).save(path)
    return path


def test_creature_caption_with_its_class_is_not_recaptioned(tmp_path):
    img = _image(tmp_path)
    caption = "a photo of trig, white wolf, full body, standing in snow, amber eyes"
    img.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")
    analyzer = _StubAnalyzer()

    out = ensure_subject_image_captions(
        [str(img)], trigger="trig", class_token="wolf", analyzer=analyzer,
    )

    assert analyzer.calls == 0
    assert out["skipped"] == 1 and out["written"] == 0
    assert img.with_suffix(".txt").read_text(encoding="utf-8").strip() == caption


def test_hand_edited_creature_caption_survives_a_second_call(tmp_path):
    img = _image(tmp_path)
    analyzer = _StubAnalyzer()

    first = ensure_subject_image_captions(
        [str(img)], trigger="trig", class_token="white wolf", analyzer=analyzer,
    )
    assert first["written"] == 1
    written = img.with_suffix(".txt").read_text(encoding="utf-8").strip()
    assert written.startswith("a photo of trig, white wolf")

    edited = "a photo of trig, white wolf, full body, howling at a pale moon, hand edited"
    img.with_suffix(".txt").write_text(edited + "\n", encoding="utf-8")

    second = ensure_subject_image_captions(
        [str(img)], trigger="trig", class_token="white wolf", analyzer=analyzer,
    )

    assert analyzer.calls == 1  # only the first call captioned
    assert second["skipped"] == 1 and second["written"] == 0
    assert img.with_suffix(".txt").read_text(encoding="utf-8").strip() == edited


def test_creature_caption_anchored_on_a_human_word_is_recaptioned(tmp_path):
    img = _image(tmp_path)
    img.with_suffix(".txt").write_text(
        "a photo of trig, person, full body, standing in snow\n", encoding="utf-8",
    )
    analyzer = _StubAnalyzer()

    out = ensure_subject_image_captions(
        [str(img)], trigger="trig", class_token="white wolf", analyzer=analyzer,
    )

    assert out["written"] == 1
    assert "white wolf" in img.with_suffix(".txt").read_text(encoding="utf-8")


def test_human_subject_accepts_any_human_class_word(tmp_path):
    img = _image(tmp_path)
    caption = "a photo of alex, man, upper body, seated at a desk, window light"
    img.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")
    analyzer = _StubAnalyzer()

    out = ensure_subject_image_captions(
        [str(img)], trigger="alex", class_token="person", analyzer=analyzer,
    )

    assert analyzer.calls == 0
    assert out["skipped"] == 1
