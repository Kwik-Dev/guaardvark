"""Declared facts about the identity-check methods, apart from the code that runs them.

Everything here is data plus the two readers that only read it. The methods live
in ``video_consistency_metrics``; what each one is allowed to claim lives here,
next to the thresholds it is judged against.

A method's score is a number a person will act on — retrain, or ship the LoRA —
so it is withheld until it has earned the right to be shown. Earning it means a
row in ``measured`` for the model actually used whose 95% lower confidence bound
clears ``floor``. Until then the method still runs, but the Cast page reads
"Identity: not measured" rather than a number nobody has checked. This replaces a
"size" method that divided the smoke PNG's byte count by the mean reference byte
count and showed the result as an identity score; it carried no identity signal
and was green above 0.75 on file compression alone.

``measured`` rows ship empty on purpose. scripts/eval_identity_methods.py builds
labelled pairs from the Cast library on the machine it runs on and prints the row
to paste in; a row measured on someone else's set is not evidence here.

Row fields:
    model     the model the row was measured with, as the method names it
              (``embed``: the encoder repo id; ``vlm``: the Ollama tag)
    digest    build fingerprint of that model — the Ollama manifest digest
              prefix for ``vlm``, the weights' sha256 prefix for ``embed``.
              A rebuilt model under the same name is a different model.
    correct   labelled pairs the method got right at ``threshold``
    n         labelled pairs tried
    date      when it was measured
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Accuracy a method must prove on labelled pairs before its score may be shown.
# 0.9 is what makes the number worth reading: one wrong call in ten is a person
# retraining a LoRA that was fine, or shipping one that drifted. Below that the
# score costs more than it tells, which is the lesson of the "size" method.
DEFAULT_IDENTITY_FLOOR = 0.9

# Confidence for the lower bound. A method that happens to go 9-for-9 has not
# proven 0.9 — the bound is what stops a lucky short run from unlocking a score.
IDENTITY_CONFIDENCE = 0.95

IDENTITY_METHODS: Dict[str, Dict[str, Any]] = {
    "embed": {
        "threshold": 0.6,
        "floor": DEFAULT_IDENTITY_FLOOR,
        "encoder": "facebook/dinov2-base",
        "license": "apache-2.0",
        "measured": [],
        "why": (
            "Cosine of self-supervised image features, which separate subjects "
            "without being trained on faces — so no face-recognition weights and "
            "no non-commercial research licence. 0.6 is where DINOv2 cosine is "
            "reported to sit between same-instance and different-instance pairs; "
            "it is a starting point to be replaced by whatever the harness "
            "measures on this machine's Cast library, not a measured value."
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

# Tried in this order on the smoke path; the first one that both runs and is
# proven for the model it used wins. ``embed`` first: it is deterministic and
# costs no Ollama load, so a box with the encoder installed never spins up a VLM.
IDENTITY_METHOD_ORDER = ("embed", "vlm")

NOT_PROVEN_REASON = "not yet proven on labelled pairs"


def accuracy_lower_bound(correct: int, n: int) -> float:
    """95% lower bound on a method's accuracy from ``correct`` of ``n`` pairs.

    Jeffreys-style beta posterior with a uniform prior, the same shape the
    decision gate uses (see decision_data.MEASURED_ACCURACY). Returns 0.0 when
    scipy is unavailable, which withholds the score rather than granting it.
    """
    if n <= 0 or correct < 0 or correct > n:
        return 0.0
    try:
        from scipy.stats import beta
    except Exception:  # noqa: BLE001 - a missing scipy must not unlock a score
        return 0.0
    return float(beta.ppf(1.0 - IDENTITY_CONFIDENCE, 1 + correct, 1 + n - correct))


def proven_for_model(method: str, model: Optional[str]) -> bool:
    """True when ``method`` has a measured row for ``model`` that clears its floor.

    An unknown method, an unknown model, or an empty ``measured`` list is not
    proven. Rows for other models do not transfer: the question is whether the
    model that produced this score has been checked.
    """
    spec = IDENTITY_METHODS.get(method) or {}
    if not model:
        return False
    floor = spec.get("floor", DEFAULT_IDENTITY_FLOOR)
    for row in spec.get("measured") or []:
        if row.get("model") != model:
            continue
        if accuracy_lower_bound(int(row.get("correct", 0)), int(row.get("n", 0))) >= floor:
            return True
    return False
