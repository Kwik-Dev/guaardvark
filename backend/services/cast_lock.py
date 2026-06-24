"""Shared character identity-lock helper — the single source of truth for how a trained
cast member's LoRA + trigger + bible are applied to a generation prompt.

Three video features independently grew the same logic:
  * music-video  (music_video_tasks._keyframe_loras_and_prompt) — trigger + bible + strength;
  * film-crew    (production_swarm_tasks._shot_loras_and_prompt)  — trigger only;
  * batch        — none (bare lora_name passthrough).

This module unifies that. **Injection stays at RENDER time on purpose** — that is strictly more
robust than injecting at plan/storyboard time:
  - it survives a user editing a clip/shot prompt afterward (the trigger can never be lost);
  - it is naturally PER-SHOT scoped, so a multi-character production only locks the characters
    actually present in a given shot (a global plan-time cast would over-lock every shot).
The Director already receives a "do NOT describe the hero's face" guidance note, so plan-time
injection's only real benefit is already captured without giving up render-time's guarantees.

The pieces:
  * ``resolve_lora_strength(model, override)`` — model-aware default strength (FLUX-dev ~0.9,
    SDXL rank-16 ~0.25; verified on sage_harlow 2026-06-22), operator override wins. (Extracted
    from music_video_tasks._keyframe_lora_strength per the long-standing #1489 cleanup.)
  * ``subjects_to_lock(subjects, include_bible)`` — Subject objects → (deduped lora_paths,
    "trigger, bible, …" prefix).
  * ``apply_lock(base_prompt, lock_prefix)`` — front-load the lock onto a prompt.
"""

from __future__ import annotations

from typing import Iterable, Optional

# Model-aware default keyframe/storyboard LoRA strength. SDXL rank-16 character LoRAs sit at
# ~0.25 (higher fries them); the FLUX-dev route holds identity at ~0.9.
DEFAULT_FLUX_DEV_STRENGTH = 0.9
DEFAULT_SDXL_STRENGTH = 0.25
_MIN_STRENGTH, _MAX_STRENGTH = 0.0, 1.5


def resolve_lora_strength(model: Optional[str], override=None) -> float:
    """Model-aware character-LoRA strength, clamped to [0, 1.5]. ``model`` is the keyframe /
    storyboard image model name (e.g. 'flux-dev', 'sdxl'); ``override`` (operator setting) wins
    when not None. FLUX-dev → 0.9, everything else (SDXL et al.) → 0.25."""
    kfm = (model or "").lower()
    default = DEFAULT_FLUX_DEV_STRENGTH if ("flux" in kfm and "dev" in kfm) else DEFAULT_SDXL_STRENGTH
    try:
        val = float(override) if override is not None else default
    except (TypeError, ValueError):
        val = default
    return max(_MIN_STRENGTH, min(_MAX_STRENGTH, val))


def _dedup(seq: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        s = (s or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def subjects_to_lock(subjects: Iterable, *, include_bible: bool = True) -> tuple[list[str], str]:
    """Given trained cast Subject objects (anything with ``.lora_path``, ``.trigger_word`` /
    ``.name``, and optionally ``.bible``), return ``(lora_paths, lock_prefix)``.

    Only subjects that actually have a ``lora_path`` contribute — an untrained subject can't lock
    anything. ``lock_prefix`` is ``"trigger1, trigger2, bible1, bible2"`` (triggers first so they
    bind the LoRA, then the verbatim bibles that pin constant features like hair length). Both
    lists are de-duplicated in order. A LoRA only locks identity when its trigger token is
    actually present in the prompt, so the triggers go INTO the prompt, not just the loader chain.
    """
    lora_paths: list[str] = []
    triggers: list[str] = []
    bibles: list[str] = []
    for subj in subjects or []:
        if getattr(subj, "lora_path", None):
            lora_paths.append(subj.lora_path)
            triggers.append((getattr(subj, "trigger_word", None) or getattr(subj, "name", None) or "").strip())
            if include_bible and getattr(subj, "bible", None):
                bibles.append(subj.bible.strip())
    lock = ", ".join([*_dedup(triggers), *_dedup(bibles)])
    return _dedup(lora_paths), lock


def apply_lock(base_prompt: str, lock_prefix: str) -> str:
    """Front-load the identity lock onto a prompt. No-op when ``lock_prefix`` is empty."""
    base = (base_prompt or "").strip()
    if not lock_prefix:
        return base
    return f"{lock_prefix}, {base}" if base else lock_prefix
