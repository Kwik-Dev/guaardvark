"""Layer 3 of the film-orchestrator plan — the auto-curator.

The production pipeline parks at the `awaiting_approval` stage with one storyboard
frame per shot and `ProductionShot.approved=False`, waiting for a human to bless
each one. That's the "flip switches all day" problem. This module lets the
multimodal brain do the blessing: Gemma-4 *looks* at each storyboard frame, judges
whether it's on-model and coherent for that shot's character, and sets `approved`
automatically — escalating to the human ONLY the shots it's unsure about.

Design notes:
  - Follows the codebase's see→think split (utils/vision_analyzer): the VISION model
    describes the frame (the "eye"); the TEXT model turns that into a structured
    APPROVE/REJECT + confidence (the "brain"). Vision models give mushy JSON; text
    models can't see — so we use each for what it's good at.
  - SINGLE-image judgment (P0.4): VisionAnalyzer.analyze takes one image, and there's
    no turnkey face-match number, so we judge the storyboard against a TEXT
    description of the expected character. A two-image (reference vs frame) cosine
    compare is a deliberate future upgrade (insightface lives in ComfyUI's env).
  - SAFE BY DEFAULT: anything we can't confidently approve stays approved=False and
    goes to the human. A garbage/unparseable LLM reply → NOT approved. False
    negatives cost a human glance; false positives would render a broken shot.
  - Idempotent: no-ops unless the production is actually at `awaiting_approval`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Approve only at/above this confidence. Below it → escalate to the human.
DEFAULT_THRESHOLD = 70

_DESCRIBE_PROMPT = (
    "You are a film script supervisor inspecting a single storyboard frame for a movie. "
    "Describe what you see in 2-3 sentences: who/what is depicted, and critically — any "
    "rendering defects (extra or missing limbs, melted or distorted face, garbled hands, "
    "duplicated subjects, incoherent anatomy) or whether the frame looks clean and usable."
)

_DECIDE_PROMPT = (
    "You are the continuity supervisor on a film. Decide if a storyboard frame is usable.\n\n"
    "EXPECTED CHARACTER: {character}\n"
    "SHOT DESCRIPTION: {shot}\n"
    "WHAT THE FRAME SHOWS (from a vision model): {frame}\n\n"
    "APPROVE only if the frame plausibly depicts the expected character, matches the shot, "
    "and has NO serious rendering defects. Otherwise REJECT.\n"
    "Respond on ONE line, exactly this format:\n"
    "VERDICT: <APPROVE or REJECT>; CONFIDENCE: <0-100>; REASON: <short reason>"
)


def _analyzer():
    from backend.utils.vision_analyzer import VisionAnalyzer
    return VisionAnalyzer()


def _resolve_character(shot) -> str:
    """Best text description of the character this shot should depict."""
    cast = [pss.subject for pss in getattr(shot, "shot_subjects", [])]
    chars = [c for c in cast if getattr(c, "kind", None) == "character"]
    subj = None
    if shot.character_name:
        subj = next((c for c in chars if c.name == shot.character_name), None)
    if subj is None:
        subj = chars[0] if chars else None
    if subj is None:
        return shot.character_name or "the scene's subject"
    bits = [subj.name]
    if subj.description:
        bits.append(subj.description)
    return ": ".join(bits)


def _parse_verdict(text: str) -> dict:
    """Lenient parse of the decider's one-liner. Unparseable → not approved."""
    upper = (text or "").upper()
    # REJECT wins if present (conservative); else require an explicit APPROVE.
    if "REJECT" in upper:
        approve = False
    elif "APPROVE" in upper:
        approve = True
    else:
        approve = False  # couldn't tell → escalate to human, never silent-approve
    m = re.search(r"CONFIDENCE[:\s]+(\d{1,3})", upper)
    confidence = min(100, int(m.group(1))) if m else 0
    rm = re.search(r"REASON[:\s]+(.+)", text or "", re.IGNORECASE)
    reason = (rm.group(1).strip() if rm else (text or "").strip())[:240]
    return {"approve": approve, "confidence": confidence, "reason": reason}


def judge_shot(shot, *, analyzer=None, decider=None, threshold: int = DEFAULT_THRESHOLD) -> dict:
    """Vision-judge one storyboard frame. Returns
    {approved, approve, confidence, reason}. Pure enough to unit-test with mocks."""
    analyzer = analyzer or _analyzer()
    decider = decider or analyzer  # same wrapper: .analyze = eye, .text_query = brain

    path = shot.storyboard_image_path
    if not path or not Path(path).exists():
        return {"approved": False, "approve": False, "confidence": 0,
                "reason": "no storyboard image to judge"}

    try:
        from PIL import Image
        image = Image.open(path).convert("RGB")
    except Exception as e:
        return {"approved": False, "approve": False, "confidence": 0,
                "reason": f"could not open frame: {e}"}

    seen = analyzer.analyze(image, _DESCRIBE_PROMPT, think=False)
    if not seen.success:
        return {"approved": False, "approve": False, "confidence": 0,
                "reason": f"vision failed: {seen.error}"}

    decision = decider.text_query(_DECIDE_PROMPT.format(
        character=_resolve_character(shot),
        shot=shot.description or "(no description)",
        frame=seen.description,
    ))
    if not decision.success:
        return {"approved": False, "approve": False, "confidence": 0,
                "reason": f"decider failed: {decision.error}"}

    v = _parse_verdict(decision.description)
    v["approved"] = bool(v["approve"] and v["confidence"] >= threshold)
    return v


def auto_curate(prod_id: int, *, analyzer=None, decider=None,
                threshold: int = DEFAULT_THRESHOLD, do_advance: bool = True) -> dict:
    """Judge every shot of a production parked at `awaiting_approval`, set
    `approved`, and (if all pass and do_advance) advance the stage to `rendering`.

    Returns a summary dict. Does NOT dispatch the editor task — the caller
    (run_curator) does, keeping celery out of this testable unit. Idempotent:
    no-ops unless the production is at `awaiting_approval`.
    """
    from backend.models import Production, ProductionShot, db
    from backend.services.production_service import ProductionService

    prod = Production.query.get(prod_id)
    if prod is None:
        return {"skipped": True, "reason": "no such production"}
    if prod.current_stage != "awaiting_approval":
        return {"skipped": True, "reason": f"stage is {prod.current_stage}, not awaiting_approval"}

    shots = ProductionShot.query.filter_by(production_id=prod_id).all()
    if not shots:
        return {"skipped": True, "reason": "no shots"}

    results = []
    for shot in shots:
        v = judge_shot(shot, analyzer=analyzer, decider=decider, threshold=threshold)
        shot.approved = v["approved"]
        results.append({"shot": shot.shot_number, **v})
        logger.info("Curator shot %s: %s conf=%s (%s)",
                    shot.shot_number, "APPROVE" if v["approved"] else "FLAG",
                    v["confidence"], v["reason"][:80])
    db.session.commit()

    approved = [r for r in results if r["approved"]]
    flagged = [r for r in results if not r["approved"]]

    advanced = False
    if do_advance and not flagged:
        # Every shot passed — no human needed. Advance the gate to rendering.
        svc = ProductionService(db.session)
        advanced = svc.advance_if_predecessor(prod_id, expected_predecessor="awaiting_approval")

    return {
        "production_id": prod_id,
        "total": len(results),
        "approved": len(approved),
        "flagged": len(flagged),
        "flagged_shots": [r["shot"] for r in flagged],
        "advanced_to_rendering": advanced,
        "results": results,
    }
