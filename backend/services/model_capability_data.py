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
FAMILY_COORD_DEFAULTS = {
    "gemma4": {
        "order": "yx", "grid": 1000, "normalised": True, "confidence": 0.95,
        "note": ("Google box_2d, [y1,x1,y2,x2] normalised to 1000. Matches the "
                 "measured, shipping gemma4:e4b row and Google's own published "
                 "notebook. Two years of this project's servo data sit on it."),
    },
    "qwen35": {
        "order": "xy", "grid": 1000, "normalised": True, "confidence": 0.9,
        "note": ("[x1,y1,x2,y2] normalised to 1000 — the OPPOSITE of Google's order. "
                 "Probed on qwen3.5:9b 2026-09-22: read as xy, median error 15px; "
                 "read as yx, 345px. This is why an untested model looks like a bad "
                 "model. Anyone who had tried qwen3.5 as the agent's eye before this "
                 "would have measured 345px, concluded it could not point, and moved "
                 "on — when in fact it out-points the model this system is hardwired "
                 "to by a factor of four."),
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
