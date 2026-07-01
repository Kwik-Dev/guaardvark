"""Shared LoRA training dispatch for Cast Studio and Production casting."""
from __future__ import annotations

import logging

from backend.models import db, Subject

log = logging.getLogger(__name__)


def dispatch_lora_train(subject_id: int) -> dict:
    """Create a unified progress job and dispatch ``lora_trainer.train_lora``.

    Caller must set ``subject.training_status = 'training'`` and commit before
    calling. Returns ``{"task_id": str, "job_id": str}``.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        raise ValueError(f"subject {subject_id} not found")
    if s.training_status != "training":
        raise ValueError(
            f"subject {subject_id} must be in 'training' status before dispatch "
            f"(got {s.training_status!r})"
        )

    from backend.celery_app import celery
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType

    progress = get_unified_progress()
    job_id = progress.create_process(
        ProcessType.TRAINING,
        f"LoRA training for cast subject {subject_id} ({s.name})",
        additional_data={
            "subject_id": subject_id,
            "operation": "train_lora",
            "kind": "cast_training",
        },
    )

    s.current_training_job_id = job_id
    s.training_error = None
    db.session.commit()

    try:
        task = celery.send_task("lora_trainer.train_lora", args=[subject_id, job_id])
    except Exception:
        try:
            progress.error_process(job_id, "Dispatch failed")
        except Exception:
            pass
        s.training_status = "untrained"
        s.current_training_job_id = None
        db.session.commit()
        raise

    return {"task_id": task.id, "job_id": job_id}


def cancel_lora_train(subject_id: int) -> dict:
    """Best-effort cancel of an in-flight Cast LoRA training run."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return {"cancelled": False, "reason": "not_found"}
    if s.training_status != "training":
        return {
            "cancelled": False,
            "reason": "not_training",
            "training_status": s.training_status,
        }

    job_id = s.current_training_job_id

    try:
        from plugins.lora_trainer.real_trainer import _TRAINER
        _TRAINER.shutdown()
    except Exception:
        log.warning(
            "cancel_lora_train: trainer shutdown failed for subject %s",
            subject_id,
            exc_info=True,
        )

    if job_id:
        try:
            from backend.utils.unified_progress_system import get_unified_progress
            get_unified_progress().cancel_process(job_id, reason="Cancelled by user")
        except Exception:
            pass

    s.training_status = "failed"
    s.training_error = "Cancelled by user"
    s.current_training_job_id = None
    db.session.commit()

    return {"cancelled": True, "subject_id": subject_id, "job_id": job_id}