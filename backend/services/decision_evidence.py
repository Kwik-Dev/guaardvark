"""How much a measured result proves.

A decision may run on a model only when the model has earned it on the
decision's labelled set. The strict rule: the lower confidence bound of its
accuracy, not the plain score, must reach the decision's floor. A perfect
short run proves less than it looks: 35 of 35 right is 1.0 observed and 0.920
at the bound, which does not clear a 0.95 floor.

The constants live in ``decision_data.EVIDENCE``. ``lcb`` is also the bound the
Cast identity methods are judged by (``identity_method_data``).
"""
from __future__ import annotations

import math
from typing import Optional

from backend.services.decision_data import EVIDENCE

# needed_to_qualify gives up past this many extra items: a floor that far away
# is not reachable by growing the set.
_MAX_EXTRA_ITEMS = 10_000_000


def lcb(correct: int, n: int, q: Optional[float] = None) -> float:
    """Lower bound on accuracy from ``correct`` of ``n``: the ``q`` quantile of
    the Beta(1 + correct, 1 + n - correct) posterior (a uniform prior).

    ``q`` defaults to EVIDENCE["lcb_q"]. Returns 0.0 for an impossible count
    or when scipy is unavailable, which withholds rather than grants.
    """
    q = EVIDENCE["lcb_q"] if q is None else q
    if n <= 0 or correct < 0 or correct > n or not 0.0 < q < 1.0:
        return 0.0
    try:
        from scipy.stats import beta
    except Exception:  # noqa: BLE001 - a missing scipy must not qualify anything
        return 0.0
    return float(beta.ppf(q, 1 + correct, 1 + n - correct))


def _bound_below(floor: float, correct: int, n: int, q: float) -> bool:
    """True while lcb(correct, n, q) is still below ``floor``.

    The posterior is Beta(correct + 1, misses + 1), whose CDF at ``floor`` is
    the chance of at most ``misses`` failures in n + 1 trials that each pass
    with probability ``floor``: a finite sum, so no quantile search is needed.
    The bound is below the floor exactly when that CDF is above ``q``.
    """
    misses, trials = n - correct, n + 1
    log_f, log_miss = math.log(floor), math.log1p(-floor)
    cdf = 0.0
    for j in range(misses + 1):
        log_comb = math.lgamma(trials + 1) - math.lgamma(j + 1) - math.lgamma(trials - j + 1)
        cdf += math.exp(log_comb + (trials - j) * log_f + j * log_miss)
    return cdf > q


def needed_to_qualify(correct: int, n: int, floor: float, q: Optional[float] = None) -> Optional[int]:
    """How many more labelled items, all answered right, before the bound
    reaches ``floor``; 0 when it already does, None when no number of items
    would (a floor of 1 or more, or an impossible count).

    With no misses the answer is closed form: the bound of c of c is
    q ** (1 / (c + 1)), so c + 1 must reach ln q / ln floor. With misses the
    count is the first k for which the finite sum in _bound_below drops to q.
    """
    q = EVIDENCE["lcb_q"] if q is None else q
    if n < 0 or correct < 0 or correct > n or not 0.0 < q < 1.0:
        return None
    if floor <= 0.0:
        return 0
    if floor >= 1.0:
        return None
    if n - correct == 0:
        k = max(0, math.ceil(math.log(q) / math.log(floor)) - (n + 1))
        # Step off a boundary that floating point rounded the wrong way.
        while k > 0 and not _bound_below(floor, correct + k - 1, n + k - 1, q):
            k -= 1
        while _bound_below(floor, correct + k, n + k, q):
            k += 1
        return k
    if not _bound_below(floor, correct, n, q):
        return 0
    low, high = 0, 1
    while _bound_below(floor, correct + high, n + high, q):
        low, high = high, high * 2
        if high > _MAX_EXTRA_ITEMS:
            return None
    while high - low > 1:
        mid = (low + high) // 2
        if _bound_below(floor, correct + mid, n + mid, q):
            low = mid
        else:
            high = mid
    return high
