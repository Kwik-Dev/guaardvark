
import json
import logging
import math
import os
import random
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from celery import shared_task
from celery.exceptions import Retry

from backend.utils.clock import utcnow

try:
    from backend.models import db, TrainingJob, DeviceProfile
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType, ProcessStatus
    from backend.utils.progress_emitter import emit_progress_event
except ImportError as e:
    logging.error(f"Failed to import dependencies: {e}")
    db = TrainingJob = DeviceProfile = None
    get_unified_progress = None
    ProcessType = None
    ProcessStatus = None
    emit_progress_event = None

logger = logging.getLogger(__name__)

TRAINING_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "training"
PROCESSED_DIR = TRAINING_DIR / "processed"
MODELS_DIR = TRAINING_DIR / "models"
MODELFILES_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "data" / "modelfiles"

MODEL_TEMPLATES = {
    'llama-3': 'llama-3.1-instruct.modelfile',
    'llama3': 'llama-3.1-instruct.modelfile',
    'gemma-3': 'gemma-3-text.modelfile',
    'gemma3': 'gemma-3-text.modelfile',
    'gemma-2': 'gemma-3-text.modelfile',
    'gemma2': 'gemma-3-text.modelfile',
}

# Fields the filter reads a pair's quality score from, first match wins.
# plugins/training/scripts/quality_filter.py writes _quality_score; a plain
# "score" covers datasets scored by other tools.
PAIR_SCORE_FIELDS = ("_quality_score", "score")
MIN_SCORE_NOT_APPLIED = "min_score not applied: pairs have no score"


def _pair_score(pair: dict):
    """The pair's numeric quality score, or None when it carries none."""
    for field in PAIR_SCORE_FIELDS:
        value = pair.get(field)
        if value is None or isinstance(value, bool):
            continue
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if score == score:  # NaN is no score
            return score
    return None


def _update_job_status(job_id: str, **kwargs):
    if not db or not TrainingJob:
        logger.warning("Cannot update job status: models not available")
        return
    
    try:
        from flask import current_app
        with current_app.app_context():
            job = db.session.query(TrainingJob).filter(
                TrainingJob.job_id == job_id
            ).first()
            
            if not job:
                logger.error(f"Job not found: {job_id}")
                return
            
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            
            db.session.commit()
            logger.debug(f"Updated job {job_id}: {kwargs}")
    except Exception as e:
        logger.error(f"Error updating job status: {e}", exc_info=True)
        if db:
            db.session.rollback()


def _emit_progress(job_id: str, progress: int, message: str, status: str = "processing", metrics: dict = None):
    try:
        if emit_progress_event:
            if progress == 0 and status == "processing":
                emit_progress_event(
                    process_id=job_id,
                    progress=0,
                    message=message,
                    status="start",  # Use "start" to create the process
                    process_type="training",
                    additional_data={
                        "job_id": job_id,
                        "metrics": metrics or {}
                    }
                )
            else:
                emit_progress_event(
                    process_id=job_id,
                    progress=progress,
                    message=message,
                    status=status,
                    process_type="training",
                    additional_data={
                        "job_id": job_id,
                        "metrics": metrics or {}
                    }
                )
        elif get_unified_progress:
            progress_system = get_unified_progress()
            if progress_system:
                from backend.utils.unified_progress_system import ProcessStatus
                existing = progress_system.get_process(job_id)
                if not existing:
                    progress_system.create_process(
                        ProcessType.TRAINING,
                        message,
                        additional_data={"job_id": job_id, "metrics": metrics or {}},
                        process_id=job_id
                    )
                
                if status == "complete":
                    progress_system.complete_process(job_id, message, {"job_id": job_id, "metrics": metrics or {}})
                elif status == "error":
                    progress_system.error_process(job_id, message, {"job_id": job_id, "metrics": metrics or {}})
                elif status == "cancelled":
                    progress_system.cancel_process(job_id, message, {"job_id": job_id, "metrics": metrics or {}})
                else:
                    progress_system.update_process(job_id, progress, message, {"job_id": job_id, "metrics": metrics or {}})
    except Exception as e:
        logger.warning(f"Could not emit progress: {e}")


@shared_task(bind=True, name='training.parse_transcripts')
def parse_transcripts_task(self, job_id: str, input_path: str, recursive: bool = True):
    logger.info(f"Starting parse_transcripts_task for job {job_id}")
    
    try:
        _update_job_status(job_id, status="running", pipeline_stage="parsing", started_at=utcnow(), celery_task_id=self.request.id)
        _emit_progress(job_id, 0, "Starting transcript parsing...", "start")
        
        sys.path.insert(0, str(TRAINING_DIR / "scripts"))
        from transcript_parser import TranscriptParser
        
        parser = TranscriptParser(output_dir=str(PROCESSED_DIR))
        
        input_path_obj = Path(input_path)
        if not input_path_obj.exists():
            raise FileNotFoundError(f"Input path not found: {input_path}")
        
        _emit_progress(job_id, 10, f"Parsing transcripts from {input_path}...", "processing")
        
        if input_path_obj.is_file():
            pairs = parser.parse_file(str(input_path_obj))
        elif input_path_obj.is_dir():
            pairs = []
            pattern = "**/*" if recursive else "*"
            for file_path in input_path_obj.glob(pattern):
                if file_path.is_file() and file_path.suffix in ['.jsonl', '.json', '.md', '.txt', '.html', '.docx']:
                    file_pairs = parser.parse_file(str(file_path))
                    pairs.extend(file_pairs)
                    _emit_progress(job_id, 10 + int(80 * len(pairs) / max(1, len(list(input_path_obj.glob(pattern))))), 
                                 f"Parsed {len(pairs)} pairs from {file_path.name}...", "processing")
        else:
            raise ValueError(f"Invalid input path: {input_path}")
        
        output_filename = f"training_corpus_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        output_path = PROCESSED_DIR / output_filename
        
        _emit_progress(job_id, 90, f"Saving {len(pairs)} training pairs...", "processing")
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for pair in pairs:
                f.write(json.dumps(pair) + '\n')
        
        _update_job_status(job_id, 
                          status="completed",
                          pipeline_stage="parsing",
                          completed_at=utcnow(),
                          progress=100,
                          config_json=json.dumps({
                              "input_path": input_path,
                              "output_path": str(output_path),
                              "pairs_count": len(pairs)
                          }))
        
        _emit_progress(job_id, 100, f"Completed: {len(pairs)} training pairs saved to {output_filename}", "complete")
        
        logger.info(f"Parse task completed for job {job_id}: {len(pairs)} pairs")
        return {"output_path": str(output_path), "pairs_count": len(pairs)}
        
    except Exception as e:
        logger.error(f"Error in parse_transcripts_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


# Share of the full pipeline's progress bar the filter step fills.
PIPELINE_FILTER_PROGRESS = (20, 30)


@shared_task(bind=True, name='training.filter_dataset')
def filter_dataset_task(self, job_id: str, input_path: str, min_score: float = 0.5,
                        in_pipeline: bool = False):
    """Drop pairs that are empty, too short, too long, or scored below
    min_score. Pairs without a score are kept; when no pair has one, the job
    says min_score was not applied rather than implying it filtered.

    in_pipeline: called by full_training_pipeline_task, which owns the job's
    status, timestamps and config. The filter then reports its stage and
    progress only, and the pipeline records the returned report."""
    logger.info(f"Starting filter_dataset_task for job {job_id}")

    def emit(pct, message, status="processing"):
        if in_pipeline:
            low, high = PIPELINE_FILTER_PROGRESS
            _emit_progress(job_id, low + (high - low) * pct // 100, message, "processing")
        else:
            _emit_progress(job_id, pct, message, status)

    try:
        min_score = float(min_score)
        if in_pipeline:
            _update_job_status(job_id, pipeline_stage="filtering")
        else:
            _update_job_status(job_id, status="running", pipeline_stage="filtering", started_at=utcnow(), celery_task_id=self.request.id)
        emit(0, "Starting dataset filtering...", "start")
        
        input_path_obj = Path(input_path)
        if not input_path_obj.exists():
            raise FileNotFoundError(f"Input path not found: {input_path}")
        
        emit(10, f"Loading dataset from {input_path}...")
        
        # Read in binary and decode per line, so a line that is not UTF-8 or
        # not a JSON object is skipped and counted instead of failing the job.
        pairs = []
        skipped_lines = []
        with open(input_path_obj, 'rb') as f:
            for line_number, raw in enumerate(f, 1):
                if not raw.strip():
                    continue
                try:
                    pair = json.loads(raw.decode('utf-8'))
                except ValueError:
                    pair = None
                if isinstance(pair, dict):
                    pairs.append(pair)
                else:
                    skipped_lines.append(line_number)
        skipped_note = ""
        if skipped_lines:
            plural = "" if len(skipped_lines) == 1 else "s"
            skipped_note = f"{len(skipped_lines)} malformed line{plural} skipped"
        if skipped_lines:
            logger.warning(f"Filter job {job_id}: {skipped_note} in {input_path} "
                           f"(first: lines {skipped_lines[:10]})")

        scores = [_pair_score(pair) for pair in pairs]
        scored_count = sum(1 for score in scores if score is not None)
        min_score_applied = scored_count > 0
        score_rule = f"min_score={min_score}" if min_score_applied else MIN_SCORE_NOT_APPLIED
        if skipped_note:
            score_rule += f"; {skipped_note}"
        emit(30, f"Filtering {len(pairs)} pairs ({score_rule})...")
        
        filtered_pairs = []
        below_min_score = 0
        for i, (pair, score) in enumerate(zip(pairs, scores)):
            if not pair.get("instruction") or not pair.get("output"):
                continue

            inst_len = len(pair.get("instruction", ""))
            out_len = len(pair.get("output", ""))
            if inst_len < 10 or out_len < 10:
                continue
            if inst_len > 10000 or out_len > 10000:
                continue

            if score is not None and score < min_score:
                below_min_score += 1
                continue

            filtered_pairs.append(pair)

            if (i + 1) % 100 == 0:
                emit(30 + int(50 * (i + 1) / len(pairs)),
                     f"Filtered {len(filtered_pairs)}/{i+1} pairs...")

        if min_score_applied:
            score_summary = f"{below_min_score} below min_score {min_score}"
            if scored_count < len(pairs):
                score_summary += f"; {len(pairs) - scored_count} pairs without a score kept"
        else:
            score_summary = MIN_SCORE_NOT_APPLIED
        if skipped_note:
            score_summary += f"; {skipped_note}"

        output_filename = f"filtered_dataset_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        output_path = PROCESSED_DIR / output_filename

        emit(90, f"Saving {len(filtered_pairs)} filtered pairs...")

        with open(output_path, 'w', encoding='utf-8') as f:
            for pair in filtered_pairs:
                f.write(json.dumps(pair) + '\n')

        report = {
            "input_path": input_path,
            "output_path": str(output_path),
            "min_score": min_score,
            "min_score_applied": min_score_applied,
            "scored_count": scored_count,
            "below_min_score": below_min_score,
            "skipped_lines": len(skipped_lines),
            "original_count": len(pairs),
            "filtered_count": len(filtered_pairs),
        }
        if not min_score_applied:
            report["min_score_note"] = MIN_SCORE_NOT_APPLIED

        if in_pipeline:
            _update_job_status(job_id, pipeline_stage="filtering", progress=PIPELINE_FILTER_PROGRESS[1])
        else:
            _update_job_status(job_id,
                              status="completed",
                              pipeline_stage="filtering",
                              completed_at=utcnow(),
                              progress=100,
                              config_json=json.dumps(report))

        emit(100,
             f"Completed: {len(filtered_pairs)}/{len(pairs)} pairs passed filtering ({score_summary})",
             "complete")

        logger.info(f"Filter task completed for job {job_id}: {len(filtered_pairs)}/{len(pairs)} pairs ({score_summary})")
        return report
        
    except Exception as e:
        logger.error(f"Error in filter_dataset_task: {e}", exc_info=True)
        if not in_pipeline:
            _update_job_status(job_id, status="failed", error_message=str(e))
            _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


# Held-out evaluation for text fine-tunes. A slice of the dataset is kept out
# of training; afterwards the mean loss on it is measured for the trained
# adapter and for the base model, so a run that diverged can be told from one
# that learned. A job's config overrides the share with "eval_fraction"
# (0 turns the split off). None of these numbers has been measured against
# training runs yet.
EVAL_SPLIT = {
    # The usual 90/10 train/held-out split.
    "fraction": 0.1,
    # Fewer held-out rows than this and the mean loss rests on a handful of
    # examples, too few for a comparison with the base model to mean much, so
    # the split is skipped and the job says so. At 10% that is datasets under
    # 100 rows.
    "min_rows": 10,
    # Caps what a large dataset gives up and the time the two measuring passes
    # add: 200 rows of chat text is already tens of thousands of tokens.
    "max_rows": 200,
    # Fixed, so a resumed run holds out the same rows as its first attempt.
    "seed": 42,
}


def _hold_out_split(data_path: str, out_dir: Path, fraction: float):
    """Split a text dataset into training rows and held-out rows.

    Returns (train_path, eval_path, report). When nothing is held out,
    train_path is data_path unchanged, eval_path is None and report["reason"]
    says why. JSONL rows are copied byte for byte in their original order."""
    path = Path(data_path)
    report = {"fraction": fraction}
    if fraction <= 0:
        report["reason"] = "held-out split turned off (eval_fraction 0)"
        return data_path, None, report

    if path.suffix == ".jsonl":
        with open(path, "rb") as f:
            rows = [line.rstrip(b"\r\n") for line in f if line.strip()]
    elif path.suffix == ".json":
        with open(path, encoding="utf-8") as f:
            records = json.load(f)
        if not isinstance(records, list):
            report["reason"] = "dataset is not a list of rows"
            return data_path, None, report
        rows = [json.dumps(record).encode("utf-8") for record in records]
    else:
        report["reason"] = f"no held-out split for '{path.suffix}' datasets"
        return data_path, None, report

    held = min(int(len(rows) * fraction), EVAL_SPLIT["max_rows"])
    report["total_rows"] = len(rows)
    if held < EVAL_SPLIT["min_rows"]:
        report["reason"] = (
            f"dataset too small for a held-out split: {len(rows)} rows give {held} "
            f"at {fraction:.0%}, fewer than the {EVAL_SPLIT['min_rows']} needed"
        )
        return data_path, None, report

    held_out = set(random.Random(EVAL_SPLIT["seed"]).sample(range(len(rows)), held))
    out_dir.mkdir(parents=True, exist_ok=True)
    train_path = out_dir / "train.jsonl"
    eval_path = out_dir / "heldout.jsonl"
    with open(train_path, "wb") as train_file, open(eval_path, "wb") as eval_file:
        for i, row in enumerate(rows):
            (eval_file if i in held_out else train_file).write(row + b"\n")
    report.update(train_rows=len(rows) - held, eval_rows=held, eval_path=str(eval_path))
    return str(train_path), str(eval_path), report


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _record_heldout_losses(report: dict, result: dict):
    """Fold the trainer's held-out result into the job's eval report.

    A loss that is not a finite number is stored as None, since the job config
    is read as JSON in the browser and JSON has no NaN. For the adapter that is
    flagged: it is what a run that diverged looks like."""
    if "reason" in result:
        report["reason"] = result["reason"]
        return
    report["rows"] = result.get("rows")
    base, adapter = result.get("base_loss"), result.get("adapter_loss")
    if not _finite(base):
        report["reason"] = "the base model's held-out loss is not a finite number"
        return
    report["base_loss"] = float(base)
    report["adapter_loss"] = float(adapter) if _finite(adapter) else None
    if not _finite(adapter):
        report["adapter_loss_not_finite"] = True
    report["measured"] = True


def _heldout_summary(report: dict) -> str:
    if not report.get("measured"):
        return f"Held-out loss not measured: {report.get('reason', 'no result from the trainer')}"
    adapter = report["adapter_loss"]
    adapter_text = f"{adapter:.4f}" if adapter is not None else "not a finite number"
    return (f"Held-out loss {adapter_text} (base model {report['base_loss']:.4f}) "
            f"on {report['rows']} rows")


@shared_task(bind=True, name='training.finetune_model',
             soft_time_limit=86400, time_limit=172800)
def finetune_model_task(self, job_id: str, config: dict, resume: bool = False):
    logger.info(f"Starting finetune_model_task for job {job_id} (resume={resume})")

    current_pid = os.getpid()

    try:
        _update_job_status(
            job_id,
            status="running",
            pipeline_stage="training",
            started_at=utcnow(),
            celery_task_id=self.request.id,
            pid=current_pid
        )
        _emit_progress(job_id, 0, "Starting model fine-tuning...", "start")
        
        from flask import current_app
        with current_app.app_context():
            job = db.session.query(TrainingJob).filter(
                TrainingJob.job_id == job_id
            ).first()
            
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            
            job_config = json.loads(job.config_json) if job.config_json else {}
            device_profile = None
            if job.device_profile_id:
                device_profile = db.session.get(DeviceProfile, job.device_profile_id)
        
        base_model = job.base_model
        data_path = job_config.get("data_path") or job_config.get("dataset_path")
        images_path = job_config.get("images_path")

        if not data_path:
            raise ValueError("data_path not found in job config")
        
        output_name = job.output_model_name or f"guaardvark-{base_model.replace('/', '-').replace(':', '-')}"
        max_steps = job_config.get("steps", 500)
        learning_rate = job_config.get("lr", 2e-4)
        batch_size = job_config.get("batch_size", device_profile.max_batch_size if device_profile else 2)
        lora_rank = job_config.get("rank", 16)
        max_seq_length = job_config.get("seq_length", device_profile.max_seq_length if device_profile else 2048)
        offload_to_cpu = job_config.get("cpu_offload", device_profile.requires_cpu_offload if device_profile else False)
        
        _update_job_status(job_id, total_steps=max_steps)

        eval_report = {"measured": False}
        if images_path:
            train_path, eval_path = data_path, None
            eval_report["reason"] = "vision runs have no held-out evaluation yet"
        else:
            fraction = float(job_config.get("eval_fraction", EVAL_SPLIT["fraction"]))
            if not 0 <= fraction < 1:
                raise ValueError(f"eval_fraction must be at least 0 and below 1 (got {fraction})")
            train_path, eval_path, split = _hold_out_split(
                data_path, MODELS_DIR / output_name / "heldout", fraction)
            eval_report.update(split)
        if eval_path:
            _emit_progress(job_id, 3,
                           f"Held out {eval_report['eval_rows']} of {eval_report['total_rows']} rows "
                           f"to measure the trained model on", "processing")
        else:
            _emit_progress(job_id, 3, _heldout_summary(eval_report), "processing")

        def progress_callback(step, total_steps, loss, metrics):
            progress = int((step / total_steps) * 100) if total_steps > 0 else 0
            _update_job_status(job_id, 
                             current_step=step,
                             progress=progress,
                             metrics_json=json.dumps(metrics))
            _emit_progress(job_id, progress, 
                         f"Training step {step}/{total_steps} (loss: {loss:.4f})", 
                         "processing",
                         metrics)
        
        _emit_progress(job_id, 5, f"Loading model {base_model}...", "processing")
        
        sys.path.insert(0, str(Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "backend" / "services" / "training" / "scripts"))

        # Claim the GPU exclusively for the actual finetune() call — model
        # finetune is a full GPU load on the shared 16GB card. Claim at EXACTLY
        # this level: full_training_pipeline_task calls finetune_model_task as a
        # plain in-process function, so wrapping here covers the pipeline entry
        # point too WITHOUT a double-claim/double-release. On contention,
        # GpuBusyError propagates to the except-block below (marks job failed,
        # re-raises) rather than double-loading the GPU.
        from backend.services.gpu_resource_policy import gpu_session
        from backend.services.job_types import JobKind
        with gpu_session(JobKind.TRAINING, str(job_id), cross_process=True,
                         lease_seconds=4 * 3600):
            if images_path:
                 _emit_progress(job_id, 8, f"Detected vision task. Using vision trainer with images from {images_path}", "processing")
                 from finetune_vision import finetune

                 resume_msg = " (resuming from checkpoint)" if resume else ""
                 _emit_progress(job_id, 10, f"Starting vision training loop{resume_msg}...", "processing")
                 model_dir = finetune(
                    base_model=base_model,
                    data_path=data_path,
                    image_folder=images_path,
                    output_name=output_name,
                    max_steps=max_steps,
                    learning_rate=learning_rate,
                    batch_size=batch_size,
                    lora_rank=lora_rank,
                    max_seq_length=max_seq_length,
                    offload_to_cpu=offload_to_cpu,
                    progress_callback=progress_callback,
                    resume=resume
                )
            else:
                 from finetune_model import finetune

                 resume_msg = " (resuming from checkpoint)" if resume else ""
                 _emit_progress(job_id, 10, f"Starting text training loop{resume_msg}...", "processing")
                 model_dir = finetune(
                    base_model=base_model,
                    data_path=train_path,
                    output_name=output_name,
                    max_steps=max_steps,
                    learning_rate=learning_rate,
                    batch_size=batch_size,
                    lora_rank=lora_rank,
                    max_seq_length=max_seq_length,
                    offload_to_cpu=offload_to_cpu,
                    progress_callback=progress_callback,
                    resume=resume,
                    eval_data_path=eval_path,
                    eval_callback=lambda result: _record_heldout_losses(eval_report, result)
                )

        job_config["eval"] = eval_report
        heldout = _heldout_summary(eval_report)
        logger.info(f"Job {job_id}: {heldout}")

        checkpoint_dir = Path(model_dir) / "checkpoints"
        checkpoint_path = None
        if checkpoint_dir.exists():
            checkpoints = list(checkpoint_dir.glob("checkpoint-*"))
            if checkpoints:
                checkpoints.sort(key=lambda x: int(x.name.split("-")[1]) if "-" in x.name else 0)
                checkpoint_path = str(checkpoints[-1])

        _update_job_status(job_id,
                          status="completed",
                          pipeline_stage="training",
                          completed_at=utcnow(),
                          progress=100,
                          lora_path=str(Path(model_dir) / "lora"),
                          checkpoint_path=checkpoint_path,
                          is_resumable=bool(checkpoint_path),
                          pid=None,
                          config_json=json.dumps(job_config))

        _emit_progress(job_id, 100, f"Training complete! {heldout}. Model saved to {model_dir}", "complete")

        logger.info(f"Training task completed for job {job_id}: {model_dir}")
        return {"model_dir": model_dir, "lora_path": str(Path(model_dir) / "lora"), "eval": eval_report}

    except Exception as e:
        logger.error(f"Error in finetune_model_task: {e}", exc_info=True)

        checkpoint_path = None
        is_resumable = False
        try:
            output_name = config.get("output_name") or f"guaardvark-{config.get('base_model', 'model').replace('/', '-')}"
            checkpoint_dir = MODELS_DIR / output_name / "checkpoints"
            if checkpoint_dir.exists():
                checkpoints = list(checkpoint_dir.glob("checkpoint-*"))
                if checkpoints:
                    checkpoints.sort(key=lambda x: int(x.name.split("-")[1]) if "-" in x.name else 0)
                    checkpoint_path = str(checkpoints[-1])
                    is_resumable = True
        except Exception:
            pass

        _update_job_status(
            job_id,
            status="failed",
            error_message=str(e),
            checkpoint_path=checkpoint_path,
            is_resumable=is_resumable,
            pid=None
        )
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


@shared_task(bind=True, name='training.export_gguf')
def export_gguf_task(self, job_id: str, model_dir: str, quantization: str = 'q4_k_m'):
    logger.info(f"Starting export_gguf_task for job {job_id}")
    
    try:
        _update_job_status(job_id, status="running", pipeline_stage="exporting", started_at=utcnow(), celery_task_id=self.request.id)
        _emit_progress(job_id, 0, "Starting GGUF export...", "start")
        
        _emit_progress(job_id, 10, "Loading model for export...", "processing")
        
        sys.path.insert(0, str(Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "backend" / "services" / "training" / "scripts"))
        from finetune_model import export_to_gguf
        
        _emit_progress(job_id, 20, f"Exporting to GGUF ({quantization})...", "processing")
        
        gguf_path = export_to_gguf(model_dir, quantization)
        
        if not gguf_path:
            raise ValueError("GGUF export failed")
        
        _update_job_status(job_id,
                          pipeline_stage="exporting",
                          progress=100,
                          gguf_path=str(gguf_path))
        
        _emit_progress(job_id, 100, f"GGUF export complete: {gguf_path}", "complete")
        
        logger.info(f"Export task completed for job {job_id}: {gguf_path}")
        return {"gguf_path": str(gguf_path)}
        
    except Exception as e:
        logger.error(f"Error in export_gguf_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


def _detect_model_architecture(model_name: str, gguf_filename: str) -> str:
    combined = f"{model_name} {gguf_filename}".lower()

    for pattern, template in MODEL_TEMPLATES.items():
        if pattern in combined:
            return template

    return 'llama-3.1-instruct.modelfile'


def _generate_modelfile_from_template(template_name: str, gguf_path: Path, mmproj_path: Path = None) -> str:
    template_path = MODELFILES_DIR / template_name

    if not template_path.exists():
        logger.warning(f"Template not found: {template_path}, using fallback")
        content = f"FROM {gguf_path}\n"
        content += "PARAMETER temperature 0.7\n"
        content += "PARAMETER top_p 0.9\n"
        return content

    with open(template_path, 'r') as f:
        content = f.read()

    content = content.replace('{{GGUF_PATH}}', str(gguf_path))

    if mmproj_path and '{{MMPROJ_PATH}}' in content:
        content = content.replace('{{MMPROJ_PATH}}', str(mmproj_path))
    elif '{{MMPROJ_PATH}}' in content:
        lines = content.split('\n')
        content = '\n'.join(line for line in lines if '{{MMPROJ_PATH}}' not in line)

    return content


@shared_task(bind=True, name='training.import_ollama')
def import_ollama_task(self, job_id: str, model_dir: str, model_name: str):
    logger.info(f"Starting import_ollama_task for job {job_id}")

    try:
        _update_job_status(job_id, status="running", pipeline_stage="importing", started_at=utcnow(), celery_task_id=self.request.id)
        _emit_progress(job_id, 0, "Starting Ollama import...", "start")

        model_dir_obj = Path(model_dir)

        gguf_files = list(model_dir_obj.glob("*.gguf"))
        if not gguf_files:
            raise FileNotFoundError(f"No GGUF file found in {model_dir}")

        main_gguf = None
        mmproj_gguf = None
        for gf in gguf_files:
            if 'mmproj' in gf.name.lower():
                mmproj_gguf = gf
            else:
                main_gguf = gf

        if not main_gguf:
            main_gguf = gguf_files[0]

        _emit_progress(job_id, 20, f"Creating Modelfile for {model_name}...", "processing")

        template_name = _detect_model_architecture(model_name, main_gguf.name)

        if mmproj_gguf and 'gemma' in template_name:
            template_name = 'gemma-3-vision.modelfile'

        logger.info(f"Using template: {template_name} for {model_name}")

        modelfile_content = _generate_modelfile_from_template(template_name, main_gguf, mmproj_gguf)

        modelfile_path = model_dir_obj / "Modelfile"
        with open(modelfile_path, 'w') as f:
            f.write(modelfile_content)

        _emit_progress(job_id, 50, f"Importing {model_name} to Ollama...", "processing")
        
        import subprocess
        result = subprocess.run(
            ["ollama", "create", model_name, "-f", str(modelfile_path)],
            capture_output=True,
            text=True,
            cwd=str(model_dir_obj)
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Ollama import failed: {result.stderr}")
        
        _update_job_status(job_id,
                          status="completed",
                          pipeline_stage="importing",
                          completed_at=utcnow(),
                          progress=100,
                          ollama_model_name=model_name)
        
        _emit_progress(job_id, 100, f"Ollama import complete: {model_name}", "complete")
        
        logger.info(f"Import task completed for job {job_id}: {model_name}")
        return {"ollama_model_name": model_name}
        
    except Exception as e:
        logger.error(f"Error in import_ollama_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


@shared_task(bind=True, name='training.full_pipeline',
             soft_time_limit=259200, time_limit=345600)
def full_training_pipeline_task(self, job_id: str, config: dict):
    logger.info(f"Starting full_training_pipeline_task for job {job_id}")
    
    try:
        _update_job_status(job_id, status="running", started_at=utcnow())
        _emit_progress(job_id, 0, "Starting full training pipeline...", "processing")

        _emit_progress(job_id, 1, "Freeing GPU memory (unloading Ollama models)...", "processing")
        try:
            from backend.services.gpu_resource_coordinator import unload_ollama_models, get_available_vram

            initial_vram = get_available_vram()
            logger.info(f"Initial VRAM: {initial_vram.get('available_mb', 'unknown')} MB available")

            unload_result = unload_ollama_models()
            if unload_result.get("success"):
                models_unloaded = unload_result.get("models_unloaded", [])
                vram_freed = unload_result.get("vram_freed_mb", 0)
                if models_unloaded:
                    logger.info(f"Unloaded Ollama models: {models_unloaded}, freed {vram_freed} MB")
                    _emit_progress(job_id, 2, f"Freed {vram_freed} MB GPU memory", "processing")
                else:
                    logger.info("No Ollama models were loaded")
            else:
                logger.warning(f"Failed to unload Ollama models: {unload_result.get('error')}")
        except Exception as e:
            logger.warning(f"Could not unload Ollama models (non-fatal): {e}")

        from flask import current_app
        with current_app.app_context():
            job = db.session.query(TrainingJob).filter(
                TrainingJob.job_id == job_id
            ).first()
            
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            
            job_config = json.loads(job.config_json) if job.config_json else {}
            job_config.update(config)
        
        parse_output_path = None
        if job_config.get("input_path"):
            _update_job_status(job_id, pipeline_stage="parsing")
            _emit_progress(job_id, 5, "Step 1/5: Parsing transcripts...", "processing")
            
            parse_result = parse_transcripts_task(job_id, 
                                                  job_config["input_path"], 
                                                  job_config.get("recursive", True))
            parse_output_path = parse_result.get("output_path")
            job_config["parse_output"] = parse_output_path
        
        filter_output_path = parse_output_path or job_config.get("dataset_path")
        if job_config.get("min_score") is not None and filter_output_path:
            _update_job_status(job_id, pipeline_stage="filtering")
            _emit_progress(job_id, PIPELINE_FILTER_PROGRESS[0], "Step 2/5: Filtering dataset...", "processing")
            
            filter_result = filter_dataset_task(job_id,
                                               filter_output_path,
                                               job_config.get("min_score", 0.5),
                                               in_pipeline=True)
            filter_output_path = filter_result.get("output_path")
            job_config["data_path"] = filter_output_path
            job_config["filter_report"] = filter_result
        elif filter_output_path:
            job_config["data_path"] = filter_output_path
        
        _update_job_status(job_id, pipeline_stage="training")
        _emit_progress(job_id, PIPELINE_FILTER_PROGRESS[1], "Step 3/5: Training model...", "processing")
        
        if not job_config.get("data_path"):
            raise ValueError("No dataset path available for training")
        
        # The job row read above belongs to a session that closed with its
        # app context, so setting attributes on it saves nothing; write
        # through a fresh one so finetune_model_task reads this config.
        _update_job_status(job_id, config_json=json.dumps(job_config))
        
        train_result = finetune_model_task(job_id, job_config)
        model_dir = train_result.get("model_dir")
        
        _update_job_status(job_id, pipeline_stage="exporting")
        _emit_progress(job_id, 80, "Step 4/5: Exporting to GGUF...", "processing")
        
        export_result = export_gguf_task(job_id,
                                        model_dir,
                                        job_config.get("quantization", "q4_k_m"))
        
        _update_job_status(job_id, pipeline_stage="importing")
        _emit_progress(job_id, 90, "Step 5/5: Importing to Ollama...", "processing")
        
        model_name = job.output_model_name or job_config.get("ollama_model_name")
        if not model_name:
            model_name = f"guaardvark-{job.base_model.replace('/', '-').replace(':', '-')}"
        
        import_result = import_ollama_task(job_id, model_dir, model_name)
        
        _update_job_status(job_id,
                          status="completed",
                          pipeline_stage="importing",
                          completed_at=utcnow(),
                          progress=100)
        
        heldout = _heldout_summary(train_result.get("eval") or {})
        _emit_progress(job_id, 100, f"Full pipeline complete! {heldout}", "complete")

        logger.info(f"Full pipeline completed for job {job_id}")
        return {
            "parse_output": parse_output_path,
            "filter_output": filter_output_path if job_config.get("min_score") is not None else None,
            "model_dir": model_dir,
            "gguf_path": export_result.get("gguf_path"),
            "ollama_model_name": import_result.get("ollama_model_name"),
            "eval": train_result.get("eval")
        }
        
    except Exception as e:
        logger.error(f"Error in full_training_pipeline_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise
