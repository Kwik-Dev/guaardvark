"""The strict qualification statistics: a lower bound, and how far a row is from its floor.

The four reference rows are measured decision_data rows; the numbers are
what the bound says about them at a 0.05 quantile.
"""
from __future__ import annotations

import pytest

from backend.services import decision_evidence as E
from backend.services.decision_data import EVIDENCE


@pytest.mark.parametrize("correct, n, floor, bound, needed", [
    (35, 35, 0.95, 0.920, 23),    # chat_route
    (30, 30, 0.95, 0.908, 28),    # image_intent
    (46, 47, 0.95, 0.905, 45),    # needs_live_data
    (27, 27, 0.98, 0.899, 121),   # accepted_offer, at the gate floor
])
def test_reference_rows(correct, n, floor, bound, needed):
    assert round(E.lcb(correct, n), 3) == bound
    assert E.needed_to_qualify(correct, n, floor) == needed


def test_the_default_quantile_is_declared_in_data():
    assert EVIDENCE["lcb_q"] == 0.05
    assert E.lcb(35, 35) == E.lcb(35, 35, q=0.05)


def test_needed_items_are_exactly_enough():
    for correct, n, floor in ((35, 35, 0.95), (46, 47, 0.95), (90, 95, 0.9), (0, 0, 0.5)):
        k = E.needed_to_qualify(correct, n, floor)
        assert E.lcb(correct + k, n + k) >= floor
        if k:
            assert E.lcb(correct + k - 1, n + k - 1) < floor


def test_a_row_already_over_the_floor_needs_nothing():
    assert E.needed_to_qualify(59, 59, 0.95) == 0
    assert E.lcb(59, 59) >= 0.95


def test_an_unreachable_floor_has_no_answer():
    assert E.needed_to_qualify(10, 10, 1.0) is None


@pytest.mark.parametrize("correct, n", [(0, 0), (5, 3), (-1, 4)])
def test_an_impossible_count_proves_nothing(correct, n):
    assert E.lcb(correct, n) == 0.0


def test_scipy_missing_proves_nothing(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "scipy.stats", None)
    assert E.lcb(100, 100) == 0.0


def test_the_identity_methods_use_the_same_bound():
    from backend.services.identity_method_data import IDENTITY_CONFIDENCE, accuracy_lower_bound

    assert accuracy_lower_bound(96, 100) == E.lcb(96, 100, q=1.0 - IDENTITY_CONFIDENCE)
