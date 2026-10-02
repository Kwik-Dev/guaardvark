"""Normalize per-subject LoRA training hyperparameters."""
from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    # 512 is the 16GB-safe default for Z-Image train_loop.
    "resolution": 512,
    "rank": 16,
    "alpha": 16,
    "learning_rate": 1.0e-4,
    "steps": None,  # computed from image count when None
    # base_model_id filled from media_model_registry default when missing
    "base_model_id": None,
}


def normalize_training_settings(raw: dict | None) -> dict[str, Any]:
    src = dict(DEFAULTS)
    if isinstance(raw, dict):
        src.update({k: v for k, v in raw.items() if v is not None})
    resolution = int(src.get("resolution") or 512)
    resolution = max(512, (resolution // 64) * 64)
    rank = max(4, min(64, int(src.get("rank") or 16)))
    alpha = max(4, min(128, int(src.get("alpha") or rank)))
    lr = float(src.get("learning_rate") or 1.0e-4)
    steps = src.get("steps")
    steps = int(steps) if steps not in (None, "") else None

    base_model_id = src.get("base_model_id") or src.get("base_model")
    try:
        from backend.services.media_model_registry import (
            get_cast_train_base_setting,
            get_profile,
        )
        if base_model_id:
            p = get_profile(str(base_model_id))
            base_model_id = p["id"] if p else get_cast_train_base_setting()
        else:
            base_model_id = get_cast_train_base_setting()
    except Exception:
        base_model_id = base_model_id or "zimage-turbo"

    return {
        "resolution": resolution,
        "rank": rank,
        "alpha": alpha,
        "learning_rate": lr,
        "steps": steps,
        "base_model_id": base_model_id,
    }


def merge_training_settings(existing: dict | None, raw: dict | None) -> dict[str, Any]:
    """Return a subject's stored settings with the hyperparameters from ``raw``.

    ``Subject.training_settings_json`` holds more than hyperparameters: the
    identity state (bible_vision_grounded, bible_manual_override,
    bible_vision_tags, bible_identity_marks, class_token) and the post-train
    smoke_identity score live in the same object. A hyperparameter write
    replaces the six keys normalize_training_settings returns, exactly as
    before, and leaves every other key as it was.
    """
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(normalize_training_settings(raw))
    return merged


def settings_for_subject(subject) -> dict[str, Any]:
    raw = getattr(subject, "training_settings_json", None) or {}
    return normalize_training_settings(raw if isinstance(raw, dict) else None)