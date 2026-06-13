"""GpuResourcePolicy — one front door composing the existing GPU-coordination layers.

Design: docs/local-workspace-only/GPU_RESOURCE_POLICY_DESIGN.md

The repo grew FOUR decoupled GPU-coordination layers — JobOperationGate (exclusivity),
GPUMemoryOrchestrator (VRAM-MB budget), GPUResourceCoordinator (cross-process file lock),
GlobalLoadGate (RAM admission) — plus scattered ad-hoc VRAM-reclaim hacks (ComfyUI /free,
4 copies of Ollama keep_alive=0 eviction). Because the gate does no VRAM math and the
orchestrator isn't called by jobs, a gate holder eats ~14GB the orchestrator never debits
and both can believe they own the 16GB card.

This module does NOT replace those layers — it COMPOSES them so exclusivity and VRAM
reclaim/budget become ONE operation. It is strictly ADDITIVE: existing
``gate.gpu_exclusive`` callers keep working untouched; new/critical paths opt into
``gpu_session(...)``. The VRAM-budget debit is an OPT-IN param (off by default) so adopting
this in one caller never changes another's behavior.

Invariants preserved (see design doc): the gate's 8s release cooldown + fail-fast
``GpuBusyError`` (we delegate straight to ``gpu_exclusive``), the lock-ordering rule, and
the ``plugin_runner`` CUDA-fork sidecar (this module spawns no processes). Reclaim runs
only AFTER the slot is claimed — we never evict on behalf of a job that lost the slot.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Iterator, Optional

log = logging.getLogger(__name__)

COMFYUI_URL = "http://127.0.0.1:8188"


# --- Canonical VRAM reclaim (consolidates the scattered ad-hoc hacks) ---------

def free_comfyui_vram(*, timeout: float = 15.0) -> bool:
    """Unload ComfyUI's resident models (POST /free). Best-effort; never raises.

    Canonical home for the FLUX→i2v eviction the i2v custom nodes need — they move
    models onto CUDA without asking ComfyUI to evict first, so a ~10GB FLUX stays
    resident and the animator OOMs. Was inlined in music_video_tasks; centralized so
    every image→video handoff can reuse the one implementation. Returns True on a
    successful POST, False if ComfyUI was unreachable (non-fatal either way).
    """
    import requests
    try:
        requests.post(
            f"{COMFYUI_URL}/free",
            json={"unload_models": True, "free_memory": True},
            timeout=timeout,
        )
        log.info("comfyui VRAM freed")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("comfyui /free failed (non-fatal): %s", e)
        return False


def evict_ollama_models() -> bool:
    """Evict Ollama's resident models so a render gets the card. Best-effort.

    Delegates to the proven ``GPUResourceCoordinator.unload_ollama_models`` (keep_alive=0
    with num_ctx=1 to avoid a large KV-cache alloc during unload) — one canonical call
    meant to converge the 4 ad-hoc copies (bark / unified_chat_engine / coordinator /
    orchestrator). Never raises.
    """
    try:
        from backend.services.gpu_resource_coordinator import unload_ollama_models as _unload
        _unload()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("ollama eviction failed (non-fatal): %s", e)
        return False


def reclaim_gpu(*, evict_ollama: bool = False, free_comfyui: bool = False) -> None:
    """Run the requested VRAM reclaims before a render uses the card. Best-effort."""
    if evict_ollama:
        evict_ollama_models()
    if free_comfyui:
        free_comfyui_vram()


# --- Orchestrator budget hooks (opt-in) --------------------------------------

def _orchestrator_request(slot_id: str, vram_estimate_mb: int) -> None:
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        get_orchestrator().request_model(slot_id, vram_estimate_mb, priority=95, exclusive=False)
    except Exception as e:  # noqa: BLE001
        log.warning("orchestrator request_model(%s) failed (non-fatal): %s", slot_id, e)


def _orchestrator_release(slot_id: str) -> None:
    try:
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        get_orchestrator().release_model(slot_id)
    except Exception as e:  # noqa: BLE001
        log.warning("orchestrator release_model(%s) failed (non-fatal): %s", slot_id, e)


# --- The front door -----------------------------------------------------------

@contextlib.contextmanager
def gpu_session(
    kind,
    op_id: str,
    *,
    on_busy: str = "raise",
    evict_ollama: bool = False,
    free_comfyui: bool = False,
    vram_estimate_mb: Optional[int] = None,
    slot_id: Optional[str] = None,
) -> Iterator[bool]:
    """Claim the GPU for a unit of work — exclusivity + VRAM reclaim/budget in one place.

    Wraps ``JobOperationGate.gpu_exclusive(kind, op_id, on_busy)`` — preserving its
    fail-fast ``GpuBusyError`` and 8s post-release cooldown EXACTLY — and additionally,
    once the slot is actually held:
      * runs ``reclaim_gpu(evict_ollama, free_comfyui)`` (evict only after we win), and
      * optionally debits the GPUMemoryOrchestrator budget when ``vram_estimate_mb`` is
        given (makes 'exclusive' and 'VRAM-budgeted' the same fact), releasing on exit.

    With all defaults this is a pure pass-through to the gate (no eviction, no budget),
    so adopting it in one caller never changes another's behavior. Yields the gate's
    acquired bool (False only in the degraded ``on_busy='register'`` path).
    """
    from backend.services.job_operation_gate import get_gate

    gate = get_gate()
    _slot = slot_id or f"{getattr(kind, 'value', kind)}:{op_id}"
    acquired = False
    try:
        with gate.gpu_exclusive(kind, op_id, on_busy=on_busy) as acq:
            acquired = acq
            if acquired:
                reclaim_gpu(evict_ollama=evict_ollama, free_comfyui=free_comfyui)
                if vram_estimate_mb:
                    _orchestrator_request(_slot, vram_estimate_mb)
            yield acquired
            # Success path for the unit of work: transition LOADING -> LOADED so
            # the orchestrator's tracked_vram and eviction scoring are accurate.
            # Particularly important for high-estimate VIDEO_RENDER slots used by
            # music-video / film-crew (the main ~14GB consumers). Without this,
            # slots linger as LOADING and inflate tracked / prevent proper idle
            # eviction (vram specialist rec).
            if acquired and vram_estimate_mb:
                try:
                    from backend.services.gpu_memory_orchestrator import get_orchestrator
                    get_orchestrator().mark_model_loaded(_slot)
                except Exception:
                    pass  # best-effort; release below will still run
    finally:
        if acquired and vram_estimate_mb:
            _orchestrator_release(_slot)

            # Proactive cleanup for VIDEO slots on gpu_session release (vram specialist rec):
            # If this was a high-VRAM video_render (music-video, film-crew, etc.), free
            # ComfyUI resident models and force-evict the slot from the orchestrator
            # registry so tracked_vram drops immediately (instead of waiting for idle
            # timeout or next exclusive route). Prevents lingering LOADED/LOADING bookings
            # after a ~14GB render finishes. Best-effort, non-fatal.
            slot_lower = _slot.lower()
            if "video" in slot_lower or "video_render" in slot_lower:
                try:
                    free_comfyui_vram()
                    from backend.services.gpu_memory_orchestrator import get_orchestrator
                    get_orchestrator().force_evict(_slot)
                    log.info(f"Proactive free_comfyui + force_evict for video slot {_slot} on release")
                except Exception:
                    pass
