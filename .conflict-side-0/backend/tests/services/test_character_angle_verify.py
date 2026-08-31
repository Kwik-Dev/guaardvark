"""Cast sheet angle verify — normalize, match, strengthen, classify (mocked)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.services.character_angle_verify import (
    angles_match,
    apply_relabel,
    classify_image_angle,
    framing_for_angle,
    normalize_angle,
    strengthen_prompt_for_angle,
    verify_sample_angle,
)


def test_normalize_angle_aliases():
    assert normalize_angle("profile right") == "profile right"
    assert normalize_angle("right profile") == "profile right"
    assert normalize_angle("Full Body front") == "full-body front"
    assert normalize_angle("3/4 left") == "three-quarter left"
    assert normalize_angle("headshot") == "face-forward"


def test_angles_match_and_unknown_observed_skips_regen():
    assert angles_match("profile right", "profile right") is True
    assert angles_match("profile right", "full-body front") is False
    assert angles_match("profile right", None) is True  # vision fail → don't regen
    assert angles_match("profile right", "gibberish xyz") is True


def test_strengthen_prompt_leads_with_framing():
    p = strengthen_prompt_for_angle("batman in alley", "profile right")
    assert p.lower().startswith("strict right profile")
    assert "batman" in p.lower()


def test_framing_for_angle():
    assert framing_for_angle("full-body front") == "full-body"
    assert framing_for_angle("profile left") == "close-up"


def _tiny_png(path: Path) -> Path:
    from PIL import Image
    Image.new("RGB", (8, 8), color=(20, 40, 60)).save(path)
    return path


def test_classify_parses_vision_reply(tmp_path):
    img = _tiny_png(tmp_path / "x.png")

    az = MagicMock()
    res = MagicMock()
    res.success = True
    res.description = "full-body front\n"
    res.model_used = "gemma4:e4b"
    res.error = None
    az.analyze.return_value = res

    out = classify_image_angle(str(img), analyzer=az)

    assert out["ok"] is True
    assert out["angle"] == "full-body front"
    az.analyze.assert_called_once()


def test_verify_sample_angle_mismatch(tmp_path):
    img = _tiny_png(tmp_path / "y.png")
    az = MagicMock()
    res = MagicMock()
    res.success = True
    res.description = "full-body front"
    res.model_used = "gemma4:e4b"
    az.analyze.return_value = res

    v = verify_sample_angle(str(img), "profile right", analyzer=az)
    assert v["ok"] is True
    assert v["match"] is False
    assert v["observed"] == "full-body front"


def test_apply_relabel():
    class S:
        angle = "profile right"
        framing = "close-up"

    s = S()
    apply_relabel(s, "full-body front")
    assert s.angle == "full-body front"
    assert s.framing == "full-body"
