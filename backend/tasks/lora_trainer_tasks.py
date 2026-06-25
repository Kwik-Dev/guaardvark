"""Celery wiring for the lora_trainer plugin.

Same factory pattern as production_swarm_tasks. The task body is intentionally
thin: load Subject, call mock_trainer (or real trainer in v1.1), persist
results. No state-machine interaction with Production — training is per-Subject
and the cast endpoint already records the user's chosen action."""
from __future__ import annotations
import logging
from celery import Celery
from flask import current_app

from backend.models import db, Subject

logger = logging.getLogger(__name__)


def _output_dir() -> str:
    return (current_app.config.get("LORA_OUTPUT_DIR")
            or "data/training/loras")


def _train_impl(subject_id: int) -> dict:
    """Picks mock or real trainer based on:
       1. GUAARDVARK_LORA_BACKEND env var (mock|real|auto, default auto)
       2. Auto: real if plugins/lora_trainer/venv-torch/bin/python exists, else mock.
       Logs which backend it picked."""
    import os
    s = db.session.get(Subject, subject_id)
    if s is None:
        return {"status": "failed", "error": f"subject {subject_id} not found"}

    # Training set = the subject's uploaded reference images (the primary Step-1
    # flow) UNION any APPROVED, done generated samples (the fallback: "no images?
    # generate some with the Character Generator, approve the good ones, train").
    # Without this union the approve step was cosmetic — approved samples never
    # reached the trainer, which only ever read ref_image_paths.
    from backend.models import SubjectSample
    train_images = list(s.ref_image_paths or [])
    approved = (
        SubjectSample.query
        .filter_by(subject_id=s.id, approved=True, status="done")
        .all()
    )
    for smp in approved:
        if smp.image_path and smp.image_path not in train_images:
            train_images.append(smp.image_path)

    backend = os.environ.get("GUAARDVARK_LORA_BACKEND", "auto").lower()
    use_real = False
    if backend == "real":
        use_real = True
    elif backend == "auto":
        from plugins.lora_trainer.real_trainer import RealLoraTrainer
        use_real = RealLoraTrainer.is_available()

    if use_real:
        from plugins.lora_trainer.real_trainer import RealLoraTrainer, _TRAINER
        logger.info(f"lora_trainer: using REAL backend for subject {subject_id}")
        # Real LoRA training is a full GPU load on the shared 16GB card — claim
        # the GPU exclusively (LORA_TRAIN slot) so it serializes against video
        # render / model finetune. The MOCK path below is CPU-only and is NOT
        # gated. On contention, return a clean failed result (rather than
        # raising) so train_subject_lora_for_subject marks the Subject 'failed'
        # instead of leaving it stuck in 'training'.
        from backend.services.job_operation_gate import GpuBusyError
        from backend.services.job_types import JobKind
        from backend.services.gpu_resource_policy import gpu_session
        try:
            # gpu_session = the gate's exclusivity PLUS VRAM reclaim once the slot
            # is won. evict_ollama/free_comfyui are the actual fix for Dean's OOM:
            # ollama keeps a chat model (~6GB) resident and ComfyUI can hold a FLUX,
            # which left no room for SDXL on the 16GB card. The bare gate did no
            # VRAM math, so training claimed "exclusive" while ollama still owned
            # 6.7GB → CUDA OOM. Reclaim runs only AFTER we hold the slot.
            with gpu_session(JobKind.LORA_TRAIN, f"subject_{s.id}",
                             evict_ollama=True, free_comfyui=True):
                try:
                    return _TRAINER.train_subject_lora(
                        subject_id=s.id,
                        subject_name=s.name,
                        trigger_word=s.trigger_word,
                        ref_image_paths=train_images,
                        output_dir=_output_dir(),
                    )
                finally:
                    # Free the ~7GB of SDXL the trainer daemon holds. Without this
                    # the daemon stays resident IDLE between jobs, and a single
                    # leftover daemon starves the next run on the shared 16GB card
                    # — the exact OOM Dean hit (subject 16: 137MiB free, a zombie
                    # daemon holding 6.7GB). Shutting down also drops any
                    # half-applied PEFT/LoRA state from a failed run. Reload on the
                    # next job is ~6s (model is disk-cached), a cheap price for not
                    # leaking the card. Best-effort: never let cleanup mask the
                    # real training result/error.
                    try:
                        _TRAINER.shutdown()
                    except Exception as _e:
                        logger.warning(f"lora_trainer: daemon shutdown after subject {subject_id} failed (non-fatal): {_e}")
        except GpuBusyError as e:
            logger.warning(f"lora_trainer: GPU busy for subject {subject_id}: {e}")
            return {"status": "failed", "error": f"GPU busy: {e}"}

    from plugins.lora_trainer.mock_trainer import train_subject_lora
    logger.info(f"lora_trainer: using MOCK backend for subject {subject_id}")
    return train_subject_lora(
        subject_id=s.id,
        subject_name=s.name,
        ref_image_paths=train_images,
        output_dir=_output_dir(),
    )


def create_lora_trainer_tasks(celery_app: Celery):
    @celery_app.task(name="lora_trainer.train_lora")
    def train_lora_task(subject_id: int):
        with current_app.app_context():
            train_subject_lora_for_subject(subject_id)

    @celery_app.task(name="lora_trainer.reap_stuck_training")
    def reap_stuck_training_task():
        with current_app.app_context():
            return reap_stuck_training_subjects()

    return {"train_lora": train_lora_task, "reap_stuck_training": reap_stuck_training_task}


def train_subject_lora_for_subject(subject_id: int) -> None:
    """Module-level entry point — directly callable from tests."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        logger.warning(f"train_lora called for unknown subject {subject_id}")
        return
    if s.training_status != "training":
        # Cast endpoint sets training_status='training' before dispatching.
        # If it's anything else, someone double-dispatched or the row was
        # raced. Idempotency: do nothing.
        logger.info(f"skip train_lora for subject {subject_id} (status={s.training_status!r})")
        return
    result = _train_impl(subject_id)
    if result.get("status") == "ok":
        s.lora_path = result["lora_path"]
        s.lora_version = result.get("lora_version", 1)
        s.training_status = "trained"
        s.training_error = None
    else:
        s.training_status = "failed"
        # Surface the reason on the Subject so the Cast card can show WHY it
        # failed instead of a dead-end 'failed' chip.
        s.training_error = (result.get("error") or "training failed")[:2000]
        logger.warning(f"lora train failed for subject {subject_id}: {result.get('error')}")
    db.session.commit()


def reap_stuck_training_subjects() -> dict:
    """Flip Subjects wedged in training_status='training' past the train cap to
    'failed' so the UI re-enables the Train button. A worker that dies mid-run
    (its trainer daemon now reaped by PR_SET_PDEATHSIG) loses the Celery task, so
    nothing marks the Subject failed — it would otherwise stay 'training' forever.
    The 45-minute cutoff is deliberately > the 30-min _TRAIN_TIMEOUT_S, so a job
    that is genuinely still running is never reaped. Uses the DB clock to avoid
    process/DB timezone skew."""
    from sqlalchemy import text
    stale = (
        Subject.query
        .filter(Subject.training_status == "training",
                Subject.updated_at < text("now() - interval '45 minutes'"))
        .all()
    )
    for s in stale:
        s.training_status = "failed"
        s.training_error = "Training did not finish (worker stopped or timed out). Safe to retry."
        logger.warning(f"reap_stuck_training: subject {s.id} ({s.name}) stuck in 'training' — marked failed")
    if stale:
        db.session.commit()
    return {"reaped": len(stale)}
