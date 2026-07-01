"""Normalize per-subject LoRA training hyperparameters."""
from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "resolution": 768,
    "rank": 16,
    "alpha": 16,
    "learning_rate": 1.0e-4,
    "steps": None,  # computed from image count when None
}


def normalize_training_settings(raw: dict | None) -> dict[str, Any]:
    src = dict(DEFAULTS)
    if isinstance(raw, dict):
        src.update({k: v for k, v in raw.items() if v is not None})
    resolution = int(src.get("resolution") or 768)
    resolution = max(512, (resolution // 64) * 64)
    rank = max(4, min(64, int(src.get("rank") or 16)))
    alpha = max(4, min(128, int(src.get("alpha") or rank)))
    lr = float(src.get("learning_rate") or 1.0e-4)
    steps = src.get("steps")
    steps = int(steps) if steps not in (None, "") else None
    return {
        "resolution": resolution,
        "rank": rank,
        "alpha": alpha,
        "learning_rate": lr,
        "steps": steps,
    }


def settings_for_subject(subject) -> dict[str, Any]:
    raw = getattr(subject, "training_settings_json", None) or {}
    return normalize_training_settings(raw if isinstance(raw, dict) else None)