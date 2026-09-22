"""Declared facts about models, kept apart from the logic that reads them.

Everything here is data. The resolver in ``model_capability_resolver`` decides
how to combine it; nothing in this file decides anything. Keeping the two apart
is what makes "declare the limit in data, next to the thing it constrains"
structurally true rather than aspirational — a new model family is a row here,
not a branch in a call site.

Nothing in this file is consulted while Ollama can answer for itself. Ollama's
``/api/show`` knows what a model can do; these tables cover the two things it
cannot tell us (what coordinate convention a vision model emits) and the one
case where it is unavailable (Ollama down).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Vision, by name — the LAST resort
# ---------------------------------------------------------------------------
# Used only when Ollama cannot be reached. Name inference is wrong often enough
# to be dangerous: this box has `VladimirGav/gemma4-26b-16GB-VRAM-Uncensored`,
# which every name-based rule in the codebase called a vision model and which
# has no vision tower at all — sending it an image earns an Ollama 400. It also
# has `qwen3.5:9b` and `ornith-1.5:9b`, both genuinely multimodal and both
# missed by the older pattern lists. So: a hint for a degraded mode, never a
# source of truth.
VISION_NAME_FALLBACK = (
    "gemma4", "gemma-4", "qwen3-vl", "qwen2.5vl", "qwen2-vl", "qwen3.5", "qwen3.6",
    "llava", "bakllava", "moondream", "minicpm-v", "llama3.2-vision", "granite-vision",
    "pixtral", "molmo", "cogvlm", "internvl", "phi-vision", "deepseek-vl", "ministral-3",
    "devstral", "muse-glimmer", "ornith",
)

# ---------------------------------------------------------------------------
# Coordinate conventions, by architecture family
# ---------------------------------------------------------------------------
# `order`      "yx" for Google-style box_2d [y1,x1,y2,x2], "xy" for [x1,y1,x2,y2]
# `grid`       the value coordinates are normalised to, or None for raw pixels
# `confidence` how much we trust it; anything below 0.9 wants a probe run
#
# Only families actually measured on this project appear here. A family that is
# absent is not assumed — it falls through to the prompt contract (see the
# resolver) with low confidence, which is honest about being a guess and is
# still strictly better than the silent "xy" default it replaces.
# ---------------------------------------------------------------------------
# Request dialects
# ---------------------------------------------------------------------------
# A convention is not only how to READ a model's numbers; it is how to ASK.
# ministral-3:14b answers "[]" (not visible) to the Google box_2d request and
# answers a plain point request at ~140px. A model can look unable to point
# when it has merely been asked in a foreign dialect. Each style here is the
# exact prompt the servo sends, the shape it expects back, and whether the
# numbers come normalised to a grid or in raw pixels of the image sent.
#
# google_box2d's prompt is the servo's historical string VERBATIM. gemma4:e4b's
# path must stay byte-identical, and a test pins that.
COORD_STYLES = {
    "google_box2d": {
        "shape": "box", "grid": 1000, "orders": ("yx", "xy"),
        "prompt": (
            "Detect the {target}. Reply with ONLY a JSON list "
            '[{{"box_2d": [y1, x1, y2, x2], "label": "{target}"}}] '
            "with coordinates normalized to 1000. If the target is not visible, "
            "reply with an empty list []."
        ),
    },
    "qwen_bbox2d_abs": {
        "shape": "box", "grid": None, "orders": ("xy", "yx"),
        "prompt": (
            "Locate the {target} in the image. Reply with ONLY a JSON list "
            '[{{"bbox_2d": [x1, y1, x2, y2], "label": "{target}"}}] '
            "using absolute pixel coordinates of this image. If the target is not "
            "visible, reply with an empty list []."
        ),
    },
    "point_xy_abs": {
        "shape": "point", "grid": None, "orders": ("xy",),
        "prompt": (
            "Where is the {target}? Reply with ONLY JSON "
            '{{"x": <pixels from the left edge>, "y": <pixels from the top edge>}} '
            "giving the centre of the {target} in absolute pixels of this image. "
            "If it is not visible, reply with {{}}."
        ),
    },
    "point_xy_norm": {
        "shape": "point", "grid": 1000, "orders": ("xy",),
        "prompt": (
            "Where is the {target}? Reply with ONLY JSON "
            '{{"x": <0-1000>, "y": <0-1000>}} giving the centre of the {target} '
            "normalized to a 1000x1000 grid, x from the left edge, y from the top. "
            "If it is not visible, reply with {{}}."
        ),
    },
}
DEFAULT_STYLE = "google_box2d"


def style_overlay(style: str, order: str) -> dict:
    """A vision_config overlay that pins one dialect and one axis order."""
    st = COORD_STYLES[style]
    return {"coord_style": style, "coord_order": order,
            "internal_width": st["grid"] or 0}


# ---------------------------------------------------------------------------
# Coordinate conventions, by architecture family
# ---------------------------------------------------------------------------
# `style`      which request dialect (COORD_STYLES key)
# `order`      "yx" for Google-style box_2d [y1,x1,y2,x2], "xy" otherwise
# `grid`       normalisation denominator, None for raw pixels
# `confidence` below MEASURED_CONFIDENCE the resolver treats it as a guess
# `min_num_predict`     token budget the anchor call needs to finish answering
# `think_uncontrollable` Ollama's think:false is ignored for this family, so the
#                        budget must cover the thinking too (declared limit, in
#                        data, next to the family it constrains)
#
# Only families measured on this project appear. An absent family falls through
# to the prompt contract with low confidence, which is honest about being a
# guess and still better than the silent "xy" it replaced.
FAMILY_COORD_DEFAULTS = {
    "gemma4": {
        "style": "google_box2d", "order": "yx", "grid": 1000, "normalised": True,
        "confidence": 0.95, "min_num_predict": 128,
        "note": ("Google box_2d, [y1,x1,y2,x2] normalised to 1000. Matches the "
                 "measured, shipping gemma4:e4b row and Google's own published "
                 "notebook. Two years of this project's servo data sit on it."),
    },
    "qwen35": {
        "style": "google_box2d", "order": "xy", "grid": 1000, "normalised": True,
        "confidence": 0.9, "min_num_predict": 128,
        "note": ("Answers the Google-style request but in [x1,y1,x2,y2] order. "
                 "Probed on qwen3.5:9b 2026-09-22: read as xy, median error 15px; "
                 "read as yx, 345px. This is why an untested model looks like a bad "
                 "model — and why qwen3.5:9b, which out-points gemma4:e4b by a "
                 "factor of four, went unfound."),
    },
    "qwen3vl": {
        "style": "qwen_bbox2d_abs", "order": "xy", "grid": None, "normalised": False,
        "confidence": 0.5, "min_num_predict": 1024, "think_uncontrollable": True,
        "note": ("Native dialect is bbox_2d in absolute pixels. The only installed tag "
                 "(qwen3-vl:8b-thinking-q8_0) ignores think:false and spends the whole "
                 "budget thinking: done_reason=length at 256/512/1024 (2026-09-22). "
                 "Half its answers truncate and the rest miss by hundreds of pixels. "
                 "Confidence stays below the measured bar until a non-thinking tag is "
                 "probed."),
    },
}

# ---------------------------------------------------------------------------
# Models that Ollama does not serve
# ---------------------------------------------------------------------------
# An MCP client driving the screen, or a hosted model, has no /api/show. Declare
# it here or the resolver treats it as unknown and refuses to click, which is
# the correct default for something we know nothing about.
EXTERNAL_MODEL_ROWS: dict = {}


def name_looks_vision(tag: str) -> bool:
    """Degraded-mode guess. See VISION_NAME_FALLBACK for why this is last."""
    if not tag:
        return False
    short = tag.split("/")[-1].lower()
    return any(m in short for m in VISION_NAME_FALLBACK)
