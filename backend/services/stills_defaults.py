"""Shared family defaults for still image generation (chat / CLI / batch).

Callers pass model id (or \"auto\"); when width/height/steps/guidance are None
or left at legacy SD placeholders, resolve from this table so all surfaces
start with the same canvas and sampling policy.

Keep in sync with OfflineImageGenerator._apply_family_sampling — that method
still corrects mid-flight; this module is the *caller-side* source of truth
so UI/API defaults are not SD-era 20/7.5/512 for modern models.
"""
from __future__ import annotations

from typing import Any


# Family sampling + canvas (PoA image gen unification §4).
# Z-Image Turbo: official HF recipe is num_inference_steps=9 (→ 8 DiT forwards),
# guidance_scale=0.0 (CFG distilled out).
_FAMILY_DEFAULTS: dict[str, dict[str, Any]] = {
    "zimage": {"width": 1024, "height": 1024, "steps": 9, "guidance": 0.0},
    "comfyui": {"width": 1024, "height": 1024, "steps": 9, "guidance": 0.0},
    "krea2-turbo": {"width": 1024, "height": 1024, "steps": 8, "guidance": 0.0},
    "krea2-raw": {"width": 1024, "height": 1024, "steps": 52, "guidance": 3.5},
    "sdxl": {"width": 1024, "height": 1024, "steps": 25, "guidance": 7.0},
    "sd": {"width": 512, "height": 512, "steps": 20, "guidance": 7.5},
    "flux": {"width": 1024, "height": 1024, "steps": 28, "guidance": 3.5},
}

# When callers still ship classic SD-era "unset" markers, treat as None so
# family defaults win for modern models.
_LEGACY_SIZE = 512
_LEGACY_STEPS = 20
_LEGACY_GUIDANCE = 7.5


def model_family(model: str | None) -> str:
    """Map catalog key / HF id / auto to a sampling family key."""
    mid = (model or "").strip().lower()
    if mid == "comfyui":
        return "comfyui"
    if not mid or mid == "auto":
        # Product daily driver family for unresolved auto.
        try:
            from backend.services.media_model_registry import resolve_stills_model
            mid = (resolve_stills_model("auto") or "zimage-turbo").strip().lower()
        except Exception:
            mid = "zimage-turbo"
    if "flux" in mid:
        return "flux"
    if "krea" in mid:
        if "raw" in mid:
            return "krea2-raw"
        return "krea2-turbo"
    if "z-image" in mid or "zimage" in mid:
        return "zimage"
    if "xl" in mid or "sdxl" in mid or mid in ("sd-xl", "juggernaut-xl", "realvisxl"):
        return "sdxl"
    return "sd"


def resolve_stills_defaults(
    model: str | None = "auto",
    *,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    replace_legacy_sd_markers: bool = True,
) -> dict[str, Any]:
    """Return resolved {model, family, width, height, steps, guidance}.

    Explicit non-None values are kept unless ``replace_legacy_sd_markers`` is
    True and the value is the classic 512/20/7.5 placeholder (then family wins
    for modern families).
    """
    family = model_family(model)
    base = dict(_FAMILY_DEFAULTS.get(family) or _FAMILY_DEFAULTS["sd"])

    # Classic "unset" form: all three SD-era placeholders together. Intentional
    # draft sizes (e.g. 512² with Turbo steps/CFG) must NOT be rewritten.
    full_legacy_unset = (
        replace_legacy_sd_markers
        and family != "sd"
        and width is not None
        and height is not None
        and int(width) == _LEGACY_SIZE
        and int(height) == _LEGACY_SIZE
        and steps is not None
        and int(steps) == _LEGACY_STEPS
        and guidance is not None
        and abs(float(guidance) - _LEGACY_GUIDANCE) < 1e-6
    )

    def _pick_size(val: int | None, key: str) -> int:
        if val is None:
            return int(base[key])
        if full_legacy_unset and int(val) == _LEGACY_SIZE:
            return int(base[key])
        return int(val)

    def _pick_steps(val: int | None) -> int:
        if val is None:
            return int(base["steps"])
        if replace_legacy_sd_markers and int(val) == _LEGACY_STEPS and family != "sd":
            return int(base["steps"])
        return int(val)

    def _pick_guidance(val: float | None) -> float:
        if val is None:
            return float(base["guidance"])
        if (
            replace_legacy_sd_markers
            and abs(float(val) - _LEGACY_GUIDANCE) < 1e-6
            and family != "sd"
        ):
            return float(base["guidance"])
        return float(val)

    resolved_model = (model or "auto").strip() or "auto"
    return {
        "model": resolved_model,
        "family": family,
        "width": _pick_size(width, "width"),
        "height": _pick_size(height, "height"),
        "steps": _pick_steps(steps),
        "guidance": _pick_guidance(guidance),
    }


def family_quality_presets(model: str | None = "auto") -> list[dict[str, Any]]:
    """UI quality presets keyed by family (not universal SD 15/20/30)."""
    family = model_family(model)
    if family == "zimage":
        return [
            {"value": "fast", "label": "Fast", "steps": 6, "guidance": 0.0},
            {"value": "standard", "label": "Standard", "steps": 9, "guidance": 0.0},
            {"value": "high", "label": "High Quality", "steps": 9, "guidance": 0.0},
        ]
    if family == "krea2-turbo":
        return [
            {"value": "fast", "label": "Fast", "steps": 6, "guidance": 0.0},
            {"value": "standard", "label": "Standard", "steps": 8, "guidance": 0.0},
            {"value": "high", "label": "High Quality", "steps": 12, "guidance": 0.0},
        ]
    if family == "krea2-raw":
        return [
            {"value": "standard", "label": "Standard", "steps": 40, "guidance": 3.5},
            {"value": "high", "label": "High Quality", "steps": 52, "guidance": 3.5},
            {"value": "ultra", "label": "Ultra", "steps": 60, "guidance": 3.5},
        ]
    if family == "flux":
        return [
            {"value": "flux-fast", "label": "FLUX Fast", "steps": 16, "guidance": 3.0},
            {"value": "flux-quality", "label": "FLUX Quality", "steps": 28, "guidance": 3.5},
            {"value": "flux-ultra", "label": "FLUX Ultra", "steps": 40, "guidance": 4.0},
        ]
    if family == "sdxl":
        return [
            {"value": "fast", "label": "Fast", "steps": 20, "guidance": 6.0},
            {"value": "standard", "label": "Standard", "steps": 25, "guidance": 7.0},
            {"value": "high", "label": "High Quality", "steps": 35, "guidance": 7.5},
        ]
    return [
        {"value": "fast", "label": "Fast", "steps": 15, "guidance": 7.0},
        {"value": "standard", "label": "Standard", "steps": 20, "guidance": 7.5},
        {"value": "high", "label": "High Quality", "steps": 30, "guidance": 8.0},
    ]
