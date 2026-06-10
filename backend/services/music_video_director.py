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

DEFAULT_MODEL = "gemma4:e4b"

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
    planning_mode: str = "narrative",
    extra_guidance: str | None = None,
) -> list[str]:
    """One visual prompt per cut, in cut order. Never raises.

    Each returned prompt is the Director's per-cut scene with the global ``style_prompt``
    appended as a suffix (so the look stays consistent while the scene varies). On any
    failure every entry is just ``style_prompt`` — today's behavior, no regression.

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


def _generate_storyline_and_prompts(
    style_prompt: str,
    cut_plan: list[dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    planning_mode: str = "narrative",
    extra_guidance: str | None = None,
    user_treatment: str | None = None,
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
        log.info("music video director using ollama model=%s for %d cuts (has_user_treatment=%s)", model, n, bool(user_treatment))
        user = (
            f"STYLE: {style_prompt}\n\n"
            f"CUTS ({n} total):\n{json.dumps(_cut_brief(cut_plan))}\n\n"
            f"{mode_instruction}{guidance_block}{treatment_block}\n\n"
            "TASK:\n"
            "1. Produce (or lightly refine) the top-level 'treatment' field using the provided user treatment as the main source. This field can contain the full dreamlike story and artistic directives.\n"
            "2. For the 'shots' array: Create **one completely unique visual prompt for each individual cut** in the CUTS list above.\n"
            "   - The prompts MUST be different from each other.\n"
            "   - Vary them according to each cut's 'section' label and 'energy' value.\n"
            "   - Low energy cuts (intro/build): calmer, more atmospheric, wider framing, slower implied motion, using the 'loss' and 'searching' visual language from the treatment.\n"
            "   - High energy cuts (drop): more intense, dynamic compositions, tighter framing, stronger contrast, using the 'convergence' and 'intensity' language.\n"
            "   - Every shots[].prompt must be a PURE VISUAL PROMPT ONLY — no character names, no backstory, no plot points. Use only visual descriptors for consistency (e.g. recurring visual motifs like 'fractured silver moonlight on still water').\n"
            "   - Focus exclusively on: subject visuals, setting, framing, camera angle/motion, lighting, color palette, texture, atmosphere, mood as pure image, style elements from the treatment.\n"
            "Return ONLY the JSON with 'treatment' and 'shots' (one shot per cut, in the exact order of the CUTS list). Indexes must match the CUTS exactly."
        )
        # Small retry for transient "ollama not ready" after plugin ensure/start (e.g. daemon just came up)
        resp = None
        for attempt in range(3):
            try:
                resp = ollama.chat(
                    model=model,
                    format="json",
                    messages=[
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": user},
                    ],
                    options={"temperature": 0.7},
                )
                break
            except Exception as e:  # connection, server busy, etc.
                if attempt == 2:
                    raise
                log.info("director ollama.chat attempt %d failed (%s), retrying after short delay", attempt+1, e)
                import time
                time.sleep(1.5 * (attempt + 1))
        content = resp["message"]["content"]
        data = _parse_full_director_output(content, n)
        prompts_map = data.get("prompts", {}) or {}
        treatment = data.get("treatment")

        # If the (possibly rich) first response gave us nothing usable, try a second,
        # much simpler "shots only" call. Small models often choke on the full treatment +
        # CUTS + editing fields + strict separation all in one go. The recovery prompt
        # has a tiny contract focused purely on distinct visual prompts.
        if not prompts_map:
            try:
                simple_sys = (
                    "You are a precise visual prompt generator. Output ONLY valid JSON. "
                    "No prose, no fences, no extra keys."
                )
                simple_user = (
                    f"STYLE: {style_prompt}\n\n"
                    f"CUTS ({n} total):\n{json.dumps(_cut_brief(cut_plan))}\n\n"
                    f"{treatment_block}\n\n"
                    "TASK: Return ONLY this JSON shape (one entry per cut, indexes exact):\n"
                    '{"shots": [{"index": 0, "prompt": "pure visual for cut 0 (subject, framing, camera, lighting, color, texture, atmosphere; no names, no plot)"}, ...]}\n'
                    "Make every prompt DISTINCT. Vary framing, motion implication, lighting, or density by the cut's energy and section. Pure visual descriptors only."
                )
                resp2 = ollama.chat(
                    model=model,
                    format="json",
                    messages=[
                        {"role": "system", "content": simple_sys},
                        {"role": "user", "content": simple_user},
                    ],
                    options={"temperature": 0.65},
                )
                content2 = resp2["message"]["content"]
                data2 = _parse_full_director_output(content2, n)
                if data2.get("prompts"):
                    prompts_map = data2.get("prompts", {})
                    treatment = treatment or data2.get("treatment")
                    # Prefer recovery shots (they carry duration/transition/filter if the simple call produced them)
                    if data2.get("shots"):
                        data["shots"] = data2.get("shots")
            except Exception:  # noqa: BLE001
                pass  # recovery failed; we will fallback below

        if not prompts_map:
            # Diagnostic: show a safe prefix of what the model actually emitted so we can see
            # structure mistakes (fences, wrong keys, empty prompts, prose, etc.) without dumping
            # potentially long user treatment or full PII.
            head = (content or "")[:1200].replace("\n", "\\n")
            log.warning(
                "director returned no usable prompts; using global style for all %d cuts. "
                "raw_head=%s",
                n, head
            )
            return {"prompts": fallback_prompts, "treatment": treatment, "shots": []}

        out: list[str] = []
        for c in cut_plan:
            scene = prompts_map.get(c["index"])
            out.append(f"{scene}, {style_prompt}" if scene else style_prompt)

        if treatment:
            log.info("director produced visual treatment for music video (len=%d): %s", 
                     len(treatment), treatment[:300])

        return {"prompts": out, "treatment": treatment, "shots": data.get("shots", []) or []}
    except Exception as e:  # noqa: BLE001 — director is best-effort; never sink the analyze stage
        log.warning("director failed (%s); falling back to global style prompt", e)
        return {"prompts": fallback_prompts, "storyline": None}


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

    return result
