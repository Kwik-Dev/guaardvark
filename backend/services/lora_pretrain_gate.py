"""Cast LoRA pre-train validation — adapted from scripts/pretrain_gate.py for the
in-app training path (ref images + approved samples, not a flat dataset dir)."""
from __future__ import annotations

import math
from pathlib import Path

from backend.models import Subject, SubjectSample


def _framing_helpers():
    try:
        from backend.services.character_captioner import detect_framing, FULL_BODY_FRAMINGS
        return detect_framing, FULL_BODY_FRAMINGS
    except Exception:
        FULL = {"three-quarter view", "full body", "wide shot"}

        def detect_framing(c: str):
            c = (c or "").lower()
            for tag in ("wide shot", "full body", "three-quarter view", "upper body",
                        "head and shoulders", "close-up"):
                if tag in c:
                    return tag
            return None

        return detect_framing, FULL


def is_bare_caption(caption: str, token: str) -> bool:
    """True when caption is essentially just ``a photo of {token}`` (no visual traits)."""
    t = (caption or "").strip().lower().rstrip(".")
    tok = (token or "").strip().lower()
    if not t:
        return True
    if tok and t in {tok, f"a photo of {tok}", f"photo of {tok}"}:
        return True
    if tok and t.startswith(f"a photo of {tok}"):
        # Allow a short trailing clause; still bare if almost nothing after.
        rest = t[len(f"a photo of {tok}"):].strip(" ,;-")
        return len(rest) < 12
    return False


def _caption_for_path(
    subject: Subject,
    image_path: str,
    sample_by_path: dict[str, SubjectSample],
) -> str:
    token = (subject.trigger_word or "").strip() or subject.name
    smp = sample_by_path.get(image_path)
    if smp and smp.image_prompt and smp.image_prompt.strip():
        cap = smp.image_prompt.strip()
        if token.lower() not in cap.lower():
            return f"a photo of {token}, {cap}"
        return cap
    sidecar = Path(image_path).with_suffix(".txt")
    if sidecar.is_file():
        text = sidecar.read_text(encoding="utf-8").strip()
        if text:
            if token.lower() not in text.lower():
                return f"a photo of {token}, {text}"
            return text
    return f"a photo of {token}"


def build_training_captions(
    subject: Subject,
    train_images: list[str],
) -> list[str]:
    """Per-image captions for the trainer (parallel to train_images)."""
    approved = (
        SubjectSample.query
        .filter_by(subject_id=subject.id, approved=True, status="done")
        .all()
    )
    sample_by_path = {s.image_path: s for s in approved if s.image_path}
    return [_caption_for_path(subject, p, sample_by_path) for p in train_images]


def caption_coverage_stats(
    subject: Subject,
    train_images: list[str] | None = None,
) -> dict:
    """Stats for Cast UI / train gate: how many captions are rich vs bare fallback."""
    paths = train_images
    if paths is None:
        paths = list(subject.ref_image_paths or [])
        approved = (
            SubjectSample.query
            .filter_by(subject_id=subject.id, approved=True, status="done")
            .all()
        )
        for smp in approved:
            if smp.image_path and smp.image_path not in paths:
                paths.append(smp.image_path)
    existing = [p for p in (paths or []) if p and Path(p).is_file()]
    token = (subject.trigger_word or "").strip() or subject.name
    captions = build_training_captions(subject, existing)
    bare = sum(1 for c in captions if is_bare_caption(c, token))
    rich = max(0, len(captions) - bare)
    return {
        "images": len(existing),
        "rich_captions": rich,
        "bare_captions": bare,
        "captions": captions,
        "trigger_word": token,
    }


def validate_cast_training(
    subject: Subject,
    train_images: list[str],
    *,
    min_images: int = 4,
    require_trigger_in_captions: bool = True,
) -> dict:
    """Return {pass, failures, warnings, captions, framing, ...}."""
    detect_framing, FULL_BODY_FRAMINGS = _framing_helpers()
    failures: list[str] = []
    warnings: list[str] = []
    token = (subject.trigger_word or "").strip() or subject.name

    existing = [p for p in train_images if p and Path(p).is_file()]
    missing = [p for p in train_images if p and not Path(p).is_file()]
    for p in missing:
        failures.append(f"missing image file: {p}")

    n = len(existing)
    if n < min_images:
        failures.append(f"only {n} trainable image(s); need at least {min_images}")

    captions = build_training_captions(subject, existing)
    framing_tally: dict[str, int] = {}
    bare_count = 0
    for cap in captions:
        fr = detect_framing(cap) or "unknown"
        framing_tally[fr] = framing_tally.get(fr, 0) + 1
        if require_trigger_in_captions and token.lower() not in cap.lower():
            failures.append(f"trigger '{token}' missing from caption for an image")
        if is_bare_caption(cap, token):
            bare_count += 1

    rich_count = max(0, len(captions) - bare_count)
    if n and bare_count > 0.5 * n:
        warnings.append(
            f"{bare_count}/{n} captions are bare \"a photo of {token}\" fallbacks — "
            "VLM captioning should run before train; identity will be weak without "
            "visual trait text on the uploads"
        )

    full_body = sum(framing_tally.get(f, 0) for f in FULL_BODY_FRAMINGS)
    need_full = max(2, math.ceil(0.15 * n)) if n else 2
    if n and full_body < need_full:
        warnings.append(
            f"only {full_body} full-body/three-quarter/wide caption(s); "
            f"recommend >= {need_full} for stable body identity (framing={framing_tally})"
        )

    if n < 12:
        warnings.append(
            f"only {n} images — workable for a quick LoRA, but 12–30 with varied "
            "framing/outfits improves robustness"
        )

    return {
        "pass": not failures,
        "images": n,
        "failures": failures,
        "warnings": warnings,
        "captions": captions,
        "framing": framing_tally,
        "full_body_count": full_body,
        "full_body_recommended": need_full,
        "bare_captions": bare_count,
        "rich_captions": rich_count,
    }