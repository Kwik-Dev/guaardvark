"""One place that answers what a given model can do.

The problem this exists to end: the same question — "does this model have eyes?"
— was being answered independently in six places that disagreed with each other
and, more importantly, with Ollama. On this box that produced two live faults at
once. A model whose name contains "gemma4" but which has no vision tower was
being handed images, earning an Ollama 400. Two genuinely multimodal models were
being sent through a describe-then-inject detour built for blind models, quietly
downgrading a model that could see for itself.

The fix is not a better pattern list. It is to stop guessing: Ollama's
``/api/show`` reports a ``capabilities`` array, and it is right. Note that
``/api/tags`` is NOT a substitute — measured 2026-09-22, tags omits ``vision``
for every ``gemma4`` tag on this machine while show reports it.

One thing ``/api/show`` cannot tell us is what coordinate convention a vision
model emits when asked to point at something, so that part is declared data with
a measured-or-refuse policy. See ``coords_for``.

Importable from Flask, Celery and the MCP server alike: no app context, no
``backend.app`` import, no Flask.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.services.model_capability_data import (
    EXTERNAL_MODEL_ROWS,
    FAMILY_COORD_DEFAULTS,
    name_looks_vision,
)

logger = logging.getLogger(__name__)

SURFACES = ("agent_screen", "chat", "mcp", "ambient")

# Probed conventions land here. Machine-local and gitignored, like the servo
# calibration it sits beside — a convention is a measurement about a model on a
# machine, not a fact about the source tree.
PROBE_STORE = Path(__file__).resolve().parents[2] / "data" / "training" / "model_coord_probe.json"

# Below this, a convention is a guess rather than a measurement, and the model
# is reported as unable to drive the screen until someone probes it.
MEASURED_CONFIDENCE = 0.7

_CACHE_TTL = 60.0
_cache: Dict[Tuple[str, str], Tuple[float, "ModelProfile"]] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoordConvention:
    """How to read the numbers a model gives back when asked to point.

    ``order is None`` means we do not know, and the servo must refuse rather
    than guess. Guessing here does not fail loudly; it puts the click somewhere
    plausible and wrong, which is the worst outcome available.
    """
    order: Optional[str]          # "xy" | "yx" | None
    grid: Optional[int]           # normalisation denominator, None = raw pixels
    normalised: bool
    source: str                   # row | probe | family | prompt_contract | unknown
    confidence: float


@dataclass(frozen=True)
class Eyes:
    """Who looks at the screen for this model."""
    model: Optional[str]          # None => the model sees for itself
    mechanism: str                # native | sibling_vlm | none
    reason: str                   # plain English, for the UI and the logs


@dataclass(frozen=True)
class ModelProfile:
    tag: str
    exists: bool
    sees_natively: bool
    supports_tools: bool
    supports_thinking: bool
    context_window: int
    size_mb: float
    architecture: str
    eyes: Eyes
    coords: CoordConvention
    can_drive_screen: bool
    blockers: Tuple[str, ...]
    evidence: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------

def _info(tag: str) -> Optional[dict]:
    try:
        from backend.utils.ollama_resource_manager import get_model_info
        return get_model_info(tag)
    except Exception as e:  # noqa: BLE001
        logger.debug("model info lookup failed for %r: %s", tag, e)
        return None


def sees_natively(tag: str) -> bool:
    """The single vision truth. Ollama first, declared rows second, name last."""
    return _vision_with_evidence(tag)[0]


def _vision_with_evidence(tag: str) -> Tuple[bool, str]:
    if not tag:
        return False, "no_tag"
    row = EXTERNAL_MODEL_ROWS.get(tag)
    info = _info(tag)
    if info is not None:
        caps = info.get("capabilities") or []
        # Deliberately NOT falling back to counting ".vision." keys in
        # model_info: measured 2026-09-22, several models that genuinely report
        # the vision capability expose zero such keys (gemma4:12b,
        # muse-glimmer:30b, the qwen3.6 MoE coding tags). Absence of tower
        # metadata says nothing, so treating it as a signal invents both false
        # negatives and false confidence.
        return ("vision" in caps), "api_show_capabilities"
    if row is not None:
        return bool(row.get("sees_natively")), "declared_row"
    # Ollama unreachable. Guess, and say that we guessed.
    return name_looks_vision(tag), "name_guess_ollama_unreachable"


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------

def _load_probe_store() -> dict:
    try:
        return json.loads(PROBE_STORE.read_text())
    except Exception:
        return {}


def coords_for(tag: str, screen: Optional[Tuple[int, int]] = None) -> CoordConvention:
    """What convention this model points in, and how sure we are.

    Order of authority:

    1. An explicit ``coord_order`` in MODEL_VISION_CONFIGS. Hand-measured and
       load-bearing; never overridden here.
    2. A probed row, written by the coordinate probe after measuring the model.
    3. The architecture family default, declared in model_capability_data.
    4. The prompt contract. The servo's own anchor prompt demands
       ``[y1,x1,y2,x2]`` normalised to 1000, so a model that answers with a
       box_2d at all is claiming to have followed it. This replaces a silent
       "xy" default that contradicted the very prompt the system had just sent —
       on the shipped default model that meant clicking with the axes swapped.
       Low confidence on purpose: it is a guess, just a defensible one.
    """
    try:
        from backend.services.servo_knowledge_store import MODEL_VISION_CONFIGS, get_vision_config
        cfg = get_vision_config(tag) or {}
        if cfg.get("coord_order"):
            return CoordConvention(
                order=cfg["coord_order"],
                grid=cfg.get("internal_width", 1000),
                normalised=True, source="row", confidence=1.0,
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("vision config lookup failed for %r: %s", tag, e)

    key = tag if screen is None else f"{tag}@{screen[0]}x{screen[1]}"
    store = _load_probe_store()
    probed = store.get(key) or store.get(tag)
    if probed:
        if probed.get("order"):
            return CoordConvention(
                order=probed["order"], grid=probed.get("grid", 1000),
                normalised=probed.get("normalised", True),
                source="probe", confidence=float(probed.get("confidence", 0.9)),
            )
        # Probed and found wanting. Measured 2026-09-22:
        # qwen3-vl:8b-thinking-q8_0 returns an empty string to the pointing
        # prompt at every token budget tried, and ministral-3:14b answers in
        # the right format but says the target is not visible. Both have the
        # vision capability. Seeing is not the same as pointing, and only a
        # probe can tell the two apart.
        return CoordConvention(order=None, grid=None, normalised=False,
                               source="probe_failed", confidence=0.0)

    info = _info(tag) or {}
    fam = (info.get("architecture") or "").lower()
    fd = FAMILY_COORD_DEFAULTS.get(fam)
    if fd:
        return CoordConvention(order=fd["order"], grid=fd["grid"],
                               normalised=fd["normalised"],
                               source="family", confidence=fd["confidence"])

    return CoordConvention(order="yx", grid=1000, normalised=True,
                           source="prompt_contract", confidence=0.5)


# ---------------------------------------------------------------------------
# Eyes
# ---------------------------------------------------------------------------

def _installed() -> list:
    try:
        import requests
        from backend.utils.ollama_resource_manager import get_ollama_base_url
        r = requests.get(f"{get_ollama_base_url()}/api/tags", timeout=5)
        return [m["name"] for m in r.json().get("models", [])] if r.ok else []
    except Exception:
        return []


def _resident() -> list:
    try:
        import requests
        from backend.utils.ollama_resource_manager import get_ollama_base_url
        r = requests.get(f"{get_ollama_base_url()}/api/ps", timeout=5)
        return [m["name"] for m in r.json().get("models", [])] if r.ok else []
    except Exception:
        return []


def eyes_for(tag: str, surface: str = "agent_screen") -> Eyes:
    """Who looks at the screen for this model.

    A model that can see does its own looking — no detour, no second model, no
    VRAM. Only a blind one borrows eyes, and then we prefer a vision model
    already resident so the answer costs no model swap.
    """
    if sees_natively(tag):
        return Eyes(model=None, mechanism="native",
                    reason="This model sees the screen itself.")

    installed = _installed()
    if not installed:
        return Eyes(model=None, mechanism="none",
                    reason="Ollama is not reachable, so no eyes can be loaned.")

    candidates = [m for m in installed if m != tag and sees_natively(m)]
    if surface == "agent_screen":
        # Pointing is a harder job than describing: prefer an eye whose
        # coordinate convention we actually know.
        candidates.sort(key=lambda m: (-coords_for(m).confidence, _size_mb(m)))
    else:
        candidates.sort(key=_size_mb)
    if not candidates:
        return Eyes(model=None, mechanism="none",
                    reason="No vision-capable model is installed to lend eyes.")

    resident = set(_resident())
    for m in candidates:
        if m in resident:
            return Eyes(model=m, mechanism="sibling_vlm",
                        reason=f"{m} is already loaded and can see; no model swap needed.")
    pick = candidates[0]
    return Eyes(model=pick, mechanism="sibling_vlm",
                reason=f"{tag} cannot see, so {pick} will be loaded alongside it to look.")


def _size_mb(tag: str) -> float:
    return float((_info(tag) or {}).get("size_mb") or 1e9)


# ---------------------------------------------------------------------------
# The whole answer
# ---------------------------------------------------------------------------

def resolve(tag: str, surface: str = "agent_screen",
            screen: Optional[Tuple[int, int]] = None) -> ModelProfile:
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r}; expected one of {SURFACES}")
    ck = (tag or "", surface)
    now = time.time()
    with _lock:
        hit = _cache.get(ck)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]

    info = _info(tag)
    vision, evidence = _vision_with_evidence(tag)
    eyes = eyes_for(tag, surface)
    coords = coords_for(tag, screen)

    blockers = []
    if info is None and tag not in EXTERNAL_MODEL_ROWS:
        blockers.append("Ollama cannot describe this model — capabilities are guessed from its name.")
    if not vision and eyes.model is None:
        blockers.append(eyes.reason)
    if coords.source == "probe_failed":
        blockers.append("Probed and could not point: this model returns no usable answer to "
                        "the pointing prompt, even though it can see.")
    elif coords.order is None:
        blockers.append("Coordinate convention unknown — run the coordinate probe before clicking.")
    elif coords.confidence < MEASURED_CONFIDENCE:
        blockers.append(f"Coordinate convention is a guess ({coords.source}) — run the "
                        f"coordinate probe to confirm it before trusting a click.")

    prof = ModelProfile(
        tag=tag,
        exists=info is not None,
        sees_natively=vision,
        supports_tools="tools" in ((info or {}).get("capabilities") or []),
        supports_thinking="thinking" in ((info or {}).get("capabilities") or []),
        context_window=int((info or {}).get("native_context") or 0),
        size_mb=float((info or {}).get("size_mb") or 0.0),
        architecture=(info or {}).get("architecture") or "unknown",
        eyes=eyes,
        coords=coords,
        # A guessed convention is not a licence to click. Reading a model's
        # output with the wrong axis order does not fail loudly — it clicks a
        # plausible wrong place, mirrored across the diagonal, and reports
        # success. Measured on gemma4:e4b: 326px median error read the wrong
        # way against 63px read the right way.
        can_drive_screen=bool((vision or eyes.model) and coords.order
                              and coords.confidence >= MEASURED_CONFIDENCE),
        blockers=tuple(blockers),
        evidence={"vision": evidence, "coords": coords.source},
    )
    with _lock:
        _cache[ck] = (now, prof)
    return prof


def invalidate(tag: Optional[str] = None) -> None:
    """Drop cached answers. Call on model switch and after a probe writes."""
    with _lock:
        if tag is None:
            _cache.clear()
        else:
            for k in [k for k in _cache if k[0] == tag]:
                _cache.pop(k, None)


def describe_for_ui(tag: str, surface: str = "agent_screen") -> dict:
    """The shape a model picker needs: can it drive the screen, and what else loads."""
    p = resolve(tag, surface)
    alongside = []
    if p.eyes.model:
        alongside.append({"kind": "model", "tag": p.eyes.model,
                          "vram_mb": round(_size_mb(p.eyes.model)), "why": "eyes"})
    return {
        "tag": p.tag, "exists": p.exists, "sees_natively": p.sees_natively,
        "can_drive_screen": p.can_drive_screen, "blockers": list(p.blockers),
        "supports_tools": p.supports_tools, "supports_thinking": p.supports_thinking,
        "context_window": p.context_window, "size_mb": round(p.size_mb),
        "architecture": p.architecture,
        "coords": {"order": p.coords.order, "grid": p.coords.grid,
                   "source": p.coords.source, "confidence": p.coords.confidence},
        "eyes": {"model": p.eyes.model, "mechanism": p.eyes.mechanism,
                 "reason": p.eyes.reason},
        "will_load_alongside": alongside,
        "evidence": p.evidence,
    }
