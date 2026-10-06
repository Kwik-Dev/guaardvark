"""Post-generate Cast sheet angle verification (vision) + relabel / one regen.

Plan labels (``SubjectSample.angle``) come from the Casting Director taxonomy, not
from looking at the PNG. Image models often ignore "profile right" etc. This
module uses VisionAnalyzer (gemma4:e4b preferred) to classify the finished still
against the same closed label set, then:

  1. On mismatch → one auto-regen with a strengthened framing lead (same planned slot).
  2. Always relabel the sample to what vision sees on the final image (honest UI).

Never raises for vision failures. A check that could not run or whose reply could
not be read (missing image, vision error or timeout, unparseable reply) returns
ok=False and match=None, "angle unverified": never counted as a match, so generate
continues without claiming it. When vision read the image but the plan has no
known label, match is None as well, with the observed label still reported.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# SubjectSample.angle_state values. 'verified': the label was read from the final
# image. 'unverified': the check could not run and the planned label stands.
ANGLE_VERIFIED = "verified"
ANGLE_UNVERIFIED = "unverified"

# Closed set — must stay aligned with character_generator_service._ANGLE_WEIGHTS labels.
CANONICAL_ANGLES = (
    "face-forward",
    "three-quarter left",
    "three-quarter right",
    "profile left",
    "profile right",
    "full-body front",
    "full-body three-quarter",
)

_ANGLE_ALIASES = {
    "front": "face-forward",
    "frontal": "face-forward",
    "face forward": "face-forward",
    "close-up": "face-forward",
    "headshot": "face-forward",
    "portrait": "face-forward",
    "3/4 left": "three-quarter left",
    "3/4 right": "three-quarter right",
    "three quarter left": "three-quarter left",
    "three quarter right": "three-quarter right",
    "left profile": "profile left",
    "right profile": "profile right",
    "side left": "profile left",
    "side right": "profile right",
    "full body": "full-body front",
    "full-body": "full-body front",
    "full body front": "full-body front",
    "full body three-quarter": "full-body three-quarter",
    "full-body 3/4": "full-body three-quarter",
    "wide shot": "full-body front",
}

# Fuzzy lookup tries the longest label first, on word boundaries, so a longer
# label wins over a shorter one inside it: "full-body three-quarter left" is
# full-body three-quarter (not three-quarter left), and "full body, front-facing"
# is full-body front (not the alias "front"). A trailing "s" is allowed so plurals
# such as "headshots" or "wide shots" still match.
_FUZZY_LABELS = tuple(
    (re.compile(rf"\b{re.escape(label)}s?\b"), canon)
    for label, canon in sorted(
        [(c, c) for c in CANONICAL_ANGLES] + list(_ANGLE_ALIASES.items()),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)

_CLASSIFY_PROMPT = (
    "Classify the camera framing of the MAIN person in this image. "
    "Reply with EXACTLY one label from this list and nothing else:\n"
    + "\n".join(f"- {a}" for a in CANONICAL_ANGLES)
    + "\n\nRules: profile = face in pure side view; three-quarter = between front and profile; "
    "full-body = feet or full legs visible / head-to-toe; face-forward = face fills most of frame. "
    "Output only the label."
)


def normalize_angle(label: str | None) -> Optional[str]:
    """Map free text / taxonomy label to a canonical angle, or None if unknown."""
    if not label:
        return None
    t = re.sub(r"\s+", " ", str(label).strip().lower().replace("_", "-"))
    t = t.replace("–", "-").replace("—", "-")
    if t in CANONICAL_ANGLES:
        return t
    if t in _ANGLE_ALIASES:
        return _ANGLE_ALIASES[t]
    for pattern, canon in _FUZZY_LABELS:
        if pattern.search(t):
            return canon
    if "profile" in t and "left" in t:
        return "profile left"
    if "profile" in t and "right" in t:
        return "profile right"
    if "full" in t and "body" in t:
        if "three" in t or "3/4" in t or "quarter" in t:
            return "full-body three-quarter"
        return "full-body front"
    if "three" in t and "quarter" in t:
        if "left" in t:
            return "three-quarter left"
        if "right" in t:
            return "three-quarter right"
    return None


def angles_match(planned: str | None, observed: str | None) -> Optional[bool]:
    """True when labels agree (after normalize), False when they differ, None when
    either label is unknown: nothing was compared, so it is not a match."""
    p = normalize_angle(planned)
    o = normalize_angle(observed)
    if o is None or p is None:
        return None
    if p == o:
        return True
    # Soft: full-body front vs three-quarter both count as full-body coverage for regen skip?
    # No — user asked for mismatch regen; only exact canonical match counts.
    return False


def framing_for_angle(angle: str | None) -> str:
    a = normalize_angle(angle) or ""
    if a.startswith("full-body"):
        return "full-body"
    if a.startswith("profile"):
        return "close-up"
    if a.startswith("three-quarter"):
        return "medium"
    return "close-up"


def strengthen_prompt_for_angle(prompt: str, planned_angle: str) -> str:
    """Front-load an emphatic framing lead for a one-shot regen."""
    angle = normalize_angle(planned_angle) or (planned_angle or "").strip()
    leads = {
        "profile left": (
            "strict left profile view, face in pure side silhouette facing left, "
            "only one eye visible, ear visible, NOT front-facing, NOT three-quarter"
        ),
        "profile right": (
            "strict right profile view, face in pure side silhouette facing right, "
            "only one eye visible, ear visible, NOT front-facing, NOT three-quarter"
        ),
        "three-quarter left": (
            "three-quarter view from the left, face angled ~45 degrees, both eyes partly visible"
        ),
        "three-quarter right": (
            "three-quarter view from the right, face angled ~45 degrees, both eyes partly visible"
        ),
        "face-forward": (
            "face-forward portrait, subject looking at camera, head-and-shoulders, NOT full body"
        ),
        "full-body front": (
            "full body shot, head to toe, entire figure visible, feet visible, front-facing, wide framing"
        ),
        "full-body three-quarter": (
            "full body shot, head to toe, entire figure visible, feet visible, three-quarter stance"
        ),
    }
    lead = leads.get(angle, angle)
    base = (prompt or "").strip()
    # Avoid stacking the same lead twice
    if lead and lead.lower()[:40] in base.lower():
        return base
    return f"{lead}. {base}" if base else lead


def classify_image_angle(image_path: str, *, analyzer=None) -> dict[str, Any]:
    """Vision-classify a still. Returns {ok, angle, raw, model, error}."""
    path = Path(image_path)
    if not path.is_file():
        return {"ok": False, "angle": None, "raw": "", "error": "missing image"}
    try:
        from PIL import Image
        from backend.utils.vision_analyzer import VisionAnalyzer
        az = analyzer or VisionAnalyzer()
        img = Image.open(str(path)).convert("RGB")
        res = az.analyze(img, _CLASSIFY_PROMPT, think=False, temperature=0.1, num_predict=48)
        if not getattr(res, "success", False):
            return {
                "ok": False,
                "angle": None,
                "raw": "",
                "model": getattr(res, "model_used", ""),
                "error": getattr(res, "error", "vision failed"),
            }
        raw = (getattr(res, "description", "") or "").strip()
        # First line / strip quotes
        raw_line = raw.splitlines()[0].strip().strip('"').strip("'")
        angle = normalize_angle(raw_line)
        if angle is None:
            # try whole blob
            angle = normalize_angle(raw)
        return {
            "ok": bool(angle),
            "angle": angle,
            "raw": raw[:200],
            "model": getattr(res, "model_used", ""),
            "error": None if angle else f"unparsed: {raw_line[:80]}",
        }
    except Exception as e:  # noqa: BLE001
        log.warning("classify_image_angle failed for %s: %s", image_path, e)
        return {"ok": False, "angle": None, "raw": "", "error": str(e)[:200]}


def apply_relabel(sample, observed_angle: str) -> None:
    """Update sample.angle / framing to the observed canonical label."""
    canon = normalize_angle(observed_angle)
    if not canon:
        return
    sample.angle = canon
    sample.framing = framing_for_angle(canon)


def verify_sample_angle(
    image_path: str,
    planned_angle: str | None,
    *,
    analyzer=None,
) -> dict[str, Any]:
    """Classify still and compare to plan. Does not mutate DB or regenerate.

    ``match`` is True or False only when the classifier read the image and both
    labels are known; otherwise it is None. ``ok`` says whether the image was
    read at all: ok=False is an unverified angle, while ok=True with match=None
    means the plan had no known label to compare against.
    """
    clf = classify_image_angle(image_path, analyzer=analyzer)
    observed = clf.get("angle")
    match = angles_match(planned_angle, observed) if clf.get("ok") else None
    return {
        "ok": clf.get("ok", False),
        "match": match,
        "planned": normalize_angle(planned_angle) or planned_angle,
        "observed": observed,
        "raw": clf.get("raw"),
        "model": clf.get("model"),
        "error": clf.get("error"),
    }
