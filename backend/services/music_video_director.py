"""Music-video Director — the missing storyboard layer.

Before this, every music-video clip reused ONE global ``style_prompt`` with only the
FLUX seed varied (``seed = 1000 + idx``) — "a bunch of videos of the same theme." The
Director turns the song's cut plan (timing + energy + section) plus the global style
into a DISTINCT, narratively-connected shot prompt PER CUT, so the clips read as a
sequence (recurring world/subject, energy-driven intensity, varied scenes/angles)
instead of N reseeds of the same image.

It runs in the ANALYZE stage (before the cost-approval gate, no GPU) using the local
LLM with ``format="json"`` and tolerant parsing — the same shape as the video_editor
plugin's art_director. It DEGRADES GRACEFULLY: if the LLM is unavailable or returns
garbage, it falls back to the global style prompt for every cut — i.e. exactly today's
behavior, never a regression.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemma4:e4b"

_SYSTEM = """You are a music-video director. You are given a global visual STYLE and an
ordered list of timed CUTS for one song (each cut has: index, duration seconds, section
label like intro/build/drop/outro, and a normalized energy 0..1).

Write ONE distinct, concrete, visual shot prompt per cut so that, played in order, the
cuts read as a single coherent music video — not unrelated images:
- Keep a consistent world, subject(s), palette and mood across the whole song (continuity).
- VARY the shot each cut: location, framing (wide/medium/close-up), angle, action, time of day.
- Let ENERGY drive intensity: low energy = calm, slow, wide, sparse; high energy = motion,
  tight, dynamic, dense. Match the section arc (intro establishes, drops peak, outro resolves).
- Each prompt is a short comma-separated visual description (subject + setting + framing +
  lighting + motion). Do NOT mention music, lyrics, text, or the song itself.

Return ONLY JSON, no prose, of this exact shape:
{"shots": [{"index": <int>, "prompt": "<visual description>"}]}
There must be exactly one entry per cut, indexes matching the input."""


def _installed_model_tags() -> set[str]:
    """Tags currently pulled in Ollama. Robust across ollama-lib versions: newer
    returns ListResponse with Model objects (tag under ``.model``); older returned
    plain dicts (``name``/``model``). Empty set on any failure."""
    import ollama
    resp = ollama.list()
    models = resp.get("models", []) if hasattr(resp, "get") else getattr(resp, "models", [])
    tags: set[str] = set()
    for m in models or []:
        tag = getattr(m, "model", None)
        if tag is None and hasattr(m, "get"):
            tag = m.get("model") or m.get("name")
        if tag is None:
            tag = getattr(m, "name", None)
        if tag:
            tags.add(tag)
    return tags


def _resolve_model(preferred: str) -> str:
    """Pick a model that's actually pulled. Prefer ``preferred``; else any gemma (the
    project's brain/vision family); else the first installed model; else ``preferred``
    unchanged (the chat call then fails → graceful fallback). Avoids the silent
    no-storyboard trap where the hardcoded tag (gemma4:e4b) isn't the pulled one
    (gemma4:latest) on a given box."""
    try:
        tags = _installed_model_tags()
        if not tags or preferred in tags:
            return preferred
        for t in sorted(tags):
            if "gemma" in t:
                return t
        return next(iter(sorted(tags)), preferred)
    except Exception:  # noqa: BLE001
        return preferred


def _cut_brief(cut_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in cut_plan:
        out.append({
            "index": c["index"],
            "seconds": round(float(c["end_s"]) - float(c["start_s"]), 2),
            "section": c.get("section_label", ""),
            "energy": round(float(c.get("energy", 0.0)), 3),
        })
    return out


def _parse_prompts(content: str, n: int) -> dict[int, str]:
    """Pull {index: prompt} out of the model's JSON, tolerantly. Returns {} on failure."""
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        # Fallback: grab the first {...} block (model wrapped it in prose/fences).
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            data = json.loads(content[start:end + 1])
        except (ValueError, TypeError):
            return {}
    shots = data.get("shots") if isinstance(data, dict) else data
    if not isinstance(shots, list):
        return {}
    out: dict[int, str] = {}
    for i, item in enumerate(shots):
        if not isinstance(item, dict):
            continue
        idx = item.get("index", i)
        prompt = item.get("prompt") or item.get("description")
        if isinstance(idx, int) and isinstance(prompt, str) and prompt.strip():
            out[idx] = prompt.strip()
    return out


def generate_scene_prompts(
    style_prompt: str,
    cut_plan: list[dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
) -> list[str]:
    """One visual prompt per cut, in cut order. Never raises.

    Each returned prompt is the Director's per-cut scene with the global ``style_prompt``
    appended as a suffix (so the look stays consistent while the scene varies). On any
    failure every entry is just ``style_prompt`` — today's behavior, no regression.
    """
    n = len(cut_plan)
    if n == 0:
        return []
    fallback = [style_prompt] * n
    try:
        import ollama
        model = _resolve_model(model)
        user = (
            f"STYLE: {style_prompt}\n\n"
            f"CUTS ({n} total):\n{json.dumps(_cut_brief(cut_plan))}\n\n"
            "Return the JSON shot list now."
        )
        resp = ollama.chat(
            model=model,
            format="json",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            options={"temperature": 0.7},
        )
        prompts = _parse_prompts(resp["message"]["content"], n)
        if not prompts:
            log.warning("director returned no usable prompts; using global style for all %d cuts", n)
            return fallback
        out: list[str] = []
        for c in cut_plan:
            scene = prompts.get(c["index"])
            out.append(f"{scene}, {style_prompt}" if scene else style_prompt)
        return out
    except Exception as e:  # noqa: BLE001 — director is best-effort; never sink the analyze stage
        log.warning("director failed (%s); falling back to global style prompt", e)
        return fallback
