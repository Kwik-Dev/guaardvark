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

from backend.services.swarm.agents.character_designer import (
    BibleDesigner,
    ShotDesigner,
    ShotVariation,
)

# Angle taxonomy (label, weight). Two "face-forward" buckets intentionally over-weight
# frontal coverage (one plain, one expressive) — frontal identity matters most to a LoRA.
_ANGLE_WEIGHTS: list[tuple[str, float]] = [
    ("face-forward", 0.22),
    ("three-quarter left", 0.13),
    ("three-quarter right", 0.13),
    ("profile left", 0.08),
    ("profile right", 0.08),
    ("full-body front", 0.15),
    ("face-forward", 0.21),  # expressive frontal variety
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


def _compose_prompt(trigger: str, bible: str, v: ShotVariation, angle: str) -> str:
    """The load-bearing step: trigger + VERBATIM bible + the shot's variation phrases."""
    bits = [
        angle,
        v.framing,
        f"{v.expression} expression" if v.expression else "",
        v.lighting,
        v.scene,
    ]
    variation = ", ".join(b for b in bits if b)
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
