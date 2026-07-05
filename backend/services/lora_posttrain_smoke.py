"""Best-effort SDXL smoke render after a successful cast LoRA train."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# SDXL still + rank-16 LoRA on a 16GB card after training teardown.
_SMOKE_VRAM_MB = 9000


def run_lora_smoke_test(
    *,
    subject_id: int,
    lora_path: str,
    trigger_word: str,
    resolution: int = 768,
) -> dict:
    """Generate one quick SDXL still with the new LoRA. Non-fatal on failure."""
    token = (trigger_word or "").strip() or f"subject_{subject_id}"
    out_dir = Path(lora_path).parent / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"smoke_{subject_id}.png")
    prompt = f"a photo of {token}, portrait, neutral studio lighting, sharp focus"

    try:
        from backend.services.comfyui_image_generator import ComfyUIImageGenerator
        from backend.services.gpu_resource_policy import gpu_session, free_comfyui_vram
        from backend.services.job_types import JobKind

        res = max(512, (int(resolution) // 64) * 64)
        with gpu_session(
            JobKind.LORA_TRAIN,
            f"smoke_{subject_id}",
            evict_ollama=True,
            free_comfyui=True,
            vram_estimate_mb=_SMOKE_VRAM_MB,
            require_fit=True,
        ):
            ComfyUIImageGenerator(lora_strength=0.25).generate_image(
                prompt=prompt,
                loras=[lora_path],
                output_path=out_path,
                width=res,
                height=res,
                steps=12,
                model="sdxl",
            )
        free_comfyui_vram()
        if Path(out_path).is_file() and Path(out_path).stat().st_size > 0:
            log.info("lora smoke test ok for subject %s → %s", subject_id, out_path)
            # Wire the new observability starter (non-fatal).
            try:
                from backend.services.video_consistency_metrics import score_smoke_vs_refs
                # We don't have the original ref list here easily; pass [] so it degrades gracefully.
                # Callers that do (lora_trainer_tasks) can enhance later.
                m = score_smoke_vs_refs([], out_path)
                if m.get("identity", {}).get("score"):
                    log.info("smoke identity baseline score: %s", m["identity"]["score"])
            except Exception:
                pass
            return {"ok": True, "path": out_path}
        return {"ok": False, "error": "smoke image missing after generation"}
    except Exception as e:
        log.warning("lora smoke test failed for subject %s (non-fatal): %s", subject_id, e)
        return {"ok": False, "error": str(e)}