"""Tests for shared stills enhance / negative policy."""
from backend.services.stills_policy import (
    BASE_QUALITY_NEGATIVE,
    resolve_enhance_mode,
    resolve_stills_negative,
)


def test_verbatim_forces_none(monkeypatch):
    monkeypatch.setattr(
        "backend.services.media_director.verbatim_prompts_enabled",
        lambda: True,
    )
    assert resolve_enhance_mode(director=True, auto_enhance=True) == "none"
    assert resolve_enhance_mode(enhance="offline") == "none"


def test_director_opt_in(monkeypatch):
    monkeypatch.setattr(
        "backend.services.media_director.verbatim_prompts_enabled",
        lambda: False,
    )
    assert resolve_enhance_mode(director=True) == "director"
    assert resolve_enhance_mode(enhance="director") == "director"
    assert resolve_enhance_mode() == "offline"  # default ladder


def test_auto_enhance_false_is_none(monkeypatch):
    monkeypatch.setattr(
        "backend.services.media_director.verbatim_prompts_enabled",
        lambda: False,
    )
    assert resolve_enhance_mode(auto_enhance=False) == "none"


def test_negative_verbatim_user_only():
    assert resolve_stills_negative("ugly hands", enhance_mode="none") == "ugly hands"
    assert resolve_stills_negative("", enhance_mode="none") == ""


def test_negative_offline_includes_base():
    neg = resolve_stills_negative("", enhance_mode="offline")
    assert "blurry" in neg or "low quality" in neg
    assert BASE_QUALITY_NEGATIVE.split(",")[0].strip() in neg or "blurry" in neg


def test_negative_user_plus_base():
    neg = resolve_stills_negative("watermark", enhance_mode="offline")
    assert "watermark" in neg
    assert "blurry" in neg or "low quality" in neg
