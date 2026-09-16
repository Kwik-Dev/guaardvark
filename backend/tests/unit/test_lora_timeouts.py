"""The LoRA trainer time budgets derive from one platform flag, so a future edit
cannot silently widen the stock CUDA caps (a wedged NVIDIA run would then hold
the GPU for hours)."""
from __future__ import annotations

from backend.utils import lora_timeouts as lt


def test_stock_cuda_unchanged_from_main():
    load, train, task_soft, task_hard, reap = lt.lora_train_time_budgets(False)
    assert (load, train) == (900, 1800)
    # None → the global task_soft_time_limit / task_time_limit apply.
    assert task_soft is None and task_hard is None
    assert reap == 45 * 60


def test_apple_silicon_derived_from_one_flag():
    load, train, task_soft, task_hard, reap = lt.lora_train_time_budgets(True)
    assert (load, train) == (3600, 10800)
    # The task limits and reaper are derived, not independent literals.
    assert task_soft == load + train + 300
    assert task_hard == load + train + 900
    assert reap == task_hard + 900
    assert task_hard > load + train  # the daemon train cap is never cut off
