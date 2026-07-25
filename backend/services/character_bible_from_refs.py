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

# Face-first humans AND costumed/masked characters (cowl, armor, LED eyes, etc.).
# Prior prompts only asked hair/eyewear/shave → Batman became "shaved, none, 30s".
_IDENTITY_PROMPT = (
    "Describe the MAIN character's FIXED visual identity in this photo. "
    "Output ONLY comma-separated tags (no sentences, no 'none', no 'n/a'). "
    "Cover whatever is actually visible — skip categories that do not apply:\n"
    "A) COSTUME / MASK (if any): cowl hood helmet horns ears mask; cape cloak; "
    "armor plating suit material; utility belt; gloves gauntlets spiked braces; "
    "boots; emblem logo; cape color; suit colors; rivets seams texture\n"
    "B) EYES / FACE OPENING: white glowing LED eyes, eye slits, exposed face, "
    "skin tone if visible, facial hair if visible\n"
    "C) HAIR (only if scalp/hair is actually visible — NEVER say shaved/bald for a cowl/helmet)\n"
    "D) BUILD: slim athletic muscular average heavy — only if body silhouette is clear\n"
    "E) DISTINCTIVE props/weapons/gadgets if part of the fixed look\n"
    "Describe ONLY what you SEE. Prefer concrete costume nouns over vague colors. "
    "Keep under 55 words."
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
_COSTUME_TAGS = re.compile(
    r"\b(cowl|hood|helmet|mask|horns?|ears?|cape|cloak|armor|plated?|suit|"
    r"utility\s*belt|belt|gloves?|gauntlets?|braces?|boots?|emblem|logo|"
    r"rivets?|spiked?|led\s*eyes?|glowing\s*eyes?|white\s*eyes?|eye\s*slits?|"
    r"bat[- ]?symbol|chest\s*emblem|bodysuit|catsuit)\b",
    re.I,
)
# Noise the model emits when a category does not apply.
_NOISE_TAGS = re.compile(
    r"^(none|n/?a|null|unknown|not\s*visible|not\s*applicable|unspecified|"
    r"no\s*(hair|glasses|eyewear|facial\s*hair)?|clean[- ]?shaven)$",
    re.I,
)
# When a cowl/helmet/mask is present, hair tags are usually wrong (model says "shaved").
_HEAD_COVERED = re.compile(r"\b(cowl|hood|helmet|mask|horns?)\b", re.I)


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
    t = re.sub(r"(?i)^(here('?s)?|the (image|photo|person|character)|caption)\s*[:\-]?\s*", "", t)
    t = t.replace("\n", ", ")
    parts = [p.strip().strip(".").lower() for p in t.split(",") if p.strip()]
    out: list[str] = []
    for p in parts:
        if len(p) < 3:
            continue
        if _NOISE_TAGS.match(p):
            continue
        # Drop lone color-slash piles with no noun ("black/dark gray")
        if re.fullmatch(r"[\w\s/\-]+", p) and "/" in p and not _COSTUME_TAGS.search(p):
            # keep if it names a body part / material; else skip pure color lists
            if not re.search(
                r"\b(hair|skin|eyes?|suit|cape|armor|boots?|gloves?|belt|cowl)\b", p, re.I
            ):
                continue
        out.append(p)
    return out


def extract_identity_tags(image_path: str, *, analyzer=None) -> list[str]:
    """Vision-extract identity tags from one ref. Empty list on failure."""
    try:
        from PIL import Image
        from backend.utils.vision_analyzer import VisionAnalyzer
        az = analyzer or VisionAnalyzer()
        img = Image.open(str(image_path)).convert("RGB")
        res = az.analyze(img, _IDENTITY_PROMPT, think=False, temperature=0.1, num_predict=160)
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
    """Frequency-merge tags; prefer costume, then hair/eyewear/build consensus."""
    flat: list[str] = []
    for tags in tag_lists:
        flat.extend(tags)
    if not flat:
        return []

    counts = Counter(flat)
    hair_votes: Counter[str] = Counter()
    eye_votes: Counter[str] = Counter()
    build_votes: Counter[str] = Counter()
    costume_votes: Counter[str] = Counter()
    for t in flat:
        if _COSTUME_TAGS.search(t):
            costume_votes[t.lower()] += 1
        hm = _HAIR_TAGS.search(t)
        if hm:
            hair_votes[hm.group(0).lower()] += 1
        em = _EYEWEAR_TAGS.search(t)
        if em:
            eye_votes[em.group(0).lower()] += 1
        bm = _BUILD_TAGS.search(t)
        if bm:
            build_votes[bm.group(0).lower()] += 1

    head_covered = bool(costume_votes) and any(
        _HEAD_COVERED.search(t) for t in costume_votes
    )

    chosen: list[str] = []
    # Costume first (cowl/armor/belt…) — this is the identity for masked characters
    for tag, _n in costume_votes.most_common(16):
        if tag not in chosen:
            chosen.append(tag)

    if hair_votes and not head_covered:
        chosen.append(hair_votes.most_common(1)[0][0])
    if eye_votes and not head_covered:
        top_eye = eye_votes.most_common()
        sung = [k for k, _ in top_eye if "sun" in k]
        chosen.append(sung[0] if sung else top_eye[0][0])
    if build_votes:
        chosen.append(build_votes.most_common(1)[0][0])

    chosen_l = {c.lower() for c in chosen}
    for tag, n in counts.most_common():
        if n < min_count:
            continue
        if tag in chosen_l:
            continue
        if head_covered and _HAIR_TAGS.search(tag):
            continue  # cowl ≠ shaved head
        if (
            _HAIR_TAGS.search(tag)
            or _EYEWEAR_TAGS.search(tag)
            or _BUILD_TAGS.search(tag)
            or _COSTUME_TAGS.search(tag)
        ):
            continue
        chosen.append(tag)
        chosen_l.add(tag)
        if len(chosen) >= 28:
            break
    return chosen


def tags_to_bible(tags: list[str], *, name: str = "") -> str:
    """Turn merged tags into one dense identity paragraph for Subject.bible."""
    if not tags:
        return ""
    seen: set[str] = set()
    ordered: list[str] = []
    for t in tags:
        k = t.lower().strip()
        if not k or k in seen or _NOISE_TAGS.match(k):
            continue
        seen.add(k)
        ordered.append(t.strip())
    if not ordered:
        return ""

    costume = [t for t in ordered if _COSTUME_TAGS.search(t)]
    rest = [t for t in ordered if t not in costume]
    who = (name or "the subject").strip()

    if costume:
        # Prose that reads as a fixed costumed look, not a tag dump.
        body = ", ".join(costume + rest)
        return (
            f"{who}: costumed figure — {body}. "
            f"Keep this exact costume and silhouette in every shot — do not invent "
            f"different armor, cape, belt, gloves, boots, eye style, or colors."
        )

    body = ", ".join(ordered)
    return (
        f"{who}: {body}. "
        f"Keep this exact appearance in every shot — do not invent different hair, "
        f"body type, or eyewear."
    )


def short_identity_marks(tags: list[str], *, max_chars: int = 200) -> str:
    """Compact marks string for training captions (not a full invented bible)."""
    if not tags:
        return ""
    priority = []
    rest = []
    for t in tags:
        if _NOISE_TAGS.match(t.strip()):
            continue
        if (
            _COSTUME_TAGS.search(t)
            or _HAIR_TAGS.search(t)
            or _EYEWEAR_TAGS.search(t)
            or _BUILD_TAGS.search(t)
        ):
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
    from backend.services.character_identity_prompt import resolve_class_token
    cfg = dict(getattr(subject, "training_settings_json", None) or {})
    cfg["bible_vision_grounded"] = True
    cfg["bible_vision_tags"] = list(result.get("tags") or [])[:32]
    cfg["bible_identity_marks"] = result.get("marks") or ""
    cls = resolve_class_token(subject, tags=list(result.get("tags") or []))
    cfg["class_token"] = cls
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
                class_token=cls,
                overwrite=True,
            )
            result["captions_refreshed"] = True
        except Exception as e:  # noqa: BLE001
            log.warning("persist_bible: caption refresh failed: %s", e)
            result["captions_refreshed"] = False
            result["caption_error"] = str(e)[:200]
    return result
