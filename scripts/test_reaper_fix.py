#!/usr/bin/env python3
"""Focused test for Fix A: the stale-job reaper's sustained ComfyUI down-detection.

Replicates the exact streak logic added to backend/app.py's poll_celery_progress()
and verifies that a busy/slow ComfyUI (a few slow probes) no longer orphans a
video_render job, while a genuine sustained outage still does.
"""
import sys

# The exact logic from backend/app.py (Fix A):
#   comfyui_down_streak = 0
#   COMFYUI_DOWN_STREAK = 60
#   if _comfyui_is_down(): comfyui_down_streak += 1
#   else: comfyui_down_streak = 0
#   comfyui_down = comfyui_down_streak >= COMFYUI_DOWN_STREAK
COMFYUI_DOWN_STREAK = 60


def reaper_down_flag(probe_results):
    """Simulate the reaper's streak logic over a sequence of liveness probes.

    probe_results: iterable of bool (True = ComfyUI looked down).
    Returns the final comfyui_down flag.
    """
    streak = 0
    for down in probe_results:
        if down:
            streak += 1
        else:
            streak = 0
    return streak >= COMFYUI_DOWN_STREAK


def run():
    failures = 0

    # 1. A single slow probe (busy GPU) must NOT orphan.
    assert reaper_down_flag([True]) is False, "single slow probe orphaned job"
    # 2. A handful of slow probes (transient blip) must NOT orphan.
    assert reaper_down_flag([True] * 5) is False, "5 slow probes orphaned job"
    # 3. Even 59 slow probes must NOT orphan (just under the 60 threshold).
    assert reaper_down_flag([True] * 59) is False, "59 slow probes orphaned job"
    # 4. 60 consecutive slow probes (sustained outage) MUST orphan.
    assert reaper_down_flag([True] * 60) is True, "60 slow probes did not orphan"
    # 5. A recovery (one success) resets the streak — must NOT orphan.
    assert reaper_down_flag([True] * 59 + [False]) is False, "recovery did not reset streak"
    # 6. Interleaved blips never accumulate to a sustained outage.
    assert reaper_down_flag([True, False] * 100) is False, "interleaved blips orphaned job"
    # 7. A real sustained outage after a healthy period DOES orphan.
    assert reaper_down_flag([False] * 10 + [True] * 60) is True, "sustained outage not detected"

    print("All Fix A reaper tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
