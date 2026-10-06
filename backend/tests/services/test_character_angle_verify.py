"""Cast sheet angle verify — normalize, match, strengthen, classify (mocked)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
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


def test_normalize_angle_prefers_the_longest_label():
    assert normalize_angle("full-body three-quarter left") == "full-body three-quarter"
    assert normalize_angle("full body three-quarter left") == "full-body three-quarter"
    assert normalize_angle("full body, front-facing") == "full-body front"
    assert normalize_angle("Three-Quarter Left.") == "three-quarter left"
    assert normalize_angle("a front view") == "face-forward"


def test_angles_match_unknown_label_is_not_a_match():
    assert angles_match("profile right", "profile right") is True
    assert angles_match("profile right", "full-body front") is False
    assert angles_match("profile right", None) is None
    assert angles_match("profile right", "gibberish xyz") is None
    assert angles_match(None, "profile right") is None
    assert angles_match("", "profile right") is None


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


def _vision_reply(description="", *, success=True, error=None):
    res = MagicMock()
    res.success = success
    res.description = description
    res.model_used = "gemma4:e4b"
    res.error = error
    return res


def test_verify_sample_angle_equal_labels_match(tmp_path):
    img = _tiny_png(tmp_path / "eq.png")
    az = MagicMock()
    az.analyze.return_value = _vision_reply("profile right")

    v = verify_sample_angle(str(img), "profile right", analyzer=az)
    assert v["ok"] is True
    assert v["match"] is True


def test_verify_sample_angle_analyzer_failure_is_unverified(tmp_path):
    img = _tiny_png(tmp_path / "fail.png")
    az = MagicMock()
    az.analyze.return_value = _vision_reply(success=False, error="vision model unavailable")

    v = verify_sample_angle(str(img), "profile right", analyzer=az)
    assert v["ok"] is False
    assert v["match"] is None


def test_verify_sample_angle_without_a_reading_is_unverified(tmp_path):
    img = _tiny_png(tmp_path / "z.png")
    az = MagicMock()

    az.analyze.return_value = _vision_reply("I cannot tell from this picture")
    assert verify_sample_angle(str(img), "profile right", analyzer=az)["match"] is None

    assert verify_sample_angle(str(tmp_path / "missing.png"), "profile right", analyzer=az)["match"] is None

    az.analyze.side_effect = TimeoutError("vision timed out")
    assert verify_sample_angle(str(img), "profile right", analyzer=az)["match"] is None


def _planned_row(angle="profile right"):
    return SimpleNamespace(index=3, angle=angle, framing="close-up",
                           image_prompt="tok in an alley", seed=1, angle_state=None)


def _verify_row(cg, row, img, az):
    return cg._verify_angle_relabel_regen(
        row=row, subject=SimpleNamespace(name="Tok"), output_path=str(img),
        route={}, loras=[], use_lora=False, analyzer=az,
    )


def test_an_unchecked_angle_keeps_the_plan_and_skips_regen(tmp_path, monkeypatch):
    import backend.tasks.character_generation_tasks as cg
    img = _tiny_png(tmp_path / "s.png")
    render = MagicMock()
    log = MagicMock()
    monkeypatch.setattr(cg, "_render_cast_still", render)
    monkeypatch.setattr(cg, "log", log)
    az = MagicMock()
    az.analyze.return_value = _vision_reply(success=False, error="vision model unavailable")
    row = _planned_row()

    out = _verify_row(cg, row, img, az)

    render.assert_not_called()
    assert out["match"] is None
    assert out["regenerated"] is False
    assert row.angle == "profile right"
    assert row.framing == "close-up"
    assert row.angle_state == "unverified"
    assert any("angle unverified" in c.args[0] for c in log.warning.call_args_list)


def test_a_regen_that_cannot_be_checked_is_unverified(tmp_path, monkeypatch):
    import backend.tasks.character_generation_tasks as cg
    img = _tiny_png(tmp_path / "r.png")
    render = MagicMock()
    monkeypatch.setattr(cg, "_render_cast_still", render)
    monkeypatch.setattr(cg, "_aspect_for_row", lambda row: (1024, 1024))
    az = MagicMock()
    az.analyze.side_effect = [
        _vision_reply("full-body front"),
        _vision_reply(success=False, error="vision timed out"),
    ]
    row = _planned_row()

    out = _verify_row(cg, row, img, az)

    render.assert_called_once()
    assert out["regenerated"] is True
    assert out["match"] is None
    assert out["observed"] is None
    assert row.angle == "profile right"
    assert row.angle_state == "unverified"


def test_an_unplanned_angle_takes_the_observed_label_as_verified(tmp_path, monkeypatch):
    import backend.tasks.character_generation_tasks as cg
    img = _tiny_png(tmp_path / "u.png")
    render = MagicMock()
    monkeypatch.setattr(cg, "_render_cast_still", render)
    az = MagicMock()
    az.analyze.return_value = _vision_reply("profile left")
    row = _planned_row(angle="")

    out = _verify_row(cg, row, img, az)

    render.assert_not_called()
    assert out["observed"] == "profile left"
    assert row.angle == "profile left"
    assert row.framing == "close-up"
    assert row.angle_state == "verified"


def test_a_checked_angle_is_verified(tmp_path):
    import backend.tasks.character_generation_tasks as cg
    img = _tiny_png(tmp_path / "v.png")
    az = MagicMock()
    az.analyze.return_value = _vision_reply("profile right")
    row = _planned_row()

    out = _verify_row(cg, row, img, az)

    assert out["match"] is True
    assert row.angle == "profile right"
    assert row.angle_state == "verified"
