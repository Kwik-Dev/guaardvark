"""Video Director — a cinematic prompt/storyboard agent for the batch video generator.

This is the batch-video sibling of ``music_video_director``. The music director is
built around a *song cut plan* (timed sections + beat energy) and writes a connected
storyline across those cuts. The batch generator instead works on a list of
*independent* prompts (or one concept to expand), so it needs a director that:

  * ``direct_prompts(prompts, ...)`` — upgrade each plain user prompt into a rich,
    shot-ready VISUAL prompt (subject, setting, camera/lens/movement, lighting, color,
    mood, motion). The "director agent."
  * ``storyboard_from_concept(concept, n, ...)`` — expand ONE idea into N distinct but
    connected shot prompts. The "storyboarding agent."

It reuses the music director's hard-won safety primitives — model resolution that
never hands an embedding model to ``format="json"`` chat, and a never-raise contract
(on any LLM/parse failure we fall back to the lighter ``enhance_video_prompt`` or the
original prompt, so a director hiccup can never fail a render). It is deliberately
song-agnostic: no beats, no sections, no MusicVideo model.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

# Reuse the music director's vetted model-resolution + embedding-filter so we never
# regress the "don't send an embedding model to JSON-mode chat" lesson.
from backend.services.music_video_director import (
    DIRECTOR_MODEL,
    _resolve_model,
    _is_embedding_model,
)

log = logging.getLogger(__name__)

# A small, fast model is ideal for the strict "pure visual prompt only / one entry per
# item" JSON contract — same rationale as the music director.
DEFAULT_DIRECTOR_MODEL = DIRECTOR_MODEL

_SYSTEM_DIRECT = """You are a cinematic director and visual screenwriter for an AI video generator.
You are given one or more SHORT user ideas. For EACH idea, rewrite it as a single vivid,
shot-ready VISUAL prompt that a text-to-video / image-to-video model can render well.

Each rewritten prompt MUST:
- Keep the user's core subject and intent — enrich, never replace it.
- Describe the SHOT: subject + action, setting, camera (lens, angle, movement), lighting,
  color palette, mood, and the motion that should happen across the clip.
- Be PURE visual description. No narration, no "the video shows", no camera-crew talk,
  no audio, no scene numbers, no quotes around the whole thing.
- Be one flowing paragraph, roughly 40-80 words.

Return STRICT JSON: {"prompts": ["<rewritten 1>", "<rewritten 2>", ...]} with exactly one
entry per input idea, in the same order. No commentary outside the JSON."""

_SYSTEM_STORYBOARD = """You are a cinematic director and visual screenwriter for an AI video generator.
You are given ONE concept and a number of shots N. Break the concept into N distinct but
connected shots that read as a sequence (a recurring world/subject, varied angles and
scenes, a sense of progression) — NOT N reseeds of the same image.

Each shot prompt MUST:
- Be a single vivid, shot-ready VISUAL prompt (subject + action, setting, camera lens/angle/
  movement, lighting, color palette, mood, motion).
- Be PURE visual description. No narration, no "the video shows", no audio, no shot numbers,
  no quotes.
- Be one flowing paragraph, roughly 40-80 words.

Return STRICT JSON: {"prompts": ["<shot 1>", ..., "<shot N>"]} with exactly N entries in
order. No commentary outside the JSON."""


def _style_clause(style: Optional[str]) -> str:
    """A short global style line appended to the director's instruction. ``style`` is the
    same free-text 'look & feel' the batch UI already collects (e.g. 'neon noir, 35mm')."""
    style = (style or "").strip()
    return f"\nGlobal visual style to honor in every prompt: {style}." if style else ""


def _options(n: int) -> dict:
    """Ollama sampling/window options. ``num_predict`` is sized to the item count so a
    large batch doesn't truncate mid-array (the bug that bit the music director)."""
    n = max(1, n)
    return {"temperature": 0.7, "num_ctx": 4096, "num_predict": min(4096, 220 * n + 512)}


def _parse_prompts(content: str, n: int) -> list[str]:
    """Pull a list of N prompt strings out of the model's JSON, tolerantly. Returns
    [] on failure (caller then falls back). Accepts {"prompts": [...]} or a bare array."""
    if not content:
        return []
    text = content.strip()
    # Strip ```json fences if the model added them despite format=json.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    data: Any = None
    try:
        data = json.loads(text)
    except Exception:
        # Last-ditch: grab the first {...} or [...] block.
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except Exception:
                return []
    if data is None:
        return []
    if isinstance(data, dict):
        arr = data.get("prompts") or data.get("shots") or []
    elif isinstance(data, list):
        arr = data
    else:
        return []
    out = [str(p).strip() for p in arr if isinstance(p, (str, int, float)) and str(p).strip()]
    return out


def _chat_json(system: str, user: str, *, model: str, n: int) -> str:
    """Single Ollama JSON-mode chat call. Returns raw content ('' on any failure)."""
    try:
        import ollama
        resolved = _resolve_model(model or DEFAULT_DIRECTOR_MODEL)
        resp = ollama.chat(
            model=resolved,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            format="json",
            options=_options(n),
        )
        # ollama-lib returns a dict-like / object with message.content.
        msg = resp.get("message") if hasattr(resp, "get") else getattr(resp, "message", None)
        if msg is None:
            return ""
        content = msg.get("content") if hasattr(msg, "get") else getattr(msg, "content", "")
        return content or ""
    except Exception as e:  # noqa: BLE001 — never let a director hiccup fail a render
        log.warning("video_director chat failed (%s); caller will fall back", e)
        return ""


def _light_fallback(prompt: str, style: Optional[str]) -> str:
    """Director-off / failure fallback: the existing lightweight enhancer, then the raw
    prompt. Never raises."""
    try:
        from backend.utils.prompt_enhancer import enhance_video_prompt
        enhanced = enhance_video_prompt(prompt, style=(style or "cinematic"))
        if enhanced and enhanced.strip():
            return enhanced
    except Exception:
        pass
    base = (prompt or "").strip()
    if style and style.strip() and base:
        return f"{base}, {style.strip()}"
    return base


def direct_prompts(
    prompts: list[str],
    *,
    style: Optional[str] = None,
    model: str = DEFAULT_DIRECTOR_MODEL,
    extra_guidance: Optional[str] = None,
) -> list[str]:
    """Turn each plain user prompt into a rich cinematic shot prompt.

    Never raises and never changes the count: returns exactly ``len(prompts)`` strings.
    Any prompt the director couldn't produce falls back to the lightweight enhancer / the
    original prompt, so a partial/failed LLM response degrades gracefully per-item.
    """
    prompts = [p for p in (prompts or [])]
    if not prompts:
        return []
    user = (
        f"{_style_clause(style)}"
        + (f"\nDirector guidance: {extra_guidance.strip()}." if extra_guidance and extra_guidance.strip() else "")
        + "\n\nIdeas (rewrite each, one per entry, same order):\n"
        + json.dumps([str(p) for p in prompts], ensure_ascii=False)
    )
    directed = _parse_prompts(_chat_json(_SYSTEM_DIRECT, user, model=model, n=len(prompts)), len(prompts))
    out: list[str] = []
    for i, original in enumerate(prompts):
        cand = directed[i].strip() if i < len(directed) and directed[i].strip() else ""
        out.append(cand or _light_fallback(original, style))
    return out


def direct_prompt(
    prompt: str,
    *,
    style: Optional[str] = None,
    model: str = DEFAULT_DIRECTOR_MODEL,
    extra_guidance: Optional[str] = None,
) -> str:
    """Single-prompt convenience wrapper around :func:`direct_prompts`."""
    res = direct_prompts([prompt], style=style, model=model, extra_guidance=extra_guidance)
    return res[0] if res else _light_fallback(prompt, style)


def storyboard_from_concept(
    concept: str,
    num_shots: int,
    *,
    style: Optional[str] = None,
    model: str = DEFAULT_DIRECTOR_MODEL,
    extra_guidance: Optional[str] = None,
) -> list[str]:
    """Expand ONE concept into ``num_shots`` distinct-but-connected shot prompts.

    Never raises. On LLM/parse failure (or a short response) the missing shots are filled
    with a lightweight-enhanced variant of the concept so the caller always gets exactly
    ``num_shots`` usable prompts.
    """
    n = max(1, int(num_shots or 1))
    concept = (concept or "").strip()
    if not concept:
        return []
    user = (
        f"Concept: {concept}\nNumber of shots N: {n}."
        + _style_clause(style)
        + (f"\nDirector guidance: {extra_guidance.strip()}." if extra_guidance and extra_guidance.strip() else "")
    )
    shots = _parse_prompts(_chat_json(_SYSTEM_STORYBOARD, user, model=model, n=n), n)
    out = [s for s in shots[:n] if s]
    # Pad if the model under-delivered, so the count is exactly N.
    while len(out) < n:
        idx = len(out) + 1
        out.append(_light_fallback(f"{concept} (shot {idx} of {n})", style))
    return out
