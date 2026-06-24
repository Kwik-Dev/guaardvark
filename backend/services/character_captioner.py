"""VLM auto-captioner for character LoRA training data.

WHY THIS EXISTS — the freckle / horse-head story (2026-06-23):
The first ``sage_harlow`` LoRA trained on 8 head-only images whose ``.txt`` captions were
(a) never read (the run used ``caption_strategy: "filename"``) and (b) had no identity marks
and no full-body framing. The model overfit one face with no learned body, so under motion it
improvised a body/animal → "horse-head". The fix is real captions with: the trigger word bound
to the FIXED identity marks (freckles, eye color, beauty mark, hair highlights) AND explicit,
VARIED framing tags (full body / three-quarter / profile) so the LoRA actually learns a body.

This module is the missing caption step. There was NO auto-captioner anywhere in the repo —
captions were 100% hand-written. It reuses the existing offline VLM (``VisionAnalyzer`` →
Ollama Gemma-vision, the same one ``film_curator_service`` uses — no new model download, stays
fully offline) to describe ONLY the variable, non-identity attributes of each frame, then
deterministically front-loads the trigger word and appends the fixed identity marks.

Caption layout:  ``<trigger>, <framing>, <pose/gaze>, <expression>, <outfit>, <setting>,
                   <lighting>, <fixed identity marks>``

Used by:
  * ``scripts/caption_dataset.py`` — caption a manual SimpleTuner dataset before training.
  * ``backend/tasks/character_generation_tasks.generate_samples`` — write ``.txt`` sidecars
    alongside generated training images so the app-generated set is trainable as-is.

It NEVER runs training or GPU work; captioning is a Vision (Ollama) call only.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

# Canonical framing vocabulary. The captioner asks the VLM to lead with exactly one of these,
# and the pre-train gate uses ``detect_framing`` to measure pose coverage across the dataset
# (e.g. "are there enough full-body shots?"). Order = roughly tight → wide.
FRAMING_TAGS = [
    "close-up",
    "head and shoulders",
    "upper body",
    "three-quarter view",
    "full body",
    "wide shot",
]
# Framings that supervise the body (the coverage the horse-head failure was missing).
FULL_BODY_FRAMINGS = {"three-quarter view", "full body", "wide shot"}

_VLM_CAPTION_PROMPT = (
    "You are writing a concise training caption for ONE image of a person. "
    "Output ONLY comma-separated visual tags (no sentences, no 'the image shows', no name). "
    "Cover, in this order:\n"
    "1) FRAMING — choose EXACTLY ONE: close-up, head and shoulders, upper body, "
    "three-quarter view, full body, wide shot.\n"
    "2) head/gaze direction and body pose.\n"
    "3) facial expression.\n"
    "4) clothing/outfit with its colors.\n"
    "5) background/setting.\n"
    "6) lighting.\n"
    "Describe ONLY these VARIABLE attributes. Do NOT mention identity: no name, no skin tone, "
    "no freckles, no eye color, no hair color or length, no tattoos, no jewelry. "
    "Keep it under 30 words. Start with the framing tag."
)


def _analyzer():
    """Lazy-build the shared offline vision wrapper (same one film_curator uses)."""
    from backend.utils.vision_analyzer import VisionAnalyzer
    return VisionAnalyzer()


def _clean_vlm(text: str) -> str:
    """Normalize a VLM reply into a flat comma-separated tag string."""
    t = (text or "").strip()
    # Strip code fences / quotes / common prefixes.
    t = re.sub(r"^```.*?$", "", t, flags=re.MULTILINE).strip().strip('"').strip("'")
    t = re.sub(r"(?i)^(here('?s)?( is)?|this image (shows|depicts)|the (image|photo) (shows|depicts)|caption)\s*", "", t)
    # Strip a leading colon/dash left by a removed prefix (e.g. "shows: full body").
    t = t.lstrip(" :;-–—,")
    # Newlines / numbered list markers → commas.
    t = re.sub(r"^\s*\d+[\.\)]\s*", "", t, flags=re.MULTILINE)
    t = t.replace("\n", ", ")
    # Collapse repeated commas/whitespace and trailing punctuation.
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s*,\s*(,\s*)+", ", ", t)
    t = t.strip().strip(".,; ")
    return t


def detect_framing(caption: str) -> Optional[str]:
    """Return the canonical framing tag present in a caption, or None. Used by the gate's
    pose-coverage check and by the captioner to confirm the VLM led with a framing."""
    c = (caption or "").lower()
    # Prefer the most specific / widest match so 'full body' wins over a stray 'body'.
    for tag in ("wide shot", "full body", "three-quarter view", "upper body",
                "head and shoulders", "close-up"):
        if tag in c:
            return tag
    # Common synonyms the VLM might emit.
    if re.search(r"\bfull[- ]length\b", c):
        return "full body"
    if re.search(r"\b3/4\b|\bthree quarter\b", c):
        return "three-quarter view"
    if re.search(r"\bportrait\b|\bheadshot\b", c):
        return "close-up"
    return None


def compose_caption(trigger: str, vlm_desc: str, identity_marks: str = "") -> str:
    """Deterministically assemble the final caption: trigger first (so it binds the identity),
    the VLM's variable description in the middle, fixed identity marks last. Dedupes the trigger
    if the VLM echoed it. Never raises."""
    trigger = (trigger or "").strip().strip(",")
    body = _clean_vlm(vlm_desc)
    # Remove an accidental leading trigger from the VLM body to avoid duplication.
    if trigger and body.lower().startswith(trigger.lower()):
        body = body[len(trigger):].strip().strip(",").strip()
    marks = (identity_marks or "").strip().strip(",")
    parts = [p for p in (trigger, body, marks) if p]
    return ", ".join(parts)


def caption_image(
    image_path: str | Path,
    *,
    trigger: str,
    identity_marks: str = "",
    analyzer=None,
) -> str:
    """Caption a single image. Returns the composed caption string, or a minimal
    ``"<trigger>, <identity_marks>"`` fallback if the VLM is unavailable (so a sidecar always
    has at least the trigger + identity marks — never an empty/identity-less caption)."""
    analyzer = analyzer or _analyzer()
    try:
        # VisionAnalyzer.analyze expects a PIL Image (it base64-encodes internally), not a path.
        from PIL import Image
        img = Image.open(str(image_path)).convert("RGB")
        res = analyzer.analyze(img, _VLM_CAPTION_PROMPT, think=False)
        if getattr(res, "success", False) and getattr(res, "description", "").strip():
            return compose_caption(trigger, res.description, identity_marks)
        log.warning("captioner: VLM failed for %s (%s); using trigger+marks fallback",
                    image_path, getattr(res, "error", "no description"))
    except Exception as e:  # noqa: BLE001 — captioning must never explode a dataset run
        log.warning("captioner: exception on %s (%s); using trigger+marks fallback", image_path, e)
    return compose_caption(trigger, "", identity_marks)


def caption_dataset(
    dataset_dir: str | Path,
    *,
    trigger: str,
    identity_marks: str = "",
    overwrite: bool = False,
    dry_run: bool = False,
    analyzer=None,
) -> dict:
    """Write ``<stem>.txt`` sidecars for every image in ``dataset_dir``.

    Skips images that already have a caption unless ``overwrite``. With ``dry_run`` it captions
    but writes nothing (returns the proposed captions for review). Returns a summary dict with
    per-image results and a framing-coverage tally so the caller (and the pre-train gate) can
    see whether full-body shots are present. Never raises on a single image — it logs and moves on.
    """
    d = Path(dataset_dir)
    images = sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXTS) if d.is_dir() else []
    analyzer = analyzer or (None if dry_run is None else _analyzer())
    results = []
    framing_tally: dict[str, int] = {}
    written = skipped = 0
    for img in images:
        sidecar = img.with_suffix(".txt")
        if sidecar.exists() and not overwrite:
            skipped += 1
            existing = sidecar.read_text(encoding="utf-8").strip()
            fr = detect_framing(existing)
            framing_tally[fr or "unknown"] = framing_tally.get(fr or "unknown", 0) + 1
            results.append({"image": img.name, "caption": existing, "framing": fr, "action": "skipped"})
            continue
        caption = caption_image(img, trigger=trigger, identity_marks=identity_marks, analyzer=analyzer)
        fr = detect_framing(caption)
        framing_tally[fr or "unknown"] = framing_tally.get(fr or "unknown", 0) + 1
        if not dry_run:
            sidecar.write_text(caption + "\n", encoding="utf-8")
            written += 1
        results.append({"image": img.name, "caption": caption, "framing": fr,
                        "action": "dry-run" if dry_run else "written"})
    full_body = sum(framing_tally.get(f, 0) for f in FULL_BODY_FRAMINGS)
    return {
        "dir": str(d),
        "images": len(images),
        "written": written,
        "skipped": skipped,
        "framing_tally": framing_tally,
        "full_body_count": full_body,
        "results": results,
    }


# Convenience: pull a compact identity-marks line out of a character_profile.md so the manual
# path doesn't have to hand-type it. Best-effort — returns "" if the file isn't parseable.
def marks_from_profile(profile_path: str | Path) -> str:
    """Heuristically extract fixed identity marks (skin/freckles, eyes, hair, distinguishing
    marks, tattoo) from a character_profile.md. Best-effort, returns a comma string."""
    try:
        text = Path(profile_path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    wanted = {
        "skin": r"(?i)skin tone[:*\s]+(.+)",
        "eyes": r"(?i)\*\*eyes:\*\*\s*(.+)",
        "marks": r"(?i)distinguishing marks[:*\s]+(.+)",
        "hair": r"(?i)\*\*hair:\*\*\s*(.+)",
    }
    out: list[str] = []
    for _, pat in wanted.items():
        m = re.search(pat, text)
        if m:
            frag = re.sub(r"\([^)]*\)", "", m.group(1)).strip().strip(".")
            # keep it short — first clause only
            frag = re.split(r"[.;]", frag)[0].strip()
            if frag:
                out.append(frag.lower())
    return ", ".join(out)
