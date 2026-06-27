"""Character generator service — orchestrates the "Casting Director" agents into a
complete FLUX reference sheet (bible + N composed image prompts).

Built to the empirically-verified pattern (see character_designer.py docstring):
  1. ONE bible+trigger call (small schema → reliable).
  2. Shots generated in BATCHES with a per-batch top-up loop (the model is flaky on
     large strict JSON; small batches + retry keep coverage).
  3. Prompts COMPOSED in code as `trigger + verbatim bible + variation` — we never
     trust the model to echo the bible (it paraphrases/corrupts it per shot).

Coverage is taxonomy-driven: the service decides the angle distribution for N and
asks the ShotDesigner to fill specific slots, so we get good 3D coverage instead of
hoping the model distributes angles well.

Returns plain dicts (no DB) so it is unit-testable now; Phase 2 persists the result
into SubjectSample rows. GPU-free — pure LLM (Ollama).
"""
from __future__ import annotations
import hashlib
import math
import re

from backend.services.swarm.agents.character_designer import (
    BibleDesigner,
    ShotDesigner,
    ShotVariation,
)

# Angle taxonomy (label, weight). Two "face-forward" buckets intentionally over-weight frontal
# coverage (one plain, one expressive) — frontal identity matters most to a LoRA. Full-body is
# now ~32% (front + three-quarter), up from 15%, so the eventual LoRA training set isn't ~72%
# face-framed (which would bake a close-up bias into the trained character). Weights sum to 1.0.
_ANGLE_WEIGHTS: list[tuple[str, float]] = [
    ("face-forward", 0.18),
    ("three-quarter left", 0.11),
    ("three-quarter right", 0.11),
    ("profile left", 0.07),
    ("profile right", 0.07),
    ("full-body front", 0.20),
    ("full-body three-quarter", 0.12),
    ("face-forward", 0.14),  # expressive frontal variety
]


def _angle_slots(n: int) -> list[str]:
    """Deterministically allocate exactly `n` angle slots by the taxonomy (largest-remainder)."""
    if n <= 0:
        return []
    exact = [w * n for _, w in _ANGLE_WEIGHTS]
    counts = [int(math.floor(x)) for x in exact]
    remainder = n - sum(counts)
    # hand the leftover slots to the largest fractional parts
    order = sorted(range(len(exact)), key=lambda i: exact[i] - counts[i], reverse=True)
    for k in range(remainder):
        counts[order[k % len(order)]] += 1
    slots: list[str] = []
    for (label, _), c in zip(_ANGLE_WEIGHTS, counts):
        slots.extend([label] * c)
    return slots[:n]


def _fallback_trigger(name: str) -> str:
    """A rare fabricated token if the model dropped trigger_word. Deterministic per name."""
    cons = "".join(c for c in name.lower() if c.isalpha() and c not in "aeiou")[:4] or "chr"
    suffix = hashlib.sha1(name.encode("utf-8")).hexdigest()[:3]
    return f"{cons}{suffix}"


def is_full_body(angle: str = "", framing: str = "") -> bool:
    """True when a shot calls for a head-to-toe figure — by its angle label ('full-body ...') or
    its framing ('full-body'). Shared by _compose_prompt (prompt ordering) and the task layer
    (portrait aspect) so the prompt's framing directive and the canvas never disagree."""
    return ("full-body" in (angle or "").lower()) or ((framing or "").strip().lower() == "full-body")


# Emphatic full-length directive. For full-body slots this LEADS the prompt (before the bible) so
# the framing isn't drowned by the ~195-token, face-saturated identity bible that follows.
_FULL_BODY_LEAD = (
    "full body shot, head to toe, full-length, entire figure visible, "
    "standing, feet visible, wide framing"
)

# Fine facial MINUTIAE (the "EXACT placement" marks the bible packs in per character_designer's
# _BIBLE_SYSTEM). Word-boundary matched so 'scar' doesn't catch 'scarlet'. These magnetise FLUX
# toward a close-up face and, at full-body size, won't even be visible — so we drop clauses that
# are PURELY these. Coarse identity (face shape, eye colour) is NOT in this set and stays.
_FACE_MICRO_RE = re.compile(
    r"\b(freckles?|moles?|scars?|dimples?|eyelashes?|philtrum|nostrils?|"
    r"wrinkles?|stubble|blemish(?:es)?|birthmarks?|beauty\s+marks?|crow'?s\s+feet)\b",
    re.IGNORECASE,
)
# A clause survives even if it names a minutia, when it ALSO carries coarse body/hair/wardrobe
# identity (so we never strip the build/outfit/hair the full-body shot most needs to stay on-model).
_KEEP_RE = re.compile(
    r"\b(build|frame|tall|short|height|shoulders?|torso|physique|athletic|slender|stocky|"
    r"lean|muscular|hair|skin|complexion|wears?|wearing|jacket|shirt|trousers|pants|dress|"
    r"coat|boots|shoes|outfit|wardrobe|aged?|years?)\b",
    re.IGNORECASE,
)


def _soften_face_detail(bible: str) -> str:
    """For full-body slots: drop bible clauses that are PURELY fine facial minutiae so they don't
    pull FLUX toward a close-up or eat the token budget the full-length directive needs. A clause
    is dropped only when it matches _FACE_MICRO_RE and NOT _KEEP_RE. Never returns empty (falls
    back to the full bible) so identity is never wiped out."""
    frags = [f.strip() for f in re.split(r"(?<=[.;,])\s+", bible) if f.strip()]
    kept = [f for f in frags if not (_FACE_MICRO_RE.search(f) and not _KEEP_RE.search(f))]
    return " ".join(kept).strip() or bible.strip()


def _compose_prompt(trigger: str, bible: str, v: ShotVariation, angle: str) -> str:
    """The load-bearing step: trigger + VERBATIM bible + the shot's variation phrases.

    Full-body slots are special-cased: the full-length directive LEADS (so framing isn't drowned
    by the bible) and the bible's fine facial minutiae are softened. Close-up/medium are unchanged.
    """
    bits = [
        angle,
        v.framing,
        f"{v.expression} expression" if v.expression else "",
        v.lighting,
        v.scene,
    ]
    variation = ", ".join(b for b in bits if b)
    if is_full_body(angle, v.framing):
        identity = _soften_face_detail(bible)
        core = f"{_FULL_BODY_LEAD}. {trigger} {identity}".strip()
    else:
        core = f"{trigger} {bible}".strip()
    return f"{core}. {variation}." if variation else core


def _default_llm(*, system: str, user: str, model: str = "gemma4:12b") -> str:
    import ollama
    resp = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        format="json",  # hardens JSON parsing for the strict-schema agents
    )
    return resp["message"]["content"]


def _gen_batch(agent: ShotDesigner, bible: str, batch_angles: list[str], max_retries: int) -> list[ShotVariation]:
    """Generate variations for a batch of angle slots, topping up on shortfall."""
    needed = list(batch_angles)
    collected: list[ShotVariation] = []
    attempts = 0
    while needed and attempts <= max_retries:
        inv = agent.invoke({"bible": bible, "requested_angles": needed})
        produced = inv.output.shots if (inv.status == "ok" and inv.output) else []
        take = produced[: len(needed)]
        collected.extend(take)
        needed = needed[len(take):]
        attempts += 1
    # pad any still-missing slots with angle-only placeholders (user can regenerate in UI)
    while len(collected) < len(batch_angles):
        collected.append(ShotVariation())
    return collected[: len(batch_angles)]


def generate_character_sheet(
    name: str,
    kind: str = "character",
    description: str = "",
    n: int = 32,
    *,
    llm=None,
    trigger_word: str | None = None,
    batch_size: int = 8,
    bible_model: str = "gemma4:12b",
    shot_model: str = "gemma4:12b",
    max_batch_retries: int = 2,
) -> dict:
    """Produce {bible, trigger_word, shots:[{index, angle, ..., image_prompt}]}.

    `shots` always has exactly `n` entries (placeholders for any the model couldn't
    fill — these come back with an empty/near-empty prompt and are flagged for regen).
    """
    llm = llm or _default_llm

    # 1. bible + trigger (small, reliable; one retry on failure)
    bagent = BibleDesigner(llm)
    bagent.model = bible_model
    binv = bagent.invoke({"name": name, "kind": kind, "description": description, "trigger_word": trigger_word})
    if binv.status != "ok" or not binv.output or not binv.output.bible:
        binv = bagent.invoke({"name": name, "kind": kind, "description": description, "trigger_word": trigger_word})
    if binv.status != "ok" or not binv.output or not binv.output.bible:
        return {"bible": "", "trigger_word": "", "n_requested": n, "shots": [], "error": f"bible generation failed ({binv.status})"}

    bible = binv.output.bible.strip()
    trigger = (trigger_word or binv.output.trigger_word or _fallback_trigger(name)).strip()

    # 2. batched shots with taxonomy-driven coverage
    slots = _angle_slots(n)
    sagent = ShotDesigner(llm)
    sagent.model = shot_model
    shots: list[dict] = []
    for start in range(0, len(slots), batch_size):
        batch = slots[start: start + batch_size]
        variations = _gen_batch(sagent, bible, batch, max_batch_retries)
        for angle, v in zip(batch, variations):
            idx = len(shots)
            prompt = _compose_prompt(trigger, bible, v, angle)
            shots.append({
                "index": idx,
                "angle": angle,              # authoritative from the taxonomy, not the model
                "framing": v.framing,
                "expression": v.expression,
                "lighting": v.lighting,
                "scene": v.scene,
                "image_prompt": prompt,
                "placeholder": not (v.framing or v.expression or v.lighting or v.scene),
            })

    return {"bible": bible, "trigger_word": trigger, "n_requested": n, "shots": shots}
