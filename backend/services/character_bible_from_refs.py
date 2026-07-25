"""Vision-ground a cast Subject.bible from uploaded reference photos.

Opposite of BibleDesigner (text invention): look at pixels and write identity
that matches sunglasses / shaved head / build / etc. Used by Cast
"Rebuild bible from photos" and by generate when refs exist but bible is empty.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

_IDENTITY_PROMPT = (
    "Describe the MAIN person's FIXED identity traits visible in this photo. "
    "Output ONLY comma-separated tags (no sentences). Cover:\n"
    "1) head/hair: shaved bald buzz cut short medium long; color if hair visible\n"
    "2) eyewear: sunglasses / glasses / none\n"
    "3) facial hair: clean-shaven / stubble / beard / none\n"
    "4) build/body: slim athletic average heavy — only if body is visible\n"
    "5) approximate age range (e.g. 30s 40s)\n"
    "6) skin tone (brief)\n"
    "7) distinctive marks if any (scar mole tattoo)\n"
    "8) typical clothing style colors if clear\n"
    "Describe ONLY what you SEE. Do NOT invent hair if the head is shaved. "
    "Do NOT invent sunglasses if none are worn. Keep under 40 words."
)

# Tags that contradict common invention failures — used when merging.
_HAIR_TAGS = re.compile(
    r"\b(shaved|bald|buzz\s*cut|hairless|clean[- ]?shaven head|no hair|"
    r"short hair|medium hair|long hair|balding)\b",
    re.I,
)
_EYEWEAR_TAGS = re.compile(r"\b(sunglasses|sun glasses|glasses|spectacles|eyewear)\b", re.I)
_BUILD_TAGS = re.compile(
    r"\b(slim|lean|athletic|average build|stocky|heavy|overweight|muscular|fit)\b",
    re.I,
)


def sample_ref_paths(paths: list[str], *, max_n: int = 12) -> list[str]:
    """Pick up to max_n existing image paths, spread across the list."""
    existing = [
        p for p in (paths or [])
        if p and Path(p).is_file() and Path(p).suffix.lower() in IMAGE_EXTS
    ]
    if not existing:
        return []
    if len(existing) <= max_n:
        return existing
    # Evenly spaced sample
    step = len(existing) / max_n
    return [existing[min(len(existing) - 1, int(i * step))] for i in range(max_n)]


def _clean_tags(text: str) -> list[str]:
    t = (text or "").strip()
    t = re.sub(r"^```.*?$", "", t, flags=re.MULTILINE).strip().strip('"').strip("'")
    t = re.sub(r"(?i)^(here('?s)?|the (image|photo|person)|caption)\s*[:\-]?\s*", "", t)
    t = t.replace("\n", ", ")
    parts = [p.strip().strip(".").lower() for p in t.split(",") if p.strip()]
    # drop empties / ultra-short noise
    return [p for p in parts if len(p) >= 3]


def extract_identity_tags(image_path: str, *, analyzer=None) -> list[str]:
    """Vision-extract identity tags from one ref. Empty list on failure."""
    try:
        from PIL import Image
        from backend.utils.vision_analyzer import VisionAnalyzer
        az = analyzer or VisionAnalyzer()
        img = Image.open(str(image_path)).convert("RGB")
        res = az.analyze(img, _IDENTITY_PROMPT, think=False, temperature=0.1, num_predict=96)
        if not getattr(res, "success", False):
            log.warning(
                "bible_from_refs: vision failed on %s: %s",
                image_path, getattr(res, "error", "?"),
            )
            return []
        return _clean_tags(getattr(res, "description", "") or "")
    except Exception as e:  # noqa: BLE001
        log.warning("bible_from_refs: exception on %s: %s", image_path, e)
        return []


def merge_identity_tags(tag_lists: list[list[str]], *, min_count: int = 1) -> list[str]:
    """Frequency-merge tags; prefer high-signal hair/eyewear/build consensus."""
    flat: list[str] = []
    for tags in tag_lists:
        flat.extend(tags)
    if not flat:
        return []

    counts = Counter(flat)
    # Also count substring families for hair/eyewear/build
    hair_votes: Counter[str] = Counter()
    eye_votes: Counter[str] = Counter()
    build_votes: Counter[str] = Counter()
    for t in flat:
        hm = _HAIR_TAGS.search(t)
        if hm:
            hair_votes[hm.group(0).lower()] += 1
        em = _EYEWEAR_TAGS.search(t)
        if em:
            eye_votes[em.group(0).lower()] += 1
        bm = _BUILD_TAGS.search(t)
        if bm:
            build_votes[bm.group(0).lower()] += 1

    chosen: list[str] = []
    # Force consensus traits first (if any vote)
    if hair_votes:
        chosen.append(hair_votes.most_common(1)[0][0])
    if eye_votes:
        # Prefer sunglasses over generic glasses when both appear
        top_eye = eye_votes.most_common()
        sung = [k for k, _ in top_eye if "sun" in k]
        chosen.append(sung[0] if sung else top_eye[0][0])
    if build_votes:
        chosen.append(build_votes.most_common(1)[0][0])

    # Remaining frequent tags (dedupe against chosen)
    chosen_l = {c.lower() for c in chosen}
    for tag, n in counts.most_common():
        if n < min_count:
            continue
        if tag in chosen_l:
            continue
        # skip if already covered by hair/eyewear/build consensus
        if _HAIR_TAGS.search(tag) or _EYEWEAR_TAGS.search(tag) or _BUILD_TAGS.search(tag):
            continue
        chosen.append(tag)
        chosen_l.add(tag)
        if len(chosen) >= 24:
            break
    return chosen


def tags_to_bible(tags: list[str], *, name: str = "") -> str:
    """Turn merged tags into one dense identity paragraph for Subject.bible."""
    if not tags:
        return ""
    # Deduplicate while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for t in tags:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            ordered.append(t)
    body = ", ".join(ordered)
    who = (name or "the subject").strip()
    return (
        f"{who}: {body}. "
        f"Keep this exact appearance in every shot — do not invent different hair, "
        f"body type, or eyewear."
    )


def short_identity_marks(tags: list[str], *, max_chars: int = 200) -> str:
    """Compact marks string for training captions (not a full invented bible)."""
    if not tags:
        return ""
    # Prefer head/eyewear/build first
    priority = []
    rest = []
    for t in tags:
        if _HAIR_TAGS.search(t) or _EYEWEAR_TAGS.search(t) or _BUILD_TAGS.search(t):
            priority.append(t)
        else:
            rest.append(t)
    s = ", ".join(priority + rest)
    if len(s) > max_chars:
        s = s[:max_chars].rsplit(",", 1)[0]
    return s


def rebuild_bible_from_refs(
    ref_image_paths: list[str],
    *,
    name: str = "",
    trigger_word: str | None = None,
    max_refs: int = 12,
    analyzer=None,
    min_tag_count: int = 1,
) -> dict[str, Any]:
    """Scan refs with vision and return a grounded bible dict.

    Returns:
      {ok, bible, trigger_word, tags, marks, sources_used, errors}
    """
    from backend.services.character_generator_service import _fallback_trigger

    sampled = sample_ref_paths(ref_image_paths, max_n=max_refs)
    if not sampled:
        return {
            "ok": False,
            "bible": "",
            "trigger_word": (trigger_word or "").strip() or _fallback_trigger(name or "chr"),
            "tags": [],
            "marks": "",
            "sources_used": [],
            "error": "no reference images found on disk",
        }

    tag_lists: list[list[str]] = []
    errors: list[str] = []
    for p in sampled:
        tags = extract_identity_tags(p, analyzer=analyzer)
        if tags:
            tag_lists.append(tags)
        else:
            errors.append(Path(p).name)

    merged = merge_identity_tags(tag_lists, min_count=min_tag_count)
    # If only one image produced tags, min_count=1 already keeps them
    bible = tags_to_bible(merged, name=name)
    marks = short_identity_marks(merged)
    trigger = (trigger_word or "").strip() or _fallback_trigger(name or "chr")

    if not bible:
        return {
            "ok": False,
            "bible": "",
            "trigger_word": trigger,
            "tags": [],
            "marks": "",
            "sources_used": sampled,
            "error": "vision produced no usable identity tags",
            "failed_images": errors,
        }

    return {
        "ok": True,
        "bible": bible,
        "trigger_word": trigger,
        "tags": merged,
        "marks": marks,
        "sources_used": sampled,
        "failed_images": errors,
        "vision_grounded": True,
    }


def persist_bible_on_subject(subject, result: dict, *, refresh_captions: bool = True) -> dict:
    """Write bible/trigger onto Subject; optionally refresh caption sidecars with marks."""
    from backend.models import db

    if not result.get("ok") or not result.get("bible"):
        return result

    subject.bible = result["bible"]
    if result.get("trigger_word"):
        subject.trigger_word = result["trigger_word"]
    # Flag so UI knows bible was vision-grounded
    cfg = dict(getattr(subject, "training_settings_json", None) or {})
    cfg["bible_vision_grounded"] = True
    cfg["bible_vision_tags"] = list(result.get("tags") or [])[:32]
    cfg["bible_identity_marks"] = result.get("marks") or ""
    subject.training_settings_json = cfg
    db.session.commit()

    if refresh_captions:
        try:
            from backend.services.character_captioner import ensure_subject_image_captions
            paths = list(subject.ref_image_paths or [])
            ensure_subject_image_captions(
                paths,
                trigger=(subject.trigger_word or subject.name or "").strip(),
                identity_marks=result.get("marks") or "",
                overwrite=True,
            )
            result["captions_refreshed"] = True
        except Exception as e:  # noqa: BLE001
            log.warning("persist_bible: caption refresh failed: %s", e)
            result["captions_refreshed"] = False
            result["caption_error"] = str(e)[:200]
    return result
