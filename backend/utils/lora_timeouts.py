"""LoRA trainer time budgets, derived from one platform flag.

On current ``main`` the same single-source derivation lives in
``backend/utils/platform.py`` (``IS_APPLE_SILICON`` / ``lora_train_time_budgets``,
added with the macOS/MPS support work). This workspace predates that module, so
the identical logic lives here until the workspace is rebased onto main.

Stock (CUDA) keeps the values measured on main: a healthy Z-Image subject
finishes well inside the 30 min train cap, so a run still going at 30 min is
wedged and the reaper frees the Subject 15 min later. Apple Silicon (MPS) stages
each heavy module and runs ~11s/step, so the default 640-step schedule is ~2h —
anchored by a real 75 min / 400-step MPS run; the old 30 min CUDA-era cap killed
a healthy run mid-training (the Elara run failed at step 151/640).

The trainer daemon load/train caps, the Celery task limits, and the
stuck-training reaper cutoff are all derived from this one flag so the four
numbers cannot drift apart.
"""
from __future__ import annotations

import sys


IS_APPLE_SILICON = sys.platform == "darwin"


def lora_train_time_budgets(
    apple_silicon: bool,
) -> tuple[int, int, int | None, int | None, int]:
    """Return (load, train, task_soft, task_hard, reap) seconds for a platform.

    Pure so the non-Darwin numbers can be asserted in tests on any host. On stock
    CUDA the task limits are ``None`` so the global Celery ``task_soft_time_limit``
    / ``task_time_limit`` apply unchanged.
    """
    load = 3600 if apple_silicon else 900     # cold HF download / model load
    train = 10800 if apple_silicon else 1800  # per train call
    if apple_silicon:
        task_soft = load + train + 300   # 245 min: leaves room for teardown
        task_hard = load + train + 900   # 255 min: > 3h train + 1h load
        reap = task_hard + 900           # 270 min: never a false positive
    else:
        task_soft = task_hard = None     # global Celery limits apply
        reap = 45 * 60                   # > 30 min train cap + 15 min slack
    return load, train, task_soft, task_hard, reap


(
    LORA_LOAD_TIMEOUT_S,
    LORA_TRAIN_TIMEOUT_S,
    LORA_TRAIN_TASK_SOFT_TIME_LIMIT_S,
    LORA_TRAIN_TASK_TIME_LIMIT_S,
    LORA_REAP_STUCK_AFTER_S,
) = lora_train_time_budgets(IS_APPLE_SILICON)
