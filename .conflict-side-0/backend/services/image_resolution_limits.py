"""Family-aware stills resolution limits (Z-Image / Krea / Flux / SDXL).

Used by offline_image_generator, settings_validator, and batch Flux path so UI
2K presets are not silently truncated at the old 1536 clamp.

Limits are model-family ceilings, not GPU guarantees — 2K on 16GB may OOM.
"""
from __future__ import annotations

from typing import List, Tuple

# Z-Image: official ~512–2048 side / ~2048² area. Allow long-side to 2688 so
# Krea-style 16:9 2K packs (2688×1472 ≈ 3.95 MP) fit under 2048² area.
_DIT_MAX_SIDE = 2688
_DIT_MAX_PIXELS = 2048 * 2048  # 4_194_304

# FLUX.1-dev: ~0.1–2.0 MP design range (not 2048²).
_FLUX_MAX_SIDE = 1920
_FLUX_MAX_PIXELS = 2_100_000

_SDXL_MAX_SIDE = 1536
_SD_MAX_SIDE = 768
_MIN_SIDE = 256


def resolve_family(model_or_family: str | None) -> str:
    """Normalize catalog key / family string to a limit family."""
    k = (model_or_family or "").strip().lower()
    if not k:
        return "sd"
    if k in ("zimage", "zimage-turbo", "z-image-turbo") or "zimage" in k or "z-image" in k:
        return "zimage"
    if k.startswith("krea") or "krea2" in k or "krea-2" in k:
        return "krea2"
    if k.startswith("flux") or "flux" in k:
        return "flux"
    if "xl" in k or "sdxl" in k or k in ("sd-xl", "sdxl-turbo", "sdxl-legacy"):
        return "sdxl"
    return "sd"


def family_limits(family: str) -> Tuple[int, int]:
    """Return (max_side, max_pixels) for a family."""
    fam = resolve_family(family)
    if fam in ("zimage", "krea2"):
        return _DIT_MAX_SIDE, _DIT_MAX_PIXELS
    if fam == "flux":
        return _FLUX_MAX_SIDE, _FLUX_MAX_PIXELS
    if fam == "sdxl":
        return _SDXL_MAX_SIDE, _SDXL_MAX_SIDE * _SDXL_MAX_SIDE
    return _SD_MAX_SIDE, _SD_MAX_SIDE * _SD_MAX_SIDE


def _snap16(n: int) -> int:
    n = max(_MIN_SIDE, int(n))
    return max(_MIN_SIDE, (n // 16) * 16)


def clamp_image_dimensions(
    width: int,
    height: int,
    family: str | None,
) -> Tuple[int, int, List[str]]:
    """Clamp W×H to family max side + max area. Returns (w, h, warnings).

    Preserves aspect ratio when shrinking for max_pixels.
    """
    warnings: List[str] = []
    fam = resolve_family(family)
    max_side, max_pixels = family_limits(fam)

    w = max(_MIN_SIDE, int(width or _MIN_SIDE))
    h = max(_MIN_SIDE, int(height or _MIN_SIDE))

    if w > max_side or h > max_side:
        warnings.append(
            f"{fam}: side {w}x{h} exceeds max side {max_side}; clamping sides"
        )
        w = min(w, max_side)
        h = min(h, max_side)

    pixels = w * h
    if pixels > max_pixels:
        scale = (max_pixels / float(pixels)) ** 0.5
        nw = max(_MIN_SIDE, int(w * scale))
        nh = max(_MIN_SIDE, int(h * scale))
        warnings.append(
            f"{fam}: {w}x{h} ({pixels} px) exceeds max area {max_pixels}; "
            f"scaling to {nw}x{nh}"
        )
        w, h = nw, nh

    w, h = _snap16(w), _snap16(h)
    # Re-check area after snap (snap can grow slightly)
    while w * h > max_pixels and (w > _MIN_SIDE or h > _MIN_SIDE):
        if w >= h and w > _MIN_SIDE:
            w = max(_MIN_SIDE, w - 16)
        elif h > _MIN_SIDE:
            h = max(_MIN_SIDE, h - 16)
        else:
            break

    if w * h > 1024 * 1024 and fam in ("zimage", "krea2", "flux"):
        warnings.append(
            f"{fam}: {w}x{h} is >1MP — higher VRAM use; may OOM on 16GB cards"
        )

    return w, h, warnings
