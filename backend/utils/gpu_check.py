"""GPU/generation-activity checks for autoresearch politeness.

An overnight research run must never fight the user's image/video generation
for the GPU. Two independent signals, both fail-soft to "not busy" (a broken
probe must not permanently stall research):

- VRAM pressure via nvidia-smi (same probe pattern as the swarm plugin's
  resource monitor).
- An active ComfyUI job (its queue endpoint reports running items).
"""
import logging
import subprocess

logger = logging.getLogger(__name__)

VRAM_BUSY_THRESHOLD_PCT = 60.0
COMFYUI_URL = "http://127.0.0.1:8188"


def vram_used_pct() -> float:
    """Percent of VRAM in use across GPUs (max), or 0.0 if unprobeable."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return 0.0
        worst = 0.0
        for line in out.stdout.strip().splitlines():
            used_s, total_s = line.split(",")
            used, total = float(used_s), float(total_s)
            if total > 0:
                worst = max(worst, used / total * 100.0)
        return worst
    except Exception as e:
        logger.debug(f"VRAM probe failed: {e}")
        return 0.0


def comfyui_generating() -> bool:
    """Is ComfyUI actively rendering something right now?"""
    try:
        import requests
        resp = requests.get(f"{COMFYUI_URL}/queue", timeout=5)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get("queue_running"))
    except Exception:
        return False


def gpu_busy(threshold_pct: float = VRAM_BUSY_THRESHOLD_PCT) -> bool:
    """Should a research run yield the GPU right now?"""
    if comfyui_generating():
        return True
    return vram_used_pct() > threshold_pct
