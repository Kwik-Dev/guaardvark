"""Character Designer — the "Casting Director" appearance-bible role.

Dean's term for this is the **Casting Director**: it writes a very comprehensive,
detail-locked description of a character (down to the mole on a cheek) and a
reference-sheet of angle/expression prompts to generate synthetic LoRA training
data with FLUX.

NAMING: there is already a `CastingDirector` agent (`casting_director.py`) doing
*production casting* (which existing actors to cast + voices, deliberately
orphaned so it can't auto-trigger GPU training). This module is the DIFFERENT
role — designing a NEW character's look. Kept separate to avoid clobbering it.
UI label stays "Casting Director".

DESIGN (empirically verified 2026-06-21, gemma4:12b, format=json): a single call
asking for {bible, trigger, N fully-populated shots} is unreliable — the model
drops a field ~every gen at N>=4, the JSON goes malformed ~N>=8, and it paraphrases
the bible per shot instead of echoing it verbatim. The *content* is excellent. So
we split into two SMALL, reliable agents and let `character_generator_service`
orchestrate (bible-first → batched shots → compose prompts in code):

  - BibleDesigner: one tiny call → the frozen identity bible + a fabricated trigger.
  - ShotDesigner:  a batch of VARIATION descriptors (angle/expression/lighting/scene)
                   for a set of requested angle slots — it does NOT re-describe the
                   face. The service composes `trigger + verbatim bible + variation`.

All fields are lenient (defaults) so one dropped field degrades instead of failing,
per the never-raise BaseSwarmAgent contract.
"""
from pydantic import BaseModel
from backend.services.swarm.agents.base import BaseSwarmAgent


# ── shared models ──────────────────────────────────────────────────────────────
class CharacterBible(BaseModel):
    bible: str                 # the FROZEN identity paragraph, embedded verbatim in every prompt
    trigger_word: str = ""     # rare fabricated token; service fills a fallback if dropped


class ShotVariation(BaseModel):
    # Per-shot VARIATION only — never the identity (that lives in the frozen bible).
    # All lenient: the load-bearing output is the set of phrases the service composes.
    angle: str = ""        # "face-forward", "three-quarter left", "profile right", "full-body front"
    framing: str = ""      # "close-up" | "medium" | "full-body"
    expression: str = ""   # "neutral", "slight smile", "weary", ...
    lighting: str = ""     # "soft studio key", "window light", "harsh overhead", ...
    scene: str = ""        # brief background/setting phrase


class ShotBatch(BaseModel):
    shots: list[ShotVariation] = []


# ── bible agent ────────────────────────────────────────────────────────────────
_BIBLE_SYSTEM = """You are the Casting Director. Design a fictional character's exact physical
appearance for use as a FLUX image-generation reference (FLUX's text encoder is T5, so it wants
rich NATURAL-LANGUAGE description, not tags).

Do two things and nothing else:

1. Write a CHARACTER BIBLE: ONE dense paragraph fixing every INVARIANT identity feature so the
   same person is recognizable from any angle. Be specific and physical: face shape, jawline,
   skin tone/texture, distinguishing marks (moles/scars/freckles and their EXACT placement),
   eye color/shape, eyebrows, nose, lips, hair color/length/style, build, age, default wardrobe.
   Describe ONLY fixed identity — no camera angle, no pose, no lighting, no background.

2. Invent a TRIGGER WORD: a short rare FABRICATED token that is NOT a real name or dictionary
   word (e.g. "xvlr", "qttn", "zdrk"), so it won't collide with concepts the model already knows.

Return ONLY this JSON (no prose, no markdown):
{"bible": "<one dense identity paragraph>", "trigger_word": "<rare fabricated token>"}"""


class BibleDesigner(BaseSwarmAgent[CharacterBible]):
    name = "bible_designer"
    output_model = CharacterBible
    system_prompt = _BIBLE_SYSTEM
    model = "gemma4:12b"

    def build_user_prompt(self, input_data: dict) -> str:
        name = input_data.get("name", "the character")
        kind = input_data.get("kind", "character")
        description = input_data.get("description", "")
        trigger = input_data.get("trigger_word")
        prompt = (
            f"NAME: {name}\n"
            f"KIND: {kind}\n"
            f"BRIEF: {description or '(no brief — invent a coherent, distinctive look)'}\n"
        )
        if trigger:
            prompt += f"USE THIS TRIGGER WORD (do not invent another): {trigger}\n"
        prompt += "\nReturn the JSON with the bible and trigger_word."
        return prompt


# ── shots agent ────────────────────────────────────────────────────────────────
_SHOTS_SYSTEM = """You are the Casting Director building a reference sheet for a character whose
identity is ALREADY FIXED (given to you). Do NOT re-describe or change the face/hair/body — that
is frozen. For each requested camera angle, output ONLY the per-shot VARIATION: keep the given
angle, then choose a framing, a facial expression, a lighting setup, and a brief scene/background.
Vary expression, lighting, and scene across the shots so the training set is diverse.

Return ONLY this JSON (no prose, no markdown), one entry per requested angle, in order:
{"shots": [
  {"angle": "<the requested angle>", "framing": "<close-up|medium|full-body>",
   "expression": "<short>", "lighting": "<short>", "scene": "<short background phrase>"}
]}"""


class ShotDesigner(BaseSwarmAgent[ShotBatch]):
    name = "shot_designer"
    output_model = ShotBatch
    system_prompt = _SHOTS_SYSTEM
    model = "gemma4:12b"

    def build_user_prompt(self, input_data: dict) -> str:
        bible = input_data.get("bible", "")
        requested = input_data.get("requested_angles", [])
        numbered = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(requested))
        return (
            f"CHARACTER (frozen — do not restate in your output):\n{bible}\n\n"
            f"Produce exactly {len(requested)} shots, one for each requested angle, in this order:\n"
            f"{numbered}\n\n"
            "Return the JSON shot list (variation only — angle, framing, expression, lighting, scene)."
        )
