"""Declared facts about the identity-check methods, apart from the code that runs them.

Everything here is data plus the two readers that only read it. The methods live
in ``video_consistency_metrics``; what each one is allowed to claim lives here,
next to the thresholds it is judged against.

A method's score is a number a person will act on — retrain, or ship the LoRA —
so it is withheld until it has earned the right to be shown. Earning it means a
row in ``measured`` for the model actually used whose 95% lower confidence bound
clears ``floor``. Until then the method still runs, but the Cast page reads
"Identity: not measured" rather than a number nobody has checked. A file-size
ratio is not an identity signal and is never used for this score.

``measured`` rows are evidence, not settings. scripts/eval_identity_methods.py
builds labelled pairs from the Cast library on the machine it runs on and prints
the row to paste in. A row only speaks for the exact build it was measured with:
``embed`` reads pinned encoder weights that are byte-identical on every machine
that installs them, so its row ships, and its ``set`` says what it was measured
on; a row from a person's own library is better evidence for theirs. ``vlm``
ships without one, because its model is whichever vision model the machine has.

Row fields:
    model     the model the row was measured with, as the method names it
              (``embed``: the encoder repo id; ``vlm``: the Ollama tag)
    digest    build fingerprint of that model — the Ollama manifest digest
              prefix for ``vlm``, the weights' sha256 prefix for ``embed``.
              A rebuilt model under the same name is a different model.
    threshold the pass mark it was scored at; a row counts only for a method
              declared at that same threshold
    correct   labelled pairs the method got right at ``threshold``
    n         labelled pairs tried
    date      when it was measured
    revision  (``embed``) the encoder commit the weights came from
    set       what the pairs were, in words
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Accuracy a method must prove on labelled pairs before its score may be shown.
# 0.9 is what makes the number worth reading: one wrong call in ten is a person
# retraining a LoRA that was fine, or shipping one that drifted. Below that the
# score costs more than it tells.
DEFAULT_IDENTITY_FLOOR = 0.9

# Confidence for the lower bound. A method that happens to go 9-for-9 has not
# proven 0.9 — the bound is what stops a lucky short run from unlocking a score.
IDENTITY_CONFIDENCE = 0.95

IDENTITY_METHODS: Dict[str, Dict[str, Any]] = {
    "embed": {
        "threshold": 0.34,
        "floor": DEFAULT_IDENTITY_FLOOR,
        "encoder": "facebook/dinov2-base",
        "license": "apache-2.0",
        "measured": [
            {
                "model": "facebook/dinov2-base",
                "digest": "d73036b56966",
                "revision": "f9e44c814b77203eaa57a6bdbbd535f21ede1415",
                "threshold": 0.34,
                "correct": 307,
                "n": 320,
                "date": "2026-10-16",
                "set": (
                    "Cast library of the measuring machine: 2 characters with 5 and 32 "
                    "images; 160 same-character and 160 different-character pairs "
                    "(harness --threshold 0.34 --seed 1 --pairs 320)."
                ),
            },
        ],
        "why": (
            "Cosine of self-supervised image features (the pooled CLS token of "
            "the default 224 px centre crop), which separate subjects without being "
            "trained on faces — so no face-recognition weights and no non-commercial "
            "research licence. It reads the whole picture, not only the face: outfit, "
            "hair and framing weigh as much as features, so someone else dressed and "
            "framed like the character can come close to the pass mark. "
            "0.34 was measured 2026-10-16 at the pinned revision, on the shape the "
            "smoke check uses (one picture against the mean of up to 8 references): "
            "34 pictures against their own character's references scored 0.25-0.70 "
            "(median 0.55) and 47 against someone else's -0.01-0.39, the latter "
            "including 9 other people chosen for a setting or outfit like a "
            "character's. 0.34 sits between the highest of those under it (0.307) "
            "and the lowest same-character score over it (0.365): 32 of 34 same pass, "
            "46 of 47 different are refused, the one through being a woman's "
            "waist-up portrait (0.393). Letterboxing "
            "instead of cropping, 448 px input and patch-token pooling were tried on "
            "the same pictures and separated them no better. The row was then scored "
            "on separately drawn Cast pairs; the RGB colour histogram scored 164 of "
            "those 320 at its best cut, no better than chance. Two characters is a "
            "thin set — the bound treats the 320 pairs as independent and they are "
            "not — so a row measured on a larger library should replace this one."
        ),
    },
    "vlm": {
        "threshold": 0.66,
        "floor": DEFAULT_IDENTITY_FLOOR,
        "refs_compared": 3,
        "measured": [],
        "why": (
            "Fraction of up to 3 reference photos the installed vision model calls "
            "the same person or character, so the only scores it can produce are "
            "0, 1/3, 2/3 and 1. 0.66 selects 2 of 3 — a majority of the "
            "references, so one disagreeing reference does not sink a good render "
            "and one agreeing reference does not carry a bad one. It must sit just "
            "below 2/3 and not at 0.67, which 0.6667 fails. Unmeasured."
        ),
    },
}

# The image encoders the "embed" method can read, keyed by the name the method's
# ``encoder`` field uses. Each is one download, fetched only by its Install
# button (Settings > Generation > Cast identity check) into
# data/models/identity_encoder/<folder>/ — never at start-up and never by a
# check. ``revision`` pins the Hugging Face commit and ``weights_sha256`` the
# weights file, so every machine compares with the weights a measured row was
# taken with; a new pin is a new model and needs its own row. ``size_bytes`` is
# the sum of ``files`` at that commit, from the Hub's file listing (2026-10-16),
# and is what install progress is measured against.
IMAGE_ENCODERS: Dict[str, Dict[str, Any]] = {
    "facebook/dinov2-base": {
        "name": "DINOv2 base",
        "license": "Apache-2.0",
        "revision": "f9e44c814b77203eaa57a6bdbbd535f21ede1415",
        "files": ("config.json", "preprocessor_config.json", "model.safetensors"),
        "weights_file": "model.safetensors",
        "weights_sha256": "d73036b56966966d07975d696bde331762f37297e2f095de8cea0040c3aa0841",
        "size_bytes": 346_346_896,
        "folder": "dinov2-base",
    },
}

# Tried in this order on the smoke path; the first one that both runs and is
# proven for the model it used wins. ``embed`` first: it is deterministic and
# costs no Ollama load, so a box with the encoder installed never spins up a VLM.
IDENTITY_METHOD_ORDER = ("embed", "vlm")

NOT_PROVEN_REASON = "not yet proven on labelled pairs"


def accuracy_lower_bound(correct: int, n: int) -> float:
    """95% lower bound on a method's accuracy from ``correct`` of ``n`` pairs.

    The beta posterior bound the decision gate uses (decision_evidence.lcb),
    at IDENTITY_CONFIDENCE. Returns 0.0 when scipy is unavailable, which
    withholds the score rather than granting it.
    """
    from backend.services.decision_evidence import lcb

    return lcb(correct, n, q=1.0 - IDENTITY_CONFIDENCE)


def proven_for_model(method: str, model: Optional[str], digest: Optional[str] = None) -> bool:
    """True when ``method`` has a measured row for ``model`` that clears its floor.

    An unknown method, an unknown model, or an empty ``measured`` list is not
    proven. Rows for other models do not transfer: the question is whether the
    model that produced this score has been checked. With ``digest`` (the build
    actually loaded), a row must carry a digest it starts with — a rebuilt model
    under the same name is a different model. A row scored at another pass mark
    than the one declared says nothing about the declared one.
    """
    spec = IDENTITY_METHODS.get(method) or {}
    if not model:
        return False
    floor = spec.get("floor", DEFAULT_IDENTITY_FLOOR)
    for row in spec.get("measured") or []:
        if row.get("model") != model:
            continue
        row_digest = str(row.get("digest") or "")
        if digest and not (row_digest and digest.startswith(row_digest)):
            continue
        if "threshold" in row and row["threshold"] != spec.get("threshold"):
            continue
        if accuracy_lower_bound(int(row.get("correct", 0)), int(row.get("n", 0))) >= floor:
            return True
    return False
