"""Character Generator Celery tasks.

Two tasks hang off the same GPU-exclusivity rail used by the storyboard artist
(``JobKind.VIDEO_RENDER``) because FLUX is the same image model — running two
FLUX jobs simultaneously on the 16 GB card would OOM.

Task names:
  character.generate_samples(subject_id)   — full plan + image loop
  character.regen_sample(sample_id, ...)   — single image regen

Registration: call ``create_character_generation_tasks(celery_app)`` from
``celery_app.py`` exactly like ``create_production_swarm_tasks``.
"""
from __future__ import annotations

import logging
import os
import random
from pathlib import Path

from celery import Celery
from flask import current_app

log = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────────

def _sample_output_dir(subject_id: int) -> Path:
    """Canonical on-disk location for a subject's generated reference images.

    Mirrors _storyboard_path() in production_swarm_tasks: everything under
    data/outputs/ so it lives in the DATA_DIR subtree (backed up, served by
    the static-file route).
    """
    try:
        from backend.config import STORAGE_DIR
        base = Path(STORAGE_DIR) / "outputs" / "character_samples" / str(subject_id)
    except Exception:
        base = Path("data") / "outputs" / "character_samples" / str(subject_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _sample_image_path(subject_id: int, index: int) -> str:
    return str(_sample_output_dir(subject_id) / f"sample_{index}.png")


# ── task implementations (plain functions for testability) ─────────────────────

def generate_samples(subject_id: int, job_id: str | None = None, use_lora: bool = False) -> dict:
    """Plan + generate the full reference-sheet for a Subject.

    Flow:
      1. Load the Subject row; validate it exists.
      2. Call generate_character_sheet() (LLM-only, GPU-free).
      3. Persist bible + trigger_word onto the Subject.
      4. Delete any existing SubjectSample rows (idempotent re-plan).
      5. Insert one SubjectSample per shot (status=pending).
      6. Loop shots under gpu_exclusive, generating each image via FLUX.
      7. Update each row status→done/failed + image_path.

    Mirror of run_storyboard_artist: same gate, same commit pattern, same
    error surface (raises on ComfyUI unavailability so the Celery task retries
    or marks itself failed — no silent rot).

    job_id (optional): unified progress process id from dispatch. When present we
    drive updates so the job appears in unified queue / Activity / sockets for
    long batch runs of many cast items.
    """
    from backend.models import db, Subject, SubjectSample
    from backend.services.character_generator_service import generate_character_sheet
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.services.job_operation_gate import get_gate
    from backend.services.job_types import JobKind
    from backend.services.plugin_bridge import ensure_plugins_for_stage

    subject = db.session.get(Subject, subject_id)
    if subject is None:
        log.error("generate_samples: Subject %s not found", subject_id)
        return {"error": "subject_not_found"}

    # --- 1. LLM planning (GPU-free) -----------------------------------------
    log.info("Character Generator: planning sheet for subject %s (%s)", subject_id, subject.name)
    plan = generate_character_sheet(
        name=subject.name,
        kind=subject.kind,
        description=subject.description or "",
        trigger_word=subject.trigger_word or None,
    )

    # Per user vision for the Generate Character tab:
    # - Initially (no or partial training): bare generations produce the reference sheet for training.
    # - After training: the same tab / mechanism should be usable to generate *additional* images
    #   that stay consistent with the trained/evolved character (new outfits, tattoos, environments).
    # Current impl intentionally uses no loras (this *is* the data that will train the LoRA).
    # Future: accept flag or check subject.lora_path and pass loras + strength to
    # image_generator.generate_image when desired for "flesh out using trained identity".

    if plan.get("error"):
        log.error("Character Generator: bible generation failed for subject %s: %s",
                  subject_id, plan["error"])
        if job_id:
            try:
                from backend.utils.unified_progress_system import get_unified_progress
                get_unified_progress().error_process(job_id, plan["error"])
            except Exception:
                pass
        return {"error": plan["error"]}

    bible = plan["bible"]
    trigger = plan["trigger_word"]
    shots = plan["shots"]

    # --- 2. Persist bible + trigger on Subject --------------------------------
    subject.bible = bible
    if trigger:
        subject.trigger_word = trigger
    db.session.flush()

    # --- 3. Idempotent re-plan: delete existing samples ----------------------
    SubjectSample.query.filter_by(subject_id=subject_id).delete()
    db.session.flush()

    # --- 4. Insert SubjectSample rows (status=pending) -----------------------
    sample_rows: list[SubjectSample] = []
    for shot in shots:
        row = SubjectSample(
            subject_id=subject_id,
            index=shot["index"],
            angle=shot.get("angle") or "",
            framing=shot.get("framing") or "",
            expression=shot.get("expression") or "",
            lighting=shot.get("lighting") or "",
            scene=shot.get("scene") or "",
            image_prompt=shot.get("image_prompt") or "",
            placeholder=bool(shot.get("placeholder", False)),
            status="pending",
            approved=False,
        )
        db.session.add(row)
        sample_rows.append(row)

    db.session.commit()
    log.info("Character Generator: inserted %d SubjectSample rows for subject %s",
             len(sample_rows), subject_id)

    if job_id:
        try:
            from backend.utils.unified_progress_system import get_unified_progress
            get_unified_progress().update_process(job_id, 15, f"Plan complete — {len(sample_rows)} shots ready for FLUX")
        except Exception:
            pass

    # --- 5. Image generation loop (GPU-exclusive) ----------------------------
    ensure_plugins_for_stage("film-crew", "storyboard_gen")  # ensures comfyui plugin is up
    image_generator = ComfyUIImageGenerator(model="flux-schnell")
    gate = get_gate()

    # Refresh rows now that they have PKs (after commit).
    sample_rows = SubjectSample.query.filter_by(subject_id=subject_id).order_by(SubjectSample.index).all()

    loras_for_gen = []
    if use_lora and subject.lora_path:
        loras_for_gen = [subject.lora_path]
        log.info("Character Generator: using trained LoRA for additional consistent images: %s", subject.lora_path)

    done_count = 0
    failed_count = 0

    from backend.utils.unified_progress_system import get_unified_progress
    total = len(sample_rows)

    if job_id:
        try:
            get_unified_progress().update_process(job_id, 5, f"Starting FLUX generation of {total} samples")
        except Exception:
            pass

    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, f"char_samples_{subject_id}"):
        for idx, row in enumerate(sample_rows):
            output_path = _sample_image_path(subject_id, row.index)
            seed = random.randint(1, 2 ** 31 - 1)
            row.status = "generating"
            row.seed = seed
            db.session.commit()

            pct = int(((idx) / total) * 100) if total else 0
            if job_id:
                try:
                    get_unified_progress().update_process(
                        job_id, max(5, pct),
                        f"Generating sample {idx+1}/{total} ({row.angle or 'shot'})"
                    )
                except Exception:
                    pass

            try:
                image_generator.generate_image(
                    prompt=row.image_prompt or subject.name,
                    loras=loras_for_gen,
                    output_path=output_path,
                    seed=seed,
                    model="flux-schnell",
                )
                row.image_path = output_path
                row.status = "done"
                done_count += 1
                # Write a textfile caption sidecar next to the image so the generated set is
                # trainable as-is (SimpleTuner caption_strategy="textfile"). The image_prompt
                # already front-loads the trigger + bible + per-shot framing variation, so it
                # IS the caption. (Re-caption later with scripts/caption_dataset.py for a VLM
                # description of the actual render if higher fidelity is wanted.) Best-effort —
                # a sidecar failure must never fail the sample.
                try:
                    from pathlib import Path as _P
                    cap = (row.image_prompt or subject.name or "").strip()
                    if cap:
                        _P(output_path).with_suffix(".txt").write_text(cap + "\n", encoding="utf-8")
                except Exception as _se:  # noqa: BLE001
                    log.warning("Character Generator: caption sidecar failed for sample %d: %s",
                                row.index, _se)
                log.info("Character Generator: sample %d/%d done (%s)",
                         row.index + 1, len(sample_rows), output_path)
            except Exception as exc:
                row.status = "failed"
                failed_count += 1
                log.error("Character Generator: sample %d failed: %s", row.index, exc)
            finally:
                db.session.commit()

            if job_id:
                try:
                    get_unified_progress().update_process(
                        job_id, int(((idx + 1) / total) * 100),
                        f"Sample {idx+1}/{total} complete"
                    )
                except Exception:
                    pass

    if job_id:
        try:
            get_unified_progress().complete_process(
                job_id,
                f"Character sample generation finished: {done_count} done, {failed_count} failed",
                additional_data={"subject_id": subject_id, "done": done_count, "failed": failed_count, "total": total}
            )
        except Exception:
            pass

    log.info("Character Generator: finished subject %s — %d done, %d failed",
             subject_id, done_count, failed_count)
    return {"subject_id": subject_id, "done": done_count, "failed": failed_count,
            "total": len(sample_rows)}


def regen_sample(sample_id: int, prompt_override: str | None = None, seed: int | None = None, job_id: str | None = None) -> dict:
    """Regenerate the image for a single SubjectSample.

    Cloned from regen_storyboard_shot: same GPU gate, same single-commit pattern.
    prompt_override replaces the stored image_prompt for this generation only
    (the stored prompt is NOT overwritten — the user is exploring, not editing the
    plan).  Pass seed=None for a fresh random seed.
    """
    from backend.models import db, SubjectSample
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.services.job_operation_gate import get_gate
    from backend.services.job_types import JobKind
    from backend.services.plugin_bridge import ensure_plugins_for_stage

    from backend.utils.unified_progress_system import get_unified_progress

    row = db.session.get(SubjectSample, sample_id)
    if row is None:
        log.error("regen_sample: SubjectSample %s not found", sample_id)
        if job_id:
            try:
                get_unified_progress().error_process(job_id, "sample not found")
            except Exception:
                pass
        return {"error": "sample_not_found"}

    ensure_plugins_for_stage("film-crew", "storyboard_gen")
    image_generator = ComfyUIImageGenerator(model="flux-schnell")
    gate = get_gate()

    effective_seed = seed if seed is not None else random.randint(1, 2 ** 31 - 1)
    effective_prompt = prompt_override if prompt_override else (row.image_prompt or "")
    output_path = _sample_image_path(row.subject_id, row.index)

    row.status = "generating"
    row.seed = effective_seed
    db.session.commit()

    if job_id:
        try:
            get_unified_progress().update_process(job_id, 10, "Starting regen")
        except Exception:
            pass

    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, f"char_regen_{row.subject_id}"):
        try:
            if job_id:
                try:
                    get_unified_progress().update_process(job_id, 50, "Generating image")
                except Exception:
                    pass
            image_generator.generate_image(
                prompt=effective_prompt,
                loras=[],
                output_path=output_path,
                seed=effective_seed,
                model="flux-schnell",
            )
            row.image_path = output_path
            row.status = "done"
            log.info("regen_sample: sample %s regenerated → %s", sample_id, output_path)
            if job_id:
                try:
                    get_unified_progress().update_process(job_id, 90, "Image ready")
                except Exception:
                    pass
        except Exception as exc:
            row.status = "failed"
            log.error("regen_sample: sample %s failed: %s", sample_id, exc)
            if job_id:
                try:
                    get_unified_progress().error_process(job_id, str(exc))
                except Exception:
                    pass
        finally:
            db.session.commit()

    if job_id:
        try:
            get_unified_progress().complete_process(job_id, "Regen complete", additional_data={"sample_id": sample_id, "status": row.status})
        except Exception:
            pass

    return {"sample_id": sample_id, "status": row.status, "image_path": row.image_path}


# ── factory ────────────────────────────────────────────────────────────────────

def create_character_generation_tasks(celery_app: Celery):
    """Register character.* tasks with the Celery app.

    Called from backend/celery_app.py alongside create_production_swarm_tasks.
    """

    @celery_app.task(name="character.generate_samples")
    def generate_samples_task(subject_id: int, job_id: str | None = None, use_lora: bool = False):
        with current_app.app_context():
            return generate_samples(subject_id, job_id=job_id, use_lora=use_lora)

    @celery_app.task(name="character.regen_sample")
    def regen_sample_task(sample_id: int, prompt_override: str | None = None,
                          seed: int | None = None, job_id: str | None = None):
        with current_app.app_context():
            return regen_sample(sample_id, prompt_override=prompt_override, seed=seed, job_id=job_id)

    return {
        "generate_samples": generate_samples_task,
        "regen_sample": regen_sample_task,
    }
