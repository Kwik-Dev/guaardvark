"""Best-effort smoke render after a successful cast LoRA train."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def run_lora_smoke_test(
    *,
    subject_id: int,
    lora_path: str,
    trigger_word: str,
    resolution: int = 768,
    base_model_id: str | None = None,
    ref_image_paths: list[str] | None = None,
) -> dict:
    """Generate one quick still with the new LoRA via character_still_pipeline.

    Also scores identity vs training refs when available. Non-fatal on failure.
    """
    token = (trigger_word or "").strip() or f"subject_{subject_id}"
    out_dir = Path(lora_path).parent / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"smoke_{subject_id}.png")
    prompt = f"a photo of {token}, portrait, neutral studio lighting, sharp focus"
    res = max(512, (int(resolution) // 64) * 64)

    try:
        from backend.services.gpu_resource_policy import gpu_session
        from backend.services.job_types import JobKind
        from backend.services.character_still_pipeline import render_character_still
        from backend.services.media_model_registry import read_lora_sidecar

        meta = read_lora_sidecar(lora_path) or {}
        bid = base_model_id or meta.get("base_model_id")

        with gpu_session(
            JobKind.LORA_TRAIN,
            f"smoke_{subject_id}",
            evict_ollama=True,
            free_comfyui=True,
            vram_estimate_mb=11000,
            require_fit=True,
        ):
            still = render_character_still(
                prompt,
                lora_paths=[lora_path],
                apply_subject_loras=True,
                include_bible=False,
                source="smoke",
                width=res,
                height=res,
                seed=42,
                output_path=out_path,
                keep_pipeline=False,
            )

        if not still.success or not still.image_path or not Path(still.image_path).is_file():
            return {"ok": False, "error": still.error or "smoke image missing", "base_model_id": bid}

        identity = {}
        refs = [p for p in (ref_image_paths or []) if p and Path(p).is_file()][:8]
        if refs:
            try:
                from backend.services.video_consistency_metrics import score_smoke_vs_refs
                m = score_smoke_vs_refs(refs, still.image_path)
                identity = m.get("identity") or {}
                log.info(
                    "lora smoke identity for subject %s: score=%s method=%s",
                    subject_id, identity.get("score"), identity.get("method"),
                )
            except Exception as e:
                log.debug("smoke identity score skipped: %s", e)

        # Persist onto Subject.training_settings_json for Cast UI.
        try:
            from backend.models import db, Subject
            s = db.session.get(Subject, subject_id)
            if s is not None:
                cfg = dict(s.training_settings_json or {})
                cfg["smoke_identity"] = {
                    "ok": True,
                    "path": still.image_path,
                    "score": identity.get("score"),
                    "method": identity.get("method"),
                    "base_model_id": bid or still.metadata.get("base_model_id"),
                    "family": still.metadata.get("family"),
                    "lora_strength": still.metadata.get("lora_strength"),
                }
                s.training_settings_json = cfg
                db.session.commit()
        except Exception as e:
            log.debug("could not persist smoke_identity: %s", e)

        log.info("lora smoke ok for subject %s → %s", subject_id, still.image_path)
        return {
            "ok": True,
            "path": still.image_path,
            "base_model_id": bid or still.metadata.get("base_model_id"),
            "identity": identity,
            "family": still.metadata.get("family"),
            "lora_strength": still.metadata.get("lora_strength"),
        }
    except Exception as e:
        log.warning("lora smoke test failed for subject %s (non-fatal): %s", subject_id, e)
        return {"ok": False, "error": str(e)}
