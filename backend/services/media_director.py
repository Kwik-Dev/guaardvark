"""Media Director — generalized LLM director for image (and future cross-media) generation.

Reuses battle-tested primitives from music_video_director (and video_director) for:
- Dedicated small model (gemma4:e4b default) + embedding filter + resolve.
- Strict pure-visual JSON contracts, tolerant parsing, batching to avoid truncation.
- Never-raise + post-guards (distinctness, energy/mood cues, style hygiene).
- Treatment (narrative) vs. shots/prompts (visual only) separation.
- Planning modes + extra_guidance.
- Diagnostics surfaced.

For BatchImage: supports expand_image_plan (concept -> N coherent shots + optional treatment)
and enhance_prompts (per-prompt cinematic enrichment).

For chat: enhance single prompts before the generator.

Vision post-refine hook stub for future critique loops (using VisionAnalyzer / curator patterns).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional, Dict

# Reuse ALL vetted primitives (model safety, parse, chat, guards) to avoid regressions.
from backend.services.music_video_director import (
    DIRECTOR_MODEL,
    _resolve_model,
    _is_embedding_model,
    _parse_prompts as _base_parse_prompts,
    _director_options as _base_options,
    _is_mostly_style,
    _ensure_distinct_and_energy_aware,
    _director_chat as _base_director_chat,
    _installed_model_tags,
)

log = logging.getLogger(__name__)

# Image-specific dedicated model (same small fast one; user can override).
DEFAULT_DIRECTOR_MODEL = DIRECTOR_MODEL

# Batching for large N (same rationale).
DIRECTOR_BATCH_SIZE = 12  # Slightly smaller for image plans (richer per-shot often)

# Image "storyboard" contract: one concept -> N connected visual prompts.
_SYSTEM_STORYBOARD_IMAGE = """You are a visual director for still image generation.
Given ONE high-level concept and N, produce N distinct but visually coherent image prompts that feel like a series or storyboard.
Each prompt MUST:
- Be PURE VISUAL only (subject appearance, setting, framing/composition, camera/lens/angle if applicable, lighting, color palette, texture, mood, key style elements). No backstory, names, plot, "the image shows".
- Maintain world/character/motif consistency across the set (use recurring visual descriptors drawn from the concept).
- Vary framing, angle, action/moment, density, lighting to show progression or interesting variations.
- Be concise comma/phrase style suitable for SD/Flux/etc. (aim <30 words).
- Honor any global STYLE provided as a suffix (do NOT duplicate full style in prompt).
Return STRICT JSON: {"treatment": "<optional short evocative treatment>", "prompts": ["prompt1", "prompt2", ...]} with exactly N entries in order."""

_SYSTEM_ENHANCE_IMAGE = """You are a cinematic visual director for still images.
Rewrite each short user idea into a rich, shot-ready PURE VISUAL prompt (subject + setting + framing/composition + lighting + palette + mood + texture + style elements).
Keep the user's core intent. Enrich, never replace.
Be one flowing descriptive phrase. No narration, no "the image depicts", no meta.
STYLE (if given) is appended by caller; do not repeat it wholesale.
Return STRICT JSON: {"prompts": ["enriched1", "enriched2", ...]} exactly one per input, same order."""

def _options(n: int, sampling: Optional[dict] = None) -> dict:
    n = max(1, n)
    opts = {"temperature": 0.68, "num_ctx": 4096, "num_predict": min(3072, 180 * n + 512)}
    if sampling:
        # Sampling-profile knobs override temperature etc.; window/budget stay authoritative
        # (num_predict sized to N is the JSON-truncation fix).
        opts.update({k: v for k, v in sampling.items() if k not in ("num_ctx", "num_predict")})
    return opts

def _parse_image_prompts(content: str, n: int) -> List[str]:
    """Tolerant list parse for image plans/enhance. Accepts {"prompts": [...]}, {"shots": [...]}, bare array."""
    if not content:
        return []
    text = content.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    data: Any = None
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except Exception:
                pass
    if data is None:
        return []
    if isinstance(data, list):
        out = [str(x).strip() for x in data if str(x).strip()]
        return out[:n] if n else out
    if isinstance(data, dict):
        for key in ("prompts", "shots", "images", "items"):
            arr = data.get(key)
            if isinstance(arr, list):
                out = [str(x).strip() for x in arr if str(x).strip()]
                return out[:n] if n else out
        # Fallback single "prompt"
        if isinstance(data.get("prompt"), str):
            return [data["prompt"].strip()]
    return []

def _style_clause(style: Optional[str]) -> str:
    style = (style or "").strip()
    return f"\nGlobal visual style/aesthetic to honor: {style}." if style else ""


def _verbatim_prompts_enabled() -> bool:
    """True when the operator turned ON 'verbatim prompts' — send the user's EXACT words
    to the image/video model and SKIP the director-LLM rewrite (no softening, no
    enrichment). This is the 'no soft limits' switch (your machine, your rules).

    OFF by default (keeps cinematic enrichment). Sources, in order:
      * env VERBATIM_PROMPTS=1/true/yes/on — restart-proof, wins (good for a live demo), then
      * the runtime SystemSetting 'verbatim_prompts' (toggle in Settings, no restart).
    Any error → False (enrichment stays on)."""
    import os
    if os.environ.get("VERBATIM_PROMPTS", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        from backend.models import SystemSetting, db
        row = db.session.query(SystemSetting).filter_by(key="verbatim_prompts").first()
        return bool(row and str(row.value).lower() == "true")
    except Exception:
        return False


def enhance_prompts(
    prompts: List[str],
    *,
    style: str = "",
    extra_guidance: Optional[str] = None,
    model: Optional[str] = None,
    cast_descriptors: Optional[List[str]] = None,
    sampling: Optional[dict] = None,
) -> List[str]:
    """Enrich a list of user prompts into rich pure-visual shot-ready prompts via director LLM.
    Best-effort: on any failure returns originals (or lightly cued).
    Used by chat ImageGeneratorTool and batch pre-pass.
    """
    if not prompts:
        return []
    if _verbatim_prompts_enabled():
        log.info("media_director: verbatim prompts ON — sending user prompts to the model as-is (no director rewrite)")
        return list(prompts)
    n = len(prompts)
    resolved = _resolve_model(model or DEFAULT_DIRECTOR_MODEL)
    style_c = _style_clause(style)
    guidance = f"\nExtra direction: {extra_guidance.strip()}." if extra_guidance and extra_guidance.strip() else ""
    cast = ""
    if cast_descriptors:
        cast = "\nConsistent visual character/subject descriptors to weave in naturally (visual only): " + "; ".join(cast_descriptors[:3])

    user = (
        f"STYLE: {style or '(none)'}\n"
        f"INPUT IDEAS ({n}):\n" + "\n".join(f"{i+1}. {p}" for i,p in enumerate(prompts)) +
        f"{style_c}{guidance}{cast}\n\n"
        "TASK: Return ONLY JSON with 'prompts' array of exactly N enriched pure-visual prompts. Preserve order and core intent."
    )
    try:
        # NOTE: do NOT use the music-director's _director_chat here — its parser hunts for a
        # "shots" array, but this enrich contract returns {"prompts": [...]}. Mismatched parsing
        # silently returned [] → originals (the batch-director no-op bug, fixed 2026-06-23).
        # Mirror storyboard_from_concept: own chat call + _parse_image_prompts (list-aware).
        import ollama
        opts = _options(n, sampling)
        resp = ollama.chat(
            model=resolved,
            format="json",
            messages=[
                {"role": "system", "content": _SYSTEM_ENHANCE_IMAGE},
                {"role": "user", "content": user},
            ],
            options=opts,
        )
        out = _parse_image_prompts(resp["message"]["content"], n)
        if len(out) == n:
            return [p.strip() for p in out]
        log.warning("media_director.enhance_prompts parsed %d/%d prompts; falling back", len(out), n)
    except Exception as e:  # noqa: BLE001
        log.warning("media_director.enhance_prompts failed (%s); falling back", e)
    # Fallback: return originals (caller may still do keyword enhance)
    return list(prompts)


_SYSTEM_REFINE_EDIT = """You are an expert prompt writer for an instruction-based image EDITOR (FLUX Kontext).
Rewrite the user's terse edit request into ONE precise, imperative edit directive.
Rules:
- Name the TARGET (what changes), the CHANGE (exactly what it becomes — material, color, placement, style), and what to PRESERVE (keep the same face, identity, pose, framing, lighting, background, and everything not mentioned).
- Do NOT add new subjects, do NOT describe the whole scene, do NOT turn it into a from-scratch image prompt.
- Keep it to one or two sentences.
Return ONLY JSON: {"instruction": "<the rewritten edit directive>"}."""


def refine_edit_instruction(instruction: str, *, model: Optional[str] = None,
                            sampling: Optional[dict] = None) -> str:
    """Rewrite a terse image-EDIT instruction into a precise Kontext edit directive via the
    director LLM (gemma4). Best-effort: returns the ORIGINAL on any failure.

    Distinct from enhance_prompts (which writes a txt2img *visual* prompt and would inject
    new subjects) — this MUST stay an edit directive: target + change + what-to-preserve."""
    instr = (instruction or "").strip()
    if not instr:
        return instruction
    if _verbatim_prompts_enabled():
        log.info("media_director: verbatim prompts ON — using edit instruction as-is (no Kontext rewrite)")
        return instruction
    resolved = _resolve_model(model or DEFAULT_DIRECTOR_MODEL)
    try:
        import ollama
        import json as _json
        resp = ollama.chat(
            model=resolved,
            format="json",
            messages=[
                {"role": "system", "content": _SYSTEM_REFINE_EDIT},
                {"role": "user", "content": f"User edit request: {instr}"},
            ],
            options=_options(1, sampling),
        )
        data = _json.loads(resp["message"]["content"])
        refined = (data.get("instruction") or "").strip()
        if refined:
            return refined
        log.warning("media_director.refine_edit_instruction returned empty; using original")
    except Exception as e:  # noqa: BLE001
        log.warning("media_director.refine_edit_instruction failed (%s); using original", e)
    return instruction


def storyboard_from_concept(
    concept: str,
    n: int,
    *,
    style: str = "",
    extra_guidance: Optional[str] = None,
    model: Optional[str] = None,
    sampling: Optional[dict] = None,
) -> Dict[str, Any]:
    """Expand ONE concept into N coherent visual prompts (+ optional treatment).
    Returns {"treatment": str|None, "prompts": [str, ...]} .
    Never raises; on failure returns {"treatment": None, "prompts": [concept] * n} (or energy cued).
    """
    if n < 1:
        n = 1
    resolved = _resolve_model(model or DEFAULT_DIRECTOR_MODEL)
    style_c = _style_clause(style)
    guidance = f"\nExtra direction: {extra_guidance.strip()}." if extra_guidance and extra_guidance.strip() else ""

    user = (
        f"CONCEPT: {concept}\nN={n}\nSTYLE: {style or '(none)'}{style_c}{guidance}\n\n"
        "TASK: Return ONLY the JSON with optional 'treatment' and exactly N 'prompts'."
    )
    try:
        # Use a direct chat wrapper for storyboard (rich)
        import ollama
        opts = _options(n, sampling)
        resp = ollama.chat(
            model=resolved,
            format="json",
            messages=[
                {"role": "system", "content": _SYSTEM_STORYBOARD_IMAGE},
                {"role": "user", "content": user},
            ],
            options=opts,
        )
        content = resp["message"]["content"]
        data = _parse_storyboard_output(content, n)
        prompts = data.get("prompts") or []
        if len(prompts) != n:
            prompts = _ensure_image_distinct(prompts or [], concept, n, style)
        return {"treatment": data.get("treatment"), "prompts": prompts[:n]}
    except Exception as e:  # noqa: BLE001
        log.warning("media_director.storyboard_from_concept failed (%s); simple fallback", e)
        base = concept.strip()
        return {"treatment": None, "prompts": [base] * n}

def _parse_storyboard_output(content: str, n: int) -> Dict[str, Any]:
    try:
        data = json.loads(content)
    except Exception:
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(content[start:end+1])
            except Exception:
                data = {}
        else:
            data = {}
    if not isinstance(data, dict):
        data = {}
    prompts = data.get("prompts") or data.get("shots") or []
    if isinstance(prompts, list):
        prompts = [str(p).strip() for p in prompts if str(p).strip()][:n]
    else:
        prompts = []
    treatment = data.get("treatment") if isinstance(data.get("treatment"), str) else None
    return {"treatment": treatment, "prompts": prompts}

def _ensure_image_distinct(prompts: List[str], concept: str, n: int, style: str) -> List[str]:
    """Light deterministic guard (mirrors MV energy cues for images)."""
    out = []
    cues = [
        "wide establishing framing, atmospheric depth",
        "medium shot, focused action",
        "tight dramatic close, strong contrast",
        "dynamic angle from low, motion implication",
        "overhead or high angle, sparse or dense texture",
    ]
    for i in range(n):
        base = (prompts[i] if i < len(prompts) else concept).strip()
        cue = cues[i % len(cues)]
        if style and style.lower() not in base.lower():
            candidate = f"{base}, {cue}, {style}".strip(", ")
        else:
            candidate = f"{base}, {cue}".strip(", ")
        out.append(candidate)
    return out

def expand_image_plan(
    idea: str,
    n: int,
    *,
    look_and_feel: str = "",
    user_treatment: Optional[str] = None,
    planning_mode: str = "narrative",
    extra_guidance: Optional[str] = None,
    model: Optional[str] = None,
    cast_descriptors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """High-level BatchImage-friendly entry: concept/idea + N -> {treatment, shots}.
    shots = list of dicts with 'prompt' (and index).
    Mirrors MV plan shape for UI reuse.
    """
    res = storyboard_from_concept(
        idea, n, style=look_and_feel, extra_guidance=(extra_guidance or user_treatment), model=model
    )
    prompts = res.get("prompts") or []
    # light enhance pass for consistency if cast
    if cast_descriptors and prompts:
        try:
            enriched = enhance_prompts(prompts, style=look_and_feel, cast_descriptors=cast_descriptors, model=model)
            if len(enriched) == len(prompts):
                prompts = enriched
        except Exception:
            pass
    shots = [{"index": i, "prompt": p} for i, p in enumerate(prompts[:n])]
    return {
        "treatment": res.get("treatment") or (user_treatment or ""),
        "shots": shots,
        "planning_mode": planning_mode,
        "director_model": model or DEFAULT_DIRECTOR_MODEL,
        "director_diagnostics": None,  # future: surface fallback info
    }

# Public aliases for batch compatibility
direct_prompts = enhance_prompts
