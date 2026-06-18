"""Music-video Director — the missing storyboard layer.

Before this, every music-video clip reused ONE global ``style_prompt`` with only the
FLUX seed varied (``seed = 1000 + idx``) — "a bunch of videos of the same theme." The
Director turns the song's cut plan (timing + energy + section) plus the global style
into a DISTINCT, narratively-connected shot prompt PER CUT, so the clips read as a
sequence (recurring world/subject, energy-driven intensity, varied scenes/angles)
instead of N reseeds of the same image.

It runs in the ANALYZE stage (before the cost-approval gate, no GPU) using the local
LLM with ``format="json"`` and tolerant parsing — the same shape as the video_editor
plugin's art_director. The plain "repeat global style verbatim for every cut" fallback
is DISABLED for music video (user requirement): on LLM failure / empty / cardinality
problems we still emit energy-aware cue variations of the style (via the deterministic
guard) and surface director_diagnostics so the UI can warn the operator. Only when
director_enabled=False in settings do you get pure repeated global style.

Supports planning_mode ("narrative" vs "visual"/"mood_arc") and extra_guidance so the
same engine can serve both character-driven videos and abstract/soundtrack "visual tone
poems" driven purely by energy and mood arc.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# Dedicated lightweight model for the Music Video Director.
# The Director is a narrow structured-JSON + visual-storytelling task (treatment + N shot prompts).
# We prefer a small, fast model (good at following the strict "pure visual prompt only", "one entry per cut" contract)
# rather than whatever large model the user has loaded for general chat/brain (e.g. gemma4:12b).
# Users can override per-video with settings_json["director_model"].
DIRECTOR_MODEL = "gemma4:e4b"
DEFAULT_MODEL = DIRECTOR_MODEL  # legacy alias for any direct callers of generate_scene_prompts

# Cuts are generated one LLM call at a time in batches of this size. A small model asked to
# emit ALL N shots in one JSON response overflows its output window once N gets large (~150
# cuts truncated mid-array → unparseable → empty prompts → mechanical energy-cue fallback).
# Batching keeps each JSON response small enough that even the small dedicated model produces
# valid, distinct shots. 20 sits between the largest known good single call (22) and the
# known-failing one (158).
DIRECTOR_BATCH_SIZE = 20


def _is_embedding_model(name: str | None) -> bool:
    """Embedding models (used for RAG/vector search) do not support the chat API
    (or JSON mode) that the Director requires. Filter them aggressively."""
    if not name:
        return False
    n = name.lower()
    bad_substrings = ("embed", "embedding", "bge", "nomic", "snowflake", "e5", "gte", "minilm", "all-minilm")
    return any(b in n for b in bad_substrings)

_SYSTEM = """You are a music-video director and visual screenwriter. You are given a global visual STYLE and an
ordered list of timed CUTS for one song (each cut has: index, duration seconds, section
label like intro/build/drop/outro, and a normalized energy 0..1).

Your job is to create a single, compelling VISUAL STORY that feels like one cohesive music video or short film, not a list of unrelated images.

Step 1 — Write a rich VISUAL TREATMENT / SHORT STORY (aim for 400-1200 words when expanded, but keep the JSON field concise yet evocative).
The treatment must:
- Be written in the exact aesthetic language of the provided STYLE (e.g. "American Hand Drawn Animation, Dark, Gothic..." must produce prose that sounds like it belongs in that visual world — dramatic lines, high contrast, specific textures, etc.).
- Follow the song's emotional and energy arc: intro establishes world/character/mood, build introduces conflict or rising elements, drop is the peak (action, revelation, intensity), outro resolves or transforms with a final image or feeling.
- Include recurring visual motifs, a clear through-line (character journey, place evolving, mood shift, visual metaphor), and explicit "intense vs calm" contrasts where the style prompt calls for them.
- Read like screenwriting — vivid, cinematic, specific about light, composition, movement, and atmosphere. Do not mention the song, music, beats, or "the drop".

Step 2 — Break the treatment into specific, distinct shot plans, one per cut.

CRITICAL SEPARATION OF CONCERNS (strictly enforce):
- The top-level "treatment" field is the ONLY place allowed to contain narrative, character names, backstory, emotional journey, or plot points. It can read like a dreamlike short story or screenplay treatment.
- Every "shots[].prompt" MUST be a PURE VISUAL PROMPT ONLY. 
  - NEVER use character names (use consistent visual descriptors drawn from the treatment instead, e.g. "the pale woman with dark flowing hair and luminous eyes" or simply describe what the camera sees).
  - NEVER include backstory, plot exposition, or "the character is X because of Y".
  - Focus exclusively on what an image generator + i2v needs: subject appearance (visual only), setting, framing/composition, camera angle and movement, lighting, color palette and contrast, texture, atmosphere, mood/emotion as conveyed purely through image, key recurring style elements.
  - These prompts are fed directly to FLUX/SDXL for keyframes and then to i2v, so they must be optimized for visual consistency and cinematic quality.

Each shot plan must:
- Visually realize one specific moment from the treatment.
- Maintain strict visual continuity across all shots (same world, recurring visual motifs, palette, line quality, lighting language, overall style).
- Vary framing, angle, distance, action, and composition.
- Respond to the cut's energy and section (low energy = slower, wider, sparser, calmer; high energy = tighter, more dynamic, denser).
- Be a short, concrete, comma-separated visual description suitable for an image generator: subject appearance (visual descriptors only), setting, framing/composition, camera angle and motion, lighting, color palette and contrast, mood/emotion as conveyed visually, key style elements, atmosphere. Focus on consistency, visuals, motion, style, emotion, color, camera, texture.
- Never mention music, the song, lyrics, beats, or "the drop".
- For EDITING (to make it more dramatic, resource-efficient, and cinematic using the final Shotcut/MLT assembler):
  - duration_seconds: optional float — suggest the ideal source clip length for this visual (0.6s min, typically up to base_cut * 2). Longer holds for calm/intense drama; shorter staccato for peaks. The system will stretch it to the timeline slot.
  - transition_to_next: choose from available (hard-cut for energy/punch, luma-wipe/luma-circle for dramatic shifts, cross-dissolve for smooth builds, etc.). Match energy and mood.
  - filter_preset: choose from available (none, or style-specific like warm-tint, high-contrast, glow, vertigo, cool-tint). Reinforce the visual language and energy without extra generation cost.

Return ONLY valid JSON, no extra prose:
{
  "treatment": "<rich, evocative visual story / treatment text that could stand alone as the creative foundation for the video (names, backstory, and plot are allowed here)>",
  "shots": [
    {
      "index": <int>,
      "prompt": "<PURE VISUAL PROMPT ONLY — no names, no backstory, no plot. Example: 'ethereal woman with dark flowing hair in flowing white dress, standing at the edge of a still dark lake under fractured silver moonlight, extreme shallow depth of field, soft bokeh, slow drifting camera, deep indigo and warm amber palette, volumetric god rays, dreamlike impressionist atmosphere'>",
      "duration_seconds": <float or null>,
      "transition_to_next": "<hard-cut | luma-wipe | cross-dissolve | ... or null>",
      "filter_preset": "<none | warm-tint | high-contrast | ... or null>"
    }
  ]
}
Exactly one shot per input cut. Indexes must match exactly. Use the EDITING fields to turn this into a real edited music video, not just a slideshow of similar shots."""

# Lightweight system prompt for shots-only batches (every batch after the first, plus any
# recovery call). Small models choke on the full treatment + strict separation + editing
# fields all at once; this tiny contract focuses purely on distinct visual prompts.
_SHOTS_ONLY_SYSTEM = (
    "You are a music-video director. Follow the mode, guidance, and treatment instructions exactly. "
    "Output ONLY valid JSON with a 'shots' array. No prose, no fences, no extra keys."
)


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
    unchanged (the chat call then fails → graceful fallback).

    For the Director we default to a small dedicated model (DIRECTOR_MODEL) optimized
    for fast, reliable structured JSON + strict visual-prompt-only output. This is
    intentionally separate from whatever large model is loaded for general chat/brain.

    Critically: never return an embedding model (they don't support /api/chat or
    the JSON format mode the Director needs). If the requested/preferred model is an
    embedding model, we force the safe DIRECTOR_MODEL.
    """
    try:
        raw_tags = _installed_model_tags() or set()
        # Aggressively exclude embedding models — they are pulled for RAG but will 400
        # on ollama.chat with format=json.
        tags = {t for t in raw_tags if not _is_embedding_model(t)}

        if not tags:
            return DIRECTOR_MODEL

        if preferred and not _is_embedding_model(preferred) and preferred in tags:
            return preferred

        # Prefer any gemma (they tend to be reliable for the strict prompt contract).
        for t in sorted(tags):
            if "gemma" in t:
                return t

        return next(iter(sorted(tags)), DIRECTOR_MODEL)
    except Exception:  # noqa: BLE001
        return DIRECTOR_MODEL if _is_embedding_model(preferred) else (preferred or DIRECTOR_MODEL)


def _cut_brief(cut_plan: list[dict[str, Any]], *, max_stretch: float | None = None, fill_method: str | None = None) -> list[dict[str, Any]]:
    """Build the compact CUTS list sent to the Director LLM.

    P1 (story-arc plan): optionally include the per-video Clip Stretch settings so the
    model can intelligently suggest `duration_seconds` (the *pre-stretch* source motion
    length) that will produce the desired final pacing after `fill_clip_to_duration`
    applies k = min(..., max_stretch).
    """
    out = []
    for c in cut_plan:
        item = {
            "index": c["index"],
            "seconds": round(float(c["end_s"]) - float(c["start_s"]), 2),
            "section": c.get("section_label", ""),
            "energy": round(float(c.get("energy", 0.0)), 3),
        }
        if max_stretch is not None:
            item["max_stretch"] = round(float(max_stretch), 2)
        if fill_method:
            item["fill_method"] = fill_method
        out.append(item)
    return out


def _director_options(batch_len: int, *, rich: bool) -> dict:
    """Single source of truth for the Ollama sampling/window options per Director call.

    The original code set only ``temperature`` (no ``num_predict``), so large responses
    truncated against the model's default output budget — the core of the empty-prompts bug.
    We now size ``num_predict`` to the batch and give a modest ``num_ctx`` (kept small to
    respect tight VRAM — the small model runs partly on CPU). The first/rich batch also
    writes the whole-video treatment, so it gets more room.
    """
    bl = max(1, batch_len)
    if rich:
        return {"temperature": 0.7, "num_ctx": 8192, "num_predict": min(4096, 200 * bl + 2048)}
    return {"temperature": 0.65, "num_ctx": 4096, "num_predict": min(3072, 200 * bl + 512)}


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


def _is_mostly_style(text: str, style: str) -> bool:
    """Heuristic: does this text appear to be (mostly) a copy of the global style prompt?
    Used to sanitize LLM outputs that echo the STYLE into a per-shot 'prompt' field,
    and to make the energy guard produce clean "cue, style" (no full style duplicated)
    when the director LLM fails to invent distinct scenes."""
    if not text or not style:
        return False
    t = text.strip()
    s = style.strip()
    if not t or not s:
        return False
    tl = t.lower()
    sl = s.lower()
    if tl == sl:
        return True
    if len(t) > 30 and sl in tl:
        return True
    # High token overlap (handles minor punctuation/added commas)
    twords = set(w for w in tl.split() if len(w) > 2)
    swords = set(w for w in sl.split() if len(w) > 2)
    if swords and len(twords & swords) / max(1, len(swords)) > 0.65:
        return True
    return False


def generate_scene_prompts(
    style_prompt: str,
    cut_plan: list[dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    planning_mode: str = "narrative",
    extra_guidance: str | None = None,
) -> list[str]:
    """One visual prompt per cut, in cut order. Never raises.

    Each returned prompt is the Director's per-cut scene with the global ``style_prompt``
    appended as a suffix (so the look stays consistent while the scene varies). When the
    LLM produces no usable distinct shots we still emit energy-cued variations of the
    style (e.g. "tighter dynamic framing, {style}") — the old "all identical global style
    repeated verbatim" fallback is disabled for the music-video feature.

    planning_mode:
      - "narrative" (default): strong continuity of world/subjects + energy-responsive variation.
      - "visual" or "mood_arc": optimized for abstract / soundtrack / thinking music.
        Emphasizes evolving visual language, recurring motifs/textures/light, energy-driven
        intensity and mood shifts, "visual poem" or "mood arc" progression. Less insistence
        on persistent characters; more on camera energy, palette movement, and pure visuals.
    extra_guidance: free-text instructions appended to the user prompt (e.g. operator feedback
        like "more landscape and light play, slow and dreamy in the intro, sharp strobing at the drop").
    """
    result = _generate_storyline_and_prompts(
        style_prompt, cut_plan, model=model, planning_mode=planning_mode, extra_guidance=extra_guidance
    )
    return result["prompts"]


def _build_rich_user(
    style_prompt: str, cuts_json: str, b: int, n_total: int,
    mode_instruction: str, guidance_block: str, treatment_block: str,
) -> str:
    """The full TASK prompt: treatment + distinct per-cut shots for the first batch."""
    return (
        f"STYLE: {style_prompt}\n\n"
        f"CUTS ({b} of {n_total} total):\n{cuts_json}\n\n"
        f"{mode_instruction}{guidance_block}{treatment_block}\n\n"
        "TASK:\n"
        "1. Produce (or lightly refine) the top-level 'treatment' field using the provided user treatment as the main source. This field can contain the full dreamlike story and artistic directives. The treatment covers the WHOLE video, not just these cuts.\n"
        "2. For the 'shots' array: Create **one completely unique visual prompt for each individual cut** in the CUTS list above.\n"
        "   - The prompts MUST be different from each other.\n"
        "   - **NEVER copy or echo the STYLE text (or large parts of it) into any shots[].prompt** — the STYLE is the global aesthetic and will be appended automatically by the caller after your prompt. Your shots[].prompt must be the *varying part only* (subject appearance, specific framing/composition, camera move, lighting for this exact moment, texture, atmosphere).\n"
        "   - Vary them according to each cut's 'section' label and 'energy' value.\n"
        "   - Low energy cuts (intro/build): calmer, more atmospheric, wider framing, slower implied motion, using the 'loss' and 'searching' visual language from the treatment.\n"
        "   - High energy cuts (drop): more intense, dynamic compositions, tighter framing, stronger contrast, using the 'convergence' and 'intensity' language.\n"
        "   - Keep every prompt short and tight (aim <25 words / one comma phrase) so the full JSON fits in the model output window.\n"
        "   - Every shots[].prompt must be a PURE VISUAL PROMPT ONLY — no character names, no backstory, no plot points. Use only visual descriptors for consistency (e.g. recurring visual motifs like 'fractured silver moonlight on still water').\n"
        "   - Focus exclusively on: subject visuals, setting, framing, camera angle/motion, lighting, color palette, texture, atmosphere, mood as pure image, style elements from the treatment.\n"
        "Use the EXACT 'index' value from each entry in the CUTS list above for the matching shots[].index — DO NOT renumber from zero. Return ONLY the JSON with 'treatment' and 'shots' (one shot per cut, in the exact order of the CUTS list)."
    )


def _build_shots_only_user(
    style_prompt: str, cuts_json: str, b: int, n_total: int,
    mode_instruction: str, guidance_block: str, treatment_block: str,
    treatment_context: str | None,
) -> str:
    """The lean TASK prompt: distinct per-cut shots only, fed the already-written treatment
    as read-only continuity context. Used for every batch after the first and for recovery."""
    tctx = ""
    if treatment_context and treatment_context.strip():
        tctx = (
            "\n\nSTORY TREATMENT (for continuity only — DO NOT output it, DO NOT copy it into the prompts; "
            "use it to keep these shots on the same arc/world as the rest of the video):\n"
            f"{treatment_context.strip()[:1500]}\n"
        )
    return (
        f"STYLE: {style_prompt}\n\n"
        f"CUTS ({b} of {n_total} total):\n{cuts_json}\n\n"
        f"{mode_instruction}{guidance_block}{treatment_block}{tctx}\n\n"
        "TASK: Return ONLY this exact JSON shape (one entry per cut). Use the EXACT 'index' value from each CUTS entry above — DO NOT renumber from zero:\n"
        '{"shots": [{"index": <the cut index from the CUTS list>, "prompt": "pure visual for that cut ..."}, ...]}\n'
        "CRITICAL: Every shots[].prompt MUST be visually DISTINCT from all others AND MUST NOT BE A COPY (or near-copy) OF THE STYLE TEXT. "
        "The STYLE is the global look and will be appended by the system; your shots[].prompt is ONLY the varying subject/framing/lighting/motion/atmosphere specific to THIS cut's energy and place in the arc. "
        "Vary framing, angle, density, motion implication, lighting, and color according to the mode instruction, the cut's energy, and its position in the arc/treatment. "
        "Keep prompts concise (under ~25 words). Pure visual descriptors only (no names, no backstory, no plot)."
    )


def _director_chat(*, ollama, model: str, system: str, user: str, batch_len: int, rich: bool):
    """One ``ollama.chat(format='json')`` with a small transient-error retry loop (connection
    refused / server just came up). Returns ``(parsed_dict, raw_content)``. Raises only if all
    transient retries are exhausted; an unparseable-but-returned response yields empty prompts."""
    import time
    opts = _director_options(batch_len, rich=rich)
    resp = None
    for attempt in range(3):
        try:
            resp = ollama.chat(
                model=model,
                format="json",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                options=opts,
            )
            break
        except Exception as e:  # connection, server busy, etc.
            if attempt == 2:
                raise
            log.info("director ollama.chat attempt %d failed (%s), retrying after short delay", attempt + 1, e)
            time.sleep(1.5 * (attempt + 1))
    content = resp["message"]["content"]
    return _parse_full_director_output(content, batch_len), content


def _generate_shots_for_batch(
    *,
    ollama,
    model: str,
    style_prompt: str,
    cut_subset: list[dict[str, Any]],
    n_total: int,
    mode_instruction: str,
    guidance_block: str,
    treatment_block: str,
    treatment_context: str | None,
    max_stretch: float | None,
    fill_method: str | None,
    rich: bool,
) -> dict:
    """Generate per-shot prompts for ONE batch of cuts.

    Returns ``{"prompts": {global_index: prompt}, "shots": [...], "treatment": str|None,
    "raw_head": str|None}``. Never raises — on any failure returns empty maps so the caller's
    loop continues. Those cuts then degrade to energy-cued style variations while the rest of
    the video keeps its unique LLM prompts (per-batch failure isolation).

    ``rich=True`` runs the full treatment+shots contract (first batch only). ``rich=False`` runs
    the lean shots-only contract, fed the already-produced treatment as continuity context. If a
    batch parses empty, one shots-only recovery call is attempted for the same cuts."""
    b = len(cut_subset)
    if b == 0:
        return {"prompts": {}, "shots": [], "treatment": None, "raw_head": None}
    cuts_json = json.dumps(_cut_brief(cut_subset, max_stretch=max_stretch, fill_method=fill_method))
    try:
        if rich:
            system = _SYSTEM
            user = _build_rich_user(style_prompt, cuts_json, b, n_total, mode_instruction, guidance_block, treatment_block)
        else:
            system = _SHOTS_ONLY_SYSTEM
            user = _build_shots_only_user(style_prompt, cuts_json, b, n_total, mode_instruction, guidance_block, treatment_block, treatment_context)
        data, content = _director_chat(ollama=ollama, model=model, system=system, user=user, batch_len=b, rich=rich)
        prompts_map = data.get("prompts", {}) or {}
        treatment = data.get("treatment")
        shots = data.get("shots", []) or []

        # Recovery: nothing parsed — try once more with the lean shots-only contract.
        if not prompts_map:
            try:
                rec_user = _build_shots_only_user(style_prompt, cuts_json, b, n_total, mode_instruction, guidance_block, treatment_block, treatment_context)
                data2, _ = _director_chat(ollama=ollama, model=model, system=_SHOTS_ONLY_SYSTEM, user=rec_user, batch_len=b, rich=False)
                if data2.get("prompts"):
                    prompts_map = data2.get("prompts", {})
                    treatment = treatment or data2.get("treatment")
                    if data2.get("shots"):
                        shots = data2.get("shots")
            except Exception:  # noqa: BLE001 — recovery is best-effort
                pass

        if not prompts_map:
            return {"prompts": {}, "shots": [], "treatment": treatment, "raw_head": (content or "")[:600].replace("\n", "\\n")}
        return {"prompts": prompts_map, "shots": shots, "treatment": treatment, "raw_head": None}
    except Exception as e:  # noqa: BLE001 — one batch must never sink the whole director
        log.info("director batch (%d cuts, rich=%s) failed (%s); those cuts will use energy cues", b, rich, e)
        return {"prompts": {}, "shots": [], "treatment": None, "raw_head": f"(batch error: {str(e)[:200]})"}


def _generate_storyline_and_prompts(
    style_prompt: str,
    cut_plan: list[dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    planning_mode: str = "narrative",
    extra_guidance: str | None = None,
    user_treatment: str | None = None,
    max_stretch: float | None = None,
    fill_method: str | None = None,
) -> dict:
    """Internal: returns {'prompts': list[str], 'storyline': str | None}.
    The storyline is the actual narrative arc the model invented for this video.
    """
    n = len(cut_plan)
    if n == 0:
        return {"prompts": [], "storyline": None}
    fallback_prompts = [style_prompt] * n

    # Build mode-specific instructions (appended to the base system guidance)
    mode = (planning_mode or "narrative").lower()
    if mode in ("visual", "mood", "mood_arc", "abstract", "visual_mood_arc"):
        mode_instruction = (
            "PLANNING MODE: VISUAL / MOOD ARC. This is primarily abstract, textural, or "
            "soundtrack-driven music where literal story/characters matter less than visual "
            "progression and feeling. Focus on: evolving visual motifs and recurring textures "
            "or light phenomena; strong energy-driven shifts in density, speed, color temperature, "
            "and camera language (slow floating vs. pulsing handheld vs. vertigo moves); "
            "a clear mood arc across the sections that mirrors the energy contour without needing "
            "a single consistent 'subject'. Treat the sequence like a visual tone poem or abstract "
            "film. Still maintain overall stylistic cohesion from the global STYLE."
        )
    else:
        mode_instruction = (
            "PLANNING MODE: NARRATIVE CONTINUITY. Maintain a coherent world, recurring subjects "
            "or characters (described via the style), locations, and palette across cuts. Vary "
            "specific shots for visual interest while preserving the sense of one continuous scene "
            "or story world."
        )

    guidance_block = ""
    if extra_guidance and extra_guidance.strip():
        guidance_block = f"\n\nOPERATOR GUIDANCE / FEEDBACK:\n{extra_guidance.strip()}\nApply this direction when shaping the visual progression and specific shot choices."

    treatment_block = ""
    if user_treatment and user_treatment.strip():
        treatment_block = (
            f"\n\nUSER-PROVIDED VISUAL TREATMENT / STORY (this is the authoritative screenplay. "
            f"Use its visual language, motifs, atmosphere, and emotional arc as the source of truth. "
            f"Map its beats and progression to the specific song sections and energy values in the CUTS list below):\n"
            f"{user_treatment.strip()}\n\n"
            "Respect this treatment exactly. Do not invent a different story. Adapt the pacing and visuals to the CUTS. "
            "The treatment may contain narrative elements — those belong ONLY in the top-level treatment field."
        )

    try:
        import ollama
        model = _resolve_model(model)
        # Batch the cuts: a small model can't emit all N shots as one valid JSON object once N
        # gets large (it truncates → empty prompts → mechanical fallback). Each batch is a
        # separate, small-enough ollama.chat. The first (rich) batch writes the whole-video
        # treatment; later (shots-only) batches reuse it as continuity context.
        batches = [cut_plan[i:i + DIRECTOR_BATCH_SIZE] for i in range(0, n, DIRECTOR_BATCH_SIZE)]
        log.info(
            "music video director using ollama model=%s for %d cuts in %d batch(es) of <=%d (has_user_treatment=%s)",
            model, n, len(batches), DIRECTOR_BATCH_SIZE, bool(user_treatment),
        )

        prompts_map: dict[int, str] = {}
        merged_shots: list = []
        treatment: str | None = None
        last_head: str | None = None
        failed_batches = 0

        for bi, batch in enumerate(batches):
            res = _generate_shots_for_batch(
                ollama=ollama,
                model=model,
                style_prompt=style_prompt,
                cut_subset=batch,
                n_total=n,
                mode_instruction=mode_instruction,
                guidance_block=guidance_block,
                treatment_block=treatment_block,
                treatment_context=treatment,
                max_stretch=max_stretch,
                fill_method=fill_method,
                rich=(bi == 0),
            )
            if bi == 0 and res.get("treatment"):
                treatment = res.get("treatment")
            batch_prompts = res.get("prompts") or {}
            if batch_prompts:
                prompts_map.update(batch_prompts)
                merged_shots.extend(res.get("shots") or [])
            else:
                failed_batches += 1
                if res.get("raw_head"):
                    last_head = res.get("raw_head")

        # Total failure: not a single usable prompt across all batches → energy-cued variations
        # for the whole video (the old "all identical global style" fallback stays disabled).
        if not prompts_map:
            head = last_head or "(no usable model output across all batches)"
            log.warning(
                "director returned no usable prompts across %d batch(es); using energy-cued variations "
                "of global style for all %d cuts. raw_head=%s",
                len(batches), n, head,
            )
            cued = fallback_prompts
            try:
                cued = _ensure_distinct_and_energy_aware(
                    fallback_prompts, cut_plan, style_prompt, max_stretch=max_stretch
                )
            except Exception:  # noqa: BLE001
                pass
            return {
                "prompts": cued,
                "treatment": treatment,
                "shots": [],
                "director_diagnostics": {
                    "reason": "empty_prompts_map",
                    "raw_head": head,
                    "note": "LLM produced no usable shots across all batches; energy cues applied for variation",
                },
            }

        out: list[str] = []
        for c in cut_plan:
            scene = prompts_map.get(c["index"])
            # Sanitize: if the model echoed the global STYLE (or mostly the style) into the
            # per-shot prompt, treat it as empty variation so we don't do "style, style" on combine.
            if scene and _is_mostly_style(scene, style_prompt):
                scene = None
            out.append(f"{scene}, {style_prompt}" if scene else style_prompt)

        # P0 guard (per approved story-arc plan): guarantee distinctness + energy responsiveness
        # + consistent style suffix, even if the LLM produced near-duplicates or weak variation.
        # This also differentiates any cuts whose batch failed (they came in as bare style above).
        # On any internal failure it is a safe no-op (never produces worse/less distinct output).
        try:
            out = _ensure_distinct_and_energy_aware(
                out, cut_plan, style_prompt,
                max_stretch=None  # caller can pass from settings if desired; injected descriptors only
            )
        except Exception:  # noqa: BLE001 — guard must never break the Director
            log.warning("director post-distinctness guard failed (safe no-op); using raw LLM output")

        # P2: lightly inject arc/motif context from the treatment so per-shot prompts (used
        # for storyboards and as i2v text conditioning) visibly illustrate progression
        # through the story/mood arc, not just distinct variations on the global style.
        # Keeps prompts "pure visual" by prefixing descriptive position-in-arc language
        # drawn from the treatment (first sentence as motif) + index/energy.
        if treatment:
            try:
                out = _augment_prompts_with_arc(out, cut_plan, treatment)
            except Exception:  # noqa: BLE001
                log.warning("director arc injection failed (safe no-op)")

        if treatment:
            log.info("director produced visual treatment for music video (len=%d): %s",
                     len(treatment), treatment[:300])

        result = {"prompts": out, "treatment": treatment, "shots": merged_shots}
        # Partial failure: some batches degraded to energy cues; surface it for the UI/diagnostics
        # so a half-mechanical result is visible rather than silently passing as a clean run.
        if failed_batches:
            result["director_diagnostics"] = {
                "reason": "partial_batches",
                "raw_head": last_head or "",
                "note": (
                    f"{len(prompts_map)}/{n} cuts got unique LLM prompts; "
                    f"{failed_batches} of {len(batches)} batch(es) fell back to energy cues"
                ),
            }
        return result
    except Exception as e:  # noqa: BLE001 — director is best-effort; never sink the analyze stage
        log.warning("director failed (%s); producing energy-cued style variations (plain identical global fallback disabled)", e)
        cued = fallback_prompts
        try:
            cued = _ensure_distinct_and_energy_aware(
                fallback_prompts, cut_plan, style_prompt, max_stretch=max_stretch
            )
        except Exception:  # noqa: BLE001
            pass
        err = str(e)[:300]
        return {
            "prompts": cued,
            "storyline": None,
            "director_diagnostics": {
                "reason": "llm_exception",
                "error": err,
                "note": "energy-cued variations applied",
                "raw_head": f"(no model reply — {err})",
            },
        }


def _parse_full_director_output(content: str, n: int) -> dict:
    """Tolerant parser that extracts the rich 'treatment' (or legacy 'storyline') plus per-shot prompts.
    Returns {'treatment': str|None, 'prompts': {index: prompt}, 'shots': list}.
    Hardened for small models that may: wrap in fences/prose, use alternate keys (visual_prompt etc),
    emit string indexes, use top-level list or 'cuts'/'scenes'/'plan' instead of 'shots', or return
    slightly malformed but salvageable JSON."""
    if not content or not isinstance(content, str):
        return {"treatment": None, "prompts": {}, "shots": []}

    # Strip common markdown fences / prose wrappers so the {..} extract sees real JSON first.
    c = content.strip()
    # Remove leading ```json or ``` and trailing ```
    c = re.sub(r'^```(?:json)?\s*', '', c, flags=re.IGNORECASE)
    c = re.sub(r'\s*```$', '', c)
    c = c.strip()

    data = None
    for candidate in (c, content):  # try cleaned then original
        try:
            data = json.loads(candidate)
            break
        except (ValueError, TypeError):
            pass
    if data is None:
        # Last-chance: locate the largest plausible {...} block
        start, end = c.find("{"), c.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(c[start:end + 1])
            except (ValueError, TypeError):
                pass
    if data is None:
        # Try the legacy tolerant helper (handles some bare-list cases)
        return {"treatment": None, "prompts": _parse_prompts(content, n), "shots": []}

    result = {"treatment": None, "prompts": {}, "shots": []}

    # If the whole thing is a list, treat it as the shots array (some models do this).
    if isinstance(data, list):
        data = {"shots": data}

    if isinstance(data, dict):
        treatment = data.get("treatment") or data.get("storyline")
        if isinstance(treatment, str) and treatment.strip():
            result["treatment"] = treatment.strip()

        # Accept several possible names for the per-cut list
        shots = None
        for key in ("shots", "cuts", "scenes", "plan", "shot_plans", "clips"):
            val = data.get(key)
            if isinstance(val, list):
                shots = val
                break
        if not shots and isinstance(data.get("shots"), list):
            shots = data.get("shots")

        if shots:
            for i, item in enumerate(shots):
                if not isinstance(item, dict):
                    continue
                idx = item.get("index", i)
                if isinstance(idx, str):
                    try:
                        idx = int(idx.strip())
                    except (ValueError, TypeError):
                        idx = i
                if isinstance(idx, (int, float)):
                    idx = int(idx)

                # Try many possible prompt field names small models might emit
                prompt = None
                for pk in ("prompt", "description", "visual_prompt", "image_prompt", "text",
                           "caption", "visual", "scene_prompt", "image", "shot"):
                    p = item.get(pk)
                    if isinstance(p, str) and p.strip():
                        prompt = p.strip()
                        break
                if isinstance(idx, int) and isinstance(prompt, str) and prompt.strip():
                    result["prompts"][idx] = prompt.strip()
                    shot_plan = {
                        "index": idx,
                        "prompt": prompt.strip(),
                        "duration_seconds": item.get("duration_seconds"),
                        "transition_to_next": item.get("transition_to_next"),
                        "filter_preset": item.get("filter_preset"),
                    }
                    result["shots"].append(shot_plan)

    # Final legacy fallback (covers bare list at top level etc.)
    if not result["prompts"]:
        result["prompts"] = _parse_prompts(content, n)

    # P2 strengthening: cardinality/index enforcement. If the model (esp. in recovery)
    # returned wrong number of shots, *keep the partial* (for large N the model often
    # only emits some). Missing indices will receive a style+energy-cue from the builder/guard.
    # We no longer zero the map (that forced full identical global fallback, now disabled).
    if len(result["prompts"]) != n:
        log.warning(
            "director parse returned %d prompts for %d cuts (cardinality mismatch); "
            "keeping partial — missing cuts get style + energy cue (plain global identical fallback disabled)",
            len(result["prompts"]), n
        )
        # keep partial prompts; caller/ensure will differentiate the missing ones.
        # Do NOT clear result["prompts"]

    return result


def _ensure_distinct_and_energy_aware(
    prompts: list[str], cut_plan: list[dict[str, Any]], style_prompt: str, *, max_stretch: float | None = None
) -> list[str]:
    """P0 post-processing guard (story-arc plan): ensure per-cut prompts are textually distinct,
    carry an energy/section-appropriate visual cue, and consistently include the global style suffix.

    This is a cheap, deterministic safety net that runs after the LLM (primary or recovery).
    It never calls the model. On any internal error it returns the input unchanged (safe no-op).

    - Distinctness: strip the common style suffix and compare prefixes. If too many are identical,
      inject a light, style-preserving variation based on the cut's energy and section.
    - Energy cue injection: for low-energy (intro/build) use calmer/wider/slower/atmospheric language;
      for high-energy (drop) use tighter/dynamic/denser/contrasty language. Drawn from the Director
      system prompt contract so it stays consistent with what the model was asked to do.
    - Style suffix: every entry ends with ", {style_prompt}" (the UI and i2v expect the global look).
    - max_stretch (optional): currently only for future-proofing / diagnostics; not required for the
      core distinctness logic.

    Returns the (possibly lightly rewritten) list of ready-to-use prompts, in cut order.
    """
    if not prompts or not cut_plan or len(prompts) != len(cut_plan):
        return prompts

    # 1. Strip style suffix for comparison (the suffix is what makes many "look the same" in logs/UI).
    style_suffix = f", {style_prompt}" if not style_prompt.startswith(",") else style_prompt
    stripped = []
    for p in prompts:
        if p.endswith(style_suffix):
            stripped.append(p[: -len(style_suffix)].strip())
        else:
            stripped.append(p.strip())

    # 2. Detect duplicates (by exact stripped text or very similar prefix).
    from collections import Counter
    counts = Counter(stripped)
    num_unique = len(counts)
    threshold = max(2, len(prompts) // 2)
    needs_fix = num_unique < threshold

    # 3. Energy-based injection vocabulary (kept tiny and style-agnostic so it composes cleanly).
    low_energy_cues = ["wider calmer framing", "slow atmospheric drift", "soft diffuse light", "sparse open composition"]
    high_energy_cues = ["tighter dynamic framing", "sharp pulsing motion", "high contrast strobing light", "dense layered composition"]

    out: list[str] = []
    for i, (orig, stripped_p, cut) in enumerate(zip(prompts, stripped, cut_plan)):
        energy = float(cut.get("energy", 0.5))
        section = str(cut.get("section", "")).lower()
        is_low = energy < 0.4 or any(k in section for k in ("intro", "build", "outro", "verse"))
        is_high = energy > 0.7 or any(k in section for k in ("drop", "chorus", "peak", "bridge"))

        base = stripped_p
        if needs_fix:
            # Pick a cue that is unlikely to already be in the prompt (cheap string check).
            # Use index rotation so that many consecutive same-energy cuts (common) still
            # get *different* cue phrases instead of all receiving the first one.
            cues = low_energy_cues if is_low else (high_energy_cues if is_high else [])
            cue = None
            if cues:
                # rotate by cut index for variety even within same energy band
                for off in range(len(cues)):
                    c = cues[(i + off) % len(cues)]
                    if c not in (base or "").lower():
                        cue = c
                        break
                if cue is None:
                    cue = cues[i % len(cues)]
            if cue:
                # If the stripped base is (or was) the global style itself (LLM echoed it, or
                # this is the plain-style list from the no-usable director path), do NOT
                # append cue to the long style text (that produces dups like "style, cue, style").
                # Instead use *just* the cue as the varying part; the suffix adder below will
                # produce the clean "cue, style" form. This disables duplicating fallback.
                if not base or _is_mostly_style(base, style_prompt):
                    base = cue
                else:
                    # Inject near the end, before any trailing style (we'll re-add the suffix).
                    base = f"{base.rstrip(', ')}, {cue}"

        # Guarantee style suffix (some recovery paths or manual edits may have dropped it).
        # Use the style text itself for endswith (handles inputs that were plain style).
        style_core = style_suffix.lstrip(", ")
        if not base.endswith(style_core):
            base = f"{base.rstrip(', ')}{style_suffix}"

        out.append(base)

    # 4. Lightweight diagnostic if we had to intervene.
    if needs_fix:
        log.info(
            "director post-guard injected energy cues for %d/%d cuts (unique before=%d)",
            len(out) - num_unique, len(out), num_unique
        )

    return out


def _augment_prompts_with_arc(prompts: list[str], cut_plan: list[dict[str, Any]], treatment: str) -> list[str]:
    """P2: lightly prefix each (pure visual) prompt with position-in-arc language drawn from
    the treatment. This makes the storyboard stills and i2v clips visibly illustrate
    progression through the single cohesive story/mood arc, rather than just being
    distinct variations on the global style.

    Example output prefix: "opening moment in the fractured silver moonlight arc: [visual prompt]"
    Keeps prompts suitable for image generators (no plot/names in the visual part).
    """
    if not treatment or not prompts:
        return prompts
    # Derive a short motif from the treatment (first sentence or 60 chars).
    motif = treatment.split(".")[0][:60].strip() or "the visual arc"
    n = len(prompts)
    positions = ["opening", "rising", "peak", "resolution", "closing"]
    out = []
    for i, p in enumerate(prompts):
        pos = positions[min(i, len(positions)-1)]
        frac = (i + 1) / max(1, n)
        # Light augmentation only if not already present; keeps "pure visual".
        if "arc:" not in p.lower() and "moment in" not in p.lower() and len(p) > 5:
            p = f"{pos} moment in {motif} (energy {frac:.1f}): {p}"
        out.append(p)
    return out
