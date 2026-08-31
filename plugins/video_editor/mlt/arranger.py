"""Arranger — combines clip analysis + song structure + kept-ranges into an
ordered Arrangement that the MLT writer can render.

A1 implementation: section-by-section selection, biased by recipe filter
palette (so transitions/filters stay within the chosen aesthetic even before
A3 wires up real vision-model recommendations). Reproducible with a seed.

A3 will replace the random-from-eligible selection with a scoring function
over `ClipAnalysis.best_section_fit` and StyleRecipe biases.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Optional

from service.crew_interface import (
    ArrangedClip,
    Arrangement,
    ClipAnalysis,
    SongAnalysis,
)

logger = logging.getLogger(__name__)


def arrange_from_analysis(
    clip_analyses: list[ClipAnalysis],
    song: SongAnalysis,
    kept_ranges_by_clip: dict[str, list[tuple[float, float]]],
    recipe: Optional[dict[str, Any]] = None,
    seed: int = 0,
    respect_bin_order: bool = False,
    customer_context: Optional[dict[str, Any]] = None,
    caption_defaults: Optional[dict[str, Any]] = None,
) -> Arrangement:
    """Section-by-section: pick a clip + kept range for each song section.

    respect_bin_order: when True, assign clips to sections in the order they
    appear in the bin (cycling if there are more sections than clips) instead
    of the section-scoring pick.

    customer_context: optional dict {name, intro, brand, products, usps} used to
    generate a per-clip on-screen caption (assigned to ArrangedClip.caption).

    caption_defaults: optional dict {text, color, size, bgcolor, halign, valign}
    applied to EVERY clip after caption generation. `text` (if set) overrides the
    auto-generated caption on every clip; the style fields set the per-clip
    caption style so the user doesn't have to edit each caption individually.
    """
    rng = random.Random(seed)

    eligible = _eligible_clip_ids(clip_analyses, kept_ranges_by_clip)
    if not eligible:
        return Arrangement(clips=[], style_recipe_name=_recipe_name(recipe), seed=seed)

    analysis_by_id = {a.clip_id: a for a in clip_analyses}
    # Bin order = the order the clips were submitted (buildPlanRequest sends
    # bin_clips in bin order), filtered to eligible clips.
    bin_order = [a.clip_id for a in clip_analyses if a.clip_id in eligible]

    if respect_bin_order:
        # Use EVERY clip in bin order, each playing for its kept-range duration.
        # The total is the sum of the clip durations; the renderer loops the
        # soundtrack to cover it. The director doesn't pick — all bin clips play.
        arranged = _arrange_all_clips_in_bin_order(
            clip_analyses, kept_ranges_by_clip, rng
        )
    else:
        sections = song.sections or _fallback_single_section(song)
        arranged = _arrange_by_sections(
            sections=(
                [s for s in sections if _section_duration(s) > 0]
            ),
            eligible=eligible,
            analysis_by_id=analysis_by_id,
            kept_ranges_by_clip=kept_ranges_by_clip,
            recipe=recipe,
            rng=rng,
        )

    # Assign per-clip captions from the customer context (cycled across clips).
    caption_lines = _build_caption_lines(customer_context)
    if caption_lines:
        for idx, clip in enumerate(arranged):
            clip.caption = caption_lines[idx % len(caption_lines)]

    # Apply global caption defaults (text override + style) to every clip.
    if caption_defaults:
        _apply_caption_defaults(arranged, caption_defaults)

    return Arrangement(
        clips=arranged,
        style_recipe_name=_recipe_name(recipe),
        seed=seed,
    )


def _section_duration(section: Any) -> float:
    start = float(section["start"]) if isinstance(section, dict) else section.start
    end = float(section["end"]) if isinstance(section, dict) else section.end
    return end - start


def _arrange_by_sections(
    *,
    sections: list[Any],
    eligible: list[str],
    analysis_by_id: dict[str, ClipAnalysis],
    kept_ranges_by_clip: dict[str, list[tuple[float, float]]],
    recipe: Optional[dict[str, Any]],
    rng: random.Random,
) -> list[ArrangedClip]:
    """Section-driven arrangement: pick a clip for each song section."""
    arranged: list[ArrangedClip] = []
    for i, section in enumerate(sections):
        section_label = _section_label(section)
        section_start = float(section["start"]) if isinstance(section, dict) else section.start
        section_end = float(section["end"]) if isinstance(section, dict) else section.end
        section_duration = section_end - section_start
        if section_duration <= 0:
            continue

        clip_id = _pick_clip_for_section(
            eligible_ids=eligible,
            analysis_by_id=analysis_by_id,
            kept_ranges=kept_ranges_by_clip,
            section_label=section_label,
            section_duration=section_duration,
            recipe=recipe,
            rng=rng,
        )
        if clip_id is None:
            continue

        analysis = analysis_by_id[clip_id]
        source_in, source_out = _pick_kept_range(
            kept_ranges_by_clip[clip_id], section_duration, rng
        )

        arranged.append(
            ArrangedClip(
                clip_id=clip_id,
                source_path=analysis.source_path,
                section_label=section_label,
                timeline_start=section_start,
                timeline_end=section_end,
                source_in=source_in,
                source_out=source_out,
                filter_preset=_resolve_filter(analysis, recipe),
                transition_to_next=_resolve_transition(i, sections, recipe, rng),
            )
        )
    return arranged


def _arrange_all_clips_in_bin_order(
    clip_analyses: list[ClipAnalysis],
    kept_ranges_by_clip: dict[str, list[tuple[float, float]]],
    rng: random.Random,
) -> list[ArrangedClip]:
    """Use EVERY clip in bin order, each playing for its kept-range duration.

    The total arrangement length is the sum of the clip durations; the renderer
    loops the soundtrack to cover it. The director doesn't pick — all bin clips
    play, in the order they appear in the bin.
    """
    arranged: list[ArrangedClip] = []
    cursor = 0.0
    for analysis in clip_analyses:
        ranges = kept_ranges_by_clip.get(analysis.clip_id) or []
        if not ranges:
            continue
        start, end = rng.choice(ranges)
        duration = end - start
        if duration <= 0:
            continue
        arranged.append(
            ArrangedClip(
                clip_id=analysis.clip_id,
                source_path=analysis.source_path,
                section_label="bin",
                timeline_start=cursor,
                timeline_end=cursor + duration,
                source_in=start,
                source_out=end,
                filter_preset="none",
                transition_to_next="hard-cut",
            )
        )
        cursor += duration
    return arranged


def _apply_caption_defaults(
    arranged: list[ArrangedClip],
    defaults: dict[str, Any],
) -> None:
    """Apply a single set of caption defaults to every arranged clip.

    `text` (if non-empty) overrides the per-clip caption on every clip; the
    style fields set the per-clip caption style so the user doesn't have to
    edit each caption individually.
    """
    text = (defaults.get("text") or "").strip()
    color = (defaults.get("color") or "").strip()
    bgcolor = (defaults.get("bgcolor") or "").strip()
    size = defaults.get("size")
    halign = (defaults.get("halign") or "").strip()
    valign = (defaults.get("valign") or "").strip()

    for clip in arranged:
        if text:
            clip.caption = text
        if color:
            clip.caption_color = color
        if bgcolor:
            clip.caption_bgcolor = bgcolor
        if size:
            try:
                clip.caption_size = int(size)
            except (TypeError, ValueError):
                pass
        if halign:
            clip.caption_halign = halign
        if valign:
            clip.caption_valign = valign


def _build_caption_lines(ctx: Optional[dict[str, Any]]) -> list[str]:
    """Turn a customer context dict into a list of on-screen caption lines."""
    if not ctx:
        return []
    lines: list[str] = []
    name = (ctx.get("name") or "").strip()
    intro = (ctx.get("intro") or ctx.get("notes") or "").strip()
    brand = (ctx.get("brand") or "").strip()
    products = [p for p in (ctx.get("products") or []) if str(p).strip()]
    usps = [u for u in (ctx.get("usps") or []) if str(u).strip()]
    if name:
        lines.append(f"Introducing {name}")
    if intro:
        lines.append(intro)
    if brand:
        lines.append(brand)
    lines.extend(products)
    lines.extend(usps)
    return lines


# ---------- helpers ---------------------------------------------------------


def _eligible_clip_ids(
    analyses: list[ClipAnalysis],
    kept: dict[str, list[tuple[float, float]]],
) -> list[str]:
    """Clips need (a) an analysis entry and (b) at least one kept range."""
    return [a.clip_id for a in analyses if kept.get(a.clip_id)]


def _pick_clip_for_section(
    *,
    eligible_ids: list[str],
    analysis_by_id: dict[str, ClipAnalysis],
    kept_ranges: dict[str, list[tuple[float, float]]],
    section_label: str,
    section_duration: float,
    recipe: Optional[dict[str, Any]],
    rng: random.Random,
) -> Optional[str]:
    """Score-then-pick. A1 score = 'has a kept range long enough' + recipe-bias bonus.
    A3 will add ClipAnalysis-based scoring (best_section_fit, energy match)."""
    long_enough = [
        cid for cid in eligible_ids
        if any((end - start) >= min(section_duration, 0.5) for start, end in kept_ranges[cid])
    ]
    pool = long_enough or eligible_ids[:]
    if not pool:
        return None

    # Recipe bias: in A1 we don't yet have rich ClipAnalysis fields, so this is
    # a no-op for now. In A3 we'll prefer clips whose analysis.best_section_fit
    # contains `section_label` and whose subject/energy match recipe.prefer_*.
    scored = [(_score_clip_for_section(analysis_by_id[cid], section_label, recipe), cid) for cid in pool]
    rng.shuffle(scored)  # randomize among ties
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


def _score_clip_for_section(
    analysis: ClipAnalysis,
    section_label: str,
    recipe: Optional[dict[str, Any]],
) -> float:
    score = 0.0
    if section_label in analysis.best_section_fit or "any" in analysis.best_section_fit:
        score += 1.0
    if recipe:
        if analysis.subject in (recipe.get("prefer_subjects") or []):
            score += 0.5
        if analysis.energy in (recipe.get("prefer_energy") or []):
            score += 0.5
        if analysis.motion in (recipe.get("prefer_motion") or []):
            score += 0.5
    return score


def _pick_kept_range(
    ranges: list[tuple[float, float]],
    section_duration: float,
    rng: random.Random,
) -> tuple[float, float]:
    """Pick a kept range, then a start within it that leaves enough footage."""
    candidates = [(s, e) for s, e in ranges if (e - s) >= min(section_duration, 0.5)]
    pool = candidates or ranges
    start, end = rng.choice(pool)
    duration_available = end - start
    if duration_available <= section_duration:
        return (start, start + duration_available)
    # Slide the section_duration window randomly inside this kept range.
    max_offset = duration_available - section_duration
    offset = rng.uniform(0.0, max_offset)
    return (start + offset, start + offset + section_duration)


def _resolve_filter(
    analysis: ClipAnalysis,
    recipe: Optional[dict[str, Any]],
) -> str:
    """A1: respect recipe.filter_palette if present; otherwise use clip's recommended."""
    candidate = analysis.recommended_filter or "none"
    if recipe:
        palette = recipe.get("filter_palette") or []
        if palette and candidate not in palette and candidate != "none":
            # Out-of-palette recommendation — fall back to first palette entry.
            return palette[0]
    return candidate


def _resolve_transition(
    section_index: int,
    sections: list[Any],
    recipe: Optional[dict[str, Any]],
    rng: random.Random,
) -> str:
    """A1: hard-cut. A3 may pick based on adjacent-section energy delta."""
    if section_index >= len(sections) - 1:
        return "hard-cut"  # last clip has no following transition
    if recipe and recipe.get("transition_palette"):
        return rng.choice(recipe["transition_palette"])
    return "hard-cut"


def _section_label(section: Any) -> str:
    if isinstance(section, dict):
        return str(section.get("label", "unlabeled"))
    return getattr(section, "label", "unlabeled")


def _fallback_single_section(song: SongAnalysis) -> list[dict[str, Any]]:
    """If the song analysis didn't produce sections, treat the whole song as one."""
    return [{"label": "drop", "start": 0.0, "end": song.duration_seconds}]


def _recipe_name(recipe: Optional[dict[str, Any]]) -> str:
    if recipe and "name" in recipe:
        return str(recipe["name"])
    return "default"
