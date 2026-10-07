
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import or_

from backend.models import db, TrainingJob, DeviceProfile, TrainingDataset
from backend.utils.response_utils import success_response, error_response
from backend.utils.db_utils import ensure_db_session_cleanup
from pathlib import Path

TRAINING_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "training"

training_bp = Blueprint("training", __name__, url_prefix="/api/training")
logger = logging.getLogger(__name__)


def _not_started(job, what: str, exc: Exception):
    """Answer for a job whose task was not queued: the job is failed with the
    reason, so it does not sit at pending or running with nothing behind it."""
    from backend.celery_dispatch import TaskNotStarted

    job.status = "failed"
    job.error_message = f"Failed to start {what} task: {exc}"
    db.session.commit()
    if isinstance(exc, TaskNotStarted):
        return error_response(f"Failed to start {what}: {exc}", 503, exc.code)
    return error_response(f"Failed to start {what}: {exc}", 500)


def _put_back(job, before, what: str, exc: Exception):
    """Answer for a step started on an existing job (export, import, resume)
    whose task was not queued: nothing ran, so the job returns to the status
    and stage it had, and the step can be started again."""
    job.status, job.pipeline_stage = before
    db.session.commit()
    return error_response(f"Failed to start {what}: {exc}", 503, exc.code)


@training_bp.route("/jobs", methods=["GET"])
@ensure_db_session_cleanup
def list_jobs():
    try:
        status = request.args.get("status")
        dataset_id = request.args.get("dataset_id", type=int)
        
        query = db.session.query(TrainingJob)
        
        if status:
            query = query.filter(TrainingJob.status == status)
        if dataset_id:
            query = query.filter(TrainingJob.dataset_id == dataset_id)
        
        jobs = query.order_by(TrainingJob.created_at.desc()).all()
        
        return success_response([job.to_dict() for job in jobs])
    except Exception as e:
        logger.error(f"Error listing training jobs: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/images", methods=["GET"])
@ensure_db_session_cleanup
def list_image_folders():
    try:
        images_dir = TRAINING_DIR / "images"
        if not images_dir.exists():
            return success_response([])
            
        folders = []
        for item in images_dir.iterdir():
            if item.is_dir():
                count = len(list(item.glob("*.jpg"))) + len(list(item.glob("*.png"))) + len(list(item.glob("*.jpeg")))
                folders.append({
                    "name": item.name,
                    "path": str(item),
                    "image_count": count
                })
        
        return success_response(folders)
    except Exception as e:
        logger.error(f"Error listing image folders: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/datasets/inspect", methods=["GET"])
def inspect_dataset():
    """What a dataset path holds: files, rows, usable rows, formats, a few
    clipped sample rows and the first problems (file and line, never text).
    ?path= a .jsonl or .json file or a folder on this machine; ~ is followed."""
    from backend.services.training.scripts import dataset_formats
    try:
        return success_response(dataset_formats.inspect(request.args.get("path", "")))
    except Exception as e:
        logger.error(f"Error inspecting dataset path: {e}", exc_info=True)
        return error_response(f"Could not read the dataset: {type(e).__name__}", 500)


def training_datasets_dir() -> Path:
    """Where parsed transcripts are written and the dataset picker opens."""
    from backend.config import STORAGE_DIR
    return Path(STORAGE_DIR) / "training" / "datasets"


@training_bp.route("/datasets/locations", methods=["GET"])
def dataset_locations():
    """Starting folders for the dataset picker: Guaardvark's training datasets
    folder and the home folder, each with whether it exists, and the one the
    picker opens in."""
    datasets_dir = training_datasets_dir()
    home = Path(os.path.expanduser("~"))
    locations = [
        {"id": "datasets", "label": "Training datasets", "path": str(datasets_dir),
         "exists": datasets_dir.is_dir()},
        {"id": "home", "label": "Home", "path": str(home), "exists": home.is_dir()},
    ]
    default = next((loc["path"] for loc in locations if loc["exists"]), "~")
    return success_response({"locations": locations, "default": default})


@training_bp.route("/hardware", methods=["GET"])
@ensure_db_session_cleanup
def get_hardware_capabilities():
    try:
        from backend.services.hardware_service import HardwareService
        caps = HardwareService.get_system_capabilities()
        return success_response(caps)
    except Exception as e:
        logger.error(f"Error getting hardware capabilities: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/libraries", methods=["GET"])
def get_training_libraries():
    """Settings > Training libraries: the pinned libraries, what is installed,
    the hardware verdict and the install or remove in progress. ?plan=1 also
    hands out the one-use token the modal's Install and Remove buttons send."""
    try:
        from backend.services import training_libraries
        return success_response(training_libraries.status(with_plan_token=request.args.get("plan") == "1"))
    except Exception as e:
        logger.error(f"Error reading training libraries: {e}", exc_info=True)
        return error_response(str(e), 500)


def _libraries_in_use():
    """Why the training libraries may not change now, or None. A running job
    (training, export or import) has them loaded."""
    running = db.session.query(TrainingJob).filter(TrainingJob.status == "running").count()
    if running:
        return (f"{running} training job(s) running. Installing or removing the training "
                f"libraries now could stop them; wait for them to finish or cancel them.")
    return None


@training_bp.route("/libraries/install", methods=["POST"])
@ensure_db_session_cleanup
def install_training_libraries():
    """Install the pinned training libraries into the backend's Python.

    Only the modal's Install click sends what this needs:
    {"confirm": "install", "plan_token": <from GET /libraries?plan=1>,
     "anyway": true when the hardware verdict says not practical}."""
    from backend.services import training_libraries
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "install":
        return error_response(training_libraries.NEEDS_CLICK, 403, "NEEDS_CLICK")
    try:
        in_use = _libraries_in_use()
        if in_use:
            return error_response(in_use, 409, "TRAINING_RUNNING")
        result = training_libraries.start_install(data.get("plan_token"), anyway=data.get("anyway") is True)
        logger.info("Training libraries install started (anyway=%s)", data.get("anyway") is True)
        return success_response(result, "Install started", status_code=202)
    except training_libraries.Refused as e:
        return error_response(str(e), e.status, e.code)
    except Exception as e:
        logger.error(f"Error starting the training libraries install: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/libraries/remove", methods=["POST"])
@ensure_db_session_cleanup
def remove_training_libraries():
    """Uninstall the training libraries and what their install added.
    Body: {"confirm": "remove", "plan_token": <from GET /libraries?plan=1>}."""
    from backend.services import training_libraries
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "remove":
        return error_response(training_libraries.NEEDS_CLICK, 403, "NEEDS_CLICK")
    try:
        in_use = _libraries_in_use()
        if in_use:
            return error_response(in_use, 409, "TRAINING_RUNNING")
        result = training_libraries.start_remove(data.get("plan_token"))
        logger.info("Training libraries remove started")
        return success_response(result, "Remove started", status_code=202)
    except training_libraries.Refused as e:
        return error_response(str(e), e.status, e.code)
    except Exception as e:
        logger.error(f"Error starting the training libraries remove: {e}", exc_info=True)
        return error_response(str(e), 500)


def _libraries_missing_response():
    """The refusal for a job that would fail for want of the training
    libraries, naming where to install them; None when they are there."""
    from backend.services import training_libraries
    reason = training_libraries.unavailable_reason()
    if reason:
        return error_response(reason, 409, "TRAINING_LIBRARIES_MISSING")
    return None


@training_bp.route("/jobs", methods=["POST"])
@ensure_db_session_cleanup
def create_job():
    try:
        data = request.get_json()

        if not data.get("name"):
            return error_response("Job name is required", 400)
        if not data.get("base_model"):
            return error_response("Base model is required", 400)
        if not data.get("dataset_id"):
            return error_response("Dataset ID is required", 400)

        missing_libraries = _libraries_missing_response()
        if missing_libraries:
            return missing_libraries

        # The trainer reads the dataset's file when the config names none, so a
        # dataset it cannot read is refused here rather than failing the run.
        dataset = db.session.get(TrainingDataset, data["dataset_id"])
        if not dataset:
            return error_response("Dataset not found", 400)
        job_config = data.get("config") or {}
        if not (job_config.get("data_path") or job_config.get("dataset_path")):
            from backend.tasks.training_tasks import dataset_training_files
            _, reason = dataset_training_files(dataset.path)
            if reason:
                return error_response(f"Dataset '{dataset.name}' cannot be trained on: {reason}", 400)

        device_profile_id = data.get("device_profile_id")
        device_profile = None
        if device_profile_id:
            device_profile = db.session.get(DeviceProfile, device_profile_id)
            if not device_profile:
                return error_response("Device profile not found", 400)
            if not device_profile.is_active:
                return error_response("Device profile is not active", 400)
            
            config = data.get("config", {})
            batch_size = config.get("batch_size", device_profile.max_batch_size)
            seq_length = config.get("seq_length", device_profile.max_seq_length)
            
            if batch_size > device_profile.max_batch_size:
                return error_response(
                    f"Batch size {batch_size} exceeds device profile maximum {device_profile.max_batch_size}",
                    400
                )
            
            if seq_length > device_profile.max_seq_length:
                return error_response(
                    f"Sequence length {seq_length} exceeds device profile maximum {device_profile.max_seq_length}",
                    400
                )
            
            if device_profile.device_type == "gpu" and device_profile.gpu_vram_mb:
                estimated_vram = batch_size * 2048
                if estimated_vram > device_profile.gpu_vram_mb * 0.9:
                    return error_response(
                        f"Estimated VRAM usage ({estimated_vram}MB) exceeds available VRAM ({device_profile.gpu_vram_mb}MB)",
                        400
                    )
        
        job_id = str(uuid.uuid4())
        
        queue = "training"
        if device_profile:
            if device_profile.device_type == "gpu":
                queue = "training_gpu"
            else:
                queue = "training"
        
        job = TrainingJob(
            job_id=job_id,
            name=data["name"],
            base_model=data["base_model"],
            output_model_name=data.get("output_model_name"),
            dataset_id=data["dataset_id"],
            config_json=json.dumps(data.get("config", {})),
            device_profile_id=device_profile_id,
            status="pending",
            pipeline_stage="pending"
        )
        
        db.session.add(job)
        db.session.commit()
        
        if data.get("start_immediately", False):
            from backend.celery_dispatch import TaskNotStarted
            from backend.tasks.training_tasks import finetune_model_task
            try:
                task = finetune_model_task.apply_async(
                    args=[job_id, json.loads(job.config_json)],
                    queue=queue
                )
            except TaskNotStarted as e:
                return _not_started(job, "training", e)
            job.celery_task_id = task.id
            job.status = "running"
            db.session.commit()
        
        logger.info(f"Created training job: {job_id} - {job.name} (queue: {queue})")
        return success_response(job.to_dict(), status_code=201)
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error creating training job: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error creating training job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>", methods=["GET"])
@ensure_db_session_cleanup
def get_job(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)
        
        return success_response(job.to_dict())
    except Exception as e:
        logger.error(f"Error getting training job {job_id}: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>", methods=["DELETE"])
@ensure_db_session_cleanup
def delete_job(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)
        
        if job.status == "running":
            try:
                from celery import current_app as celery_app
                if job.celery_task_id:
                    celery_app.control.revoke(job.celery_task_id, terminate=True)
            except Exception as e:
                logger.warning(f"Could not cancel Celery task: {e}")
        
        db.session.delete(job)
        db.session.commit()
        
        logger.info(f"Deleted training job: {job_id}")
        return success_response({"message": "Job deleted"})
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error deleting training job: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error deleting training job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/cancel", methods=["POST"])
@ensure_db_session_cleanup
def cancel_job(job_id):
    import os
    import signal
    import time

    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if job.status not in ["pending", "running"]:
            return error_response(f"Job cannot be cancelled (status: {job.status})", 400)

        pid_terminated = False

        if job.pid:
            try:
                logger.info(f"Sending SIGTERM to PID {job.pid}")
                os.kill(job.pid, signal.SIGTERM)
                pid_terminated = True

                time.sleep(2)

                try:
                    os.kill(job.pid, 0)
                    logger.info(f"Process {job.pid} still running, sending SIGKILL")
                    time.sleep(3)
                    try:
                        os.kill(job.pid, 0)
                        os.kill(job.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                except ProcessLookupError:
                    logger.info(f"Process {job.pid} terminated gracefully")

            except ProcessLookupError:
                logger.info(f"Process {job.pid} already terminated")
            except PermissionError:
                logger.warning(f"Permission denied to terminate PID {job.pid}")
            except Exception as e:
                logger.warning(f"Error terminating process: {e}")

        if job.celery_task_id:
            try:
                from celery import current_app as celery_app
                celery_app.control.revoke(job.celery_task_id, terminate=True)
                logger.info(f"Revoked Celery task: {job.celery_task_id}")
            except Exception as e:
                logger.warning(f"Could not revoke Celery task: {e}")

        job.status = "cancelled"
        job.error_message = "Cancelled by user"
        job.pid = None
        db.session.commit()

        logger.info(f"Cancelled training job: {job_id} (pid_terminated={pid_terminated})")
        return success_response({
            **job.to_dict(),
            "pid_terminated": pid_terminated
        })
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error cancelling training job: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error cancelling training job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/resume", methods=["POST"])
@ensure_db_session_cleanup
def resume_job(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if job.status not in ["failed", "cancelled"]:
            return error_response(f"Job cannot be resumed (status: {job.status})", 400)

        if not job.is_resumable:
            return error_response("Job is not resumable (no checkpoint available)", 400)

        missing_libraries = _libraries_missing_response()
        if missing_libraries:
            return missing_libraries

        before = (job.status, job.pipeline_stage)
        job.status = "pending"
        job.error_message = None
        job.progress = 0
        job.pid = None
        db.session.commit()

        job_config = {}
        if job.config_json:
            import json
            job_config = json.loads(job.config_json)

        from backend.celery_dispatch import TaskNotStarted
        try:
            from backend.tasks.training_tasks import finetune_model_task

            try:
                task = finetune_model_task.apply_async(
                    args=[job.job_id, job_config],
                    kwargs={"resume": True},
                    queue="training_gpu"
                )
            except TaskNotStarted as e:
                # Back to failed or cancelled, so Resume stays available.
                job.error_message = str(e)
                return _put_back(job, before, "resume", e)

            job.celery_task_id = task.id
            job.status = "running"
            job.pipeline_stage = "training"
            db.session.commit()

            logger.info(f"Resumed training job: {job_id} with task {task.id}")
            return success_response({
                **job.to_dict(),
                "celery_task_id": task.id,
                "resumed_from_checkpoint": job.checkpoint_path
            })

        except ImportError as e:
            logger.error(f"Could not import training task: {e}")
            return error_response("Training tasks not available", 500)

    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error resuming training job: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error resuming training job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/device-profiles", methods=["GET"])
@ensure_db_session_cleanup
def list_device_profiles():
    try:
        profiles = db.session.query(DeviceProfile).filter(
            DeviceProfile.is_active == True
        ).order_by(DeviceProfile.is_default.desc(), DeviceProfile.name).all()
        
        return success_response([profile.to_dict() for profile in profiles])
    except Exception as e:
        logger.error(f"Error listing device profiles: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/device-profiles", methods=["POST"])
@ensure_db_session_cleanup
def create_device_profile():
    try:
        data = request.get_json()
        
        if not data.get("name"):
            return error_response("Profile name is required", 400)
        
        existing = db.session.query(DeviceProfile).filter(
            DeviceProfile.name == data["name"]
        ).first()
        if existing:
            return error_response("Profile name already exists", 400)
        
        if data.get("is_default"):
            db.session.query(DeviceProfile).update({"is_default": False})
        
        profile = DeviceProfile(
            name=data["name"],
            device_type=data.get("device_type", "gpu"),
            gpu_vram_mb=data.get("gpu_vram_mb"),
            system_ram_mb=data.get("system_ram_mb"),
            max_batch_size=data.get("max_batch_size", 2),
            max_seq_length=data.get("max_seq_length", 2048),
            supports_4bit=data.get("supports_4bit", True),
            requires_cpu_offload=data.get("requires_cpu_offload", False),
            is_default=data.get("is_default", False),
            is_active=data.get("is_active", True)
        )
        
        db.session.add(profile)
        db.session.commit()
        
        logger.info(f"Created device profile: {profile.name}")
        return success_response(profile.to_dict(), status_code=201)
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error creating device profile: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error creating device profile: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/device-profiles/<int:profile_id>", methods=["PUT"])
@ensure_db_session_cleanup
def update_device_profile(profile_id):
    try:
        profile = db.session.get(DeviceProfile, profile_id)
        if not profile:
            return error_response("Profile not found", 404)
        
        data = request.get_json()
        
        if "name" in data:
            if data["name"] != profile.name:
                existing = db.session.query(DeviceProfile).filter(
                    DeviceProfile.name == data["name"]
                ).first()
                if existing:
                    return error_response("Profile name already exists", 400)
            profile.name = data["name"]
        
        if "device_type" in data:
            profile.device_type = data["device_type"]
        if "gpu_vram_mb" in data:
            profile.gpu_vram_mb = data["gpu_vram_mb"]
        if "system_ram_mb" in data:
            profile.system_ram_mb = data["system_ram_mb"]
        if "max_batch_size" in data:
            profile.max_batch_size = data["max_batch_size"]
        if "max_seq_length" in data:
            profile.max_seq_length = data["max_seq_length"]
        if "supports_4bit" in data:
            profile.supports_4bit = data["supports_4bit"]
        if "requires_cpu_offload" in data:
            profile.requires_cpu_offload = data["requires_cpu_offload"]
        if "is_active" in data:
            profile.is_active = data["is_active"]
        
        if "is_default" in data and data["is_default"]:
            db.session.query(DeviceProfile).filter(
                DeviceProfile.id != profile_id
            ).update({"is_default": False})
            profile.is_default = True
        elif "is_default" in data:
            profile.is_default = False
        
        db.session.commit()
        
        logger.info(f"Updated device profile: {profile_id}")
        return success_response(profile.to_dict())
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error updating device profile: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error updating device profile: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/device-profiles/<int:profile_id>", methods=["DELETE"])
@ensure_db_session_cleanup
def delete_device_profile(profile_id):
    try:
        profile = db.session.get(DeviceProfile, profile_id)
        if not profile:
            return error_response("Profile not found", 404)
        
        jobs_using = db.session.query(TrainingJob).filter(
            TrainingJob.device_profile_id == profile_id
        ).count()
        if jobs_using > 0:
            return error_response(f"Cannot delete profile: {jobs_using} job(s) are using it", 400)
        
        db.session.delete(profile)
        db.session.commit()
        
        logger.info(f"Deleted device profile: {profile_id}")
        return success_response({"message": "Profile deleted"})
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error deleting device profile: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error deleting device profile: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/base-models", methods=["GET"])
@ensure_db_session_cleanup
def list_base_models():
    try:
        from backend.api.model_api import get_available_ollama_models
        
        models = get_available_ollama_models()
        
        base_models = [m for m in models if ":" in m.get("name", "")]
        
        return success_response(base_models)
    except Exception as e:
        logger.error(f"Error listing base models: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/base-models/status", methods=["GET"])
@ensure_db_session_cleanup
def base_model_status():
    """Whether the chosen base model is already on this machine.

    Fine-tuning loads the base model through Hugging Face; a cache miss is a
    multi-GB download the form must announce before the job is created."""
    name = (request.args.get("name") or "").strip()
    if not name:
        return error_response("name is required", 400)
    try:
        from backend.services.local_weights import download_status
        return success_response(download_status(name))
    except Exception as e:
        logger.error(f"Error checking base model status: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/pipeline/parse", methods=["POST"])
@ensure_db_session_cleanup
def start_parse_job():
    try:
        data = request.get_json(silent=True) or {}

        input_path = data.get("input_path")
        if not isinstance(input_path, str) or not input_path.strip():
            return error_response("input_path is required", 400)
        name = data.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            name = f"Parse: {os.path.basename(input_path.rstrip('/')) or input_path}"

        job_id = str(uuid.uuid4())
        job = TrainingJob(
            job_id=job_id,
            name=name,
            pipeline_stage="parsing",
            status="pending",
            config_json=json.dumps({
                "input_path": data["input_path"],
                "recursive": data.get("recursive", True)
            })
        )
        
        db.session.add(job)
        db.session.commit()
        
        try:
            from backend.tasks.training_tasks import parse_transcripts_task
            task = parse_transcripts_task.apply_async(
                args=[job_id, data["input_path"], data.get("recursive", True)],
                queue="training"
            )
            job.celery_task_id = task.id
            job.status = "running"
            db.session.commit()
            logger.info(f"Started parse task for job {job_id}: {task.id}")
        except Exception as e:
            logger.error(f"Failed to start parse task: {e}", exc_info=True)
            return _not_started(job, "parse", e)

        logger.info(f"Created parse job: {job_id}")
        return success_response(job.to_dict(), status_code=201)
    except Exception as e:
        logger.error(f"Error starting parse job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/pipeline/filter", methods=["POST"])
@ensure_db_session_cleanup
def start_filter_job():
    try:
        data = request.get_json()

        if not data.get("input_path"):
            return error_response("input_path is required", 400)

        # Compared against each pair's score, so it has to be a number.
        min_score = data.get("min_score", 0.5)
        if isinstance(min_score, bool) or not isinstance(min_score, (int, float)):
            return error_response("min_score must be a number", 400)

        job_id = str(uuid.uuid4())
        job = TrainingJob(
            job_id=job_id,
            name=data.get("name", f"Filter: {data['input_path']}"),
            pipeline_stage="filtering",
            status="pending",
            config_json=json.dumps({
                "input_path": data["input_path"],
                "min_score": min_score
            })
        )

        db.session.add(job)
        db.session.commit()

        try:
            from backend.tasks.training_tasks import filter_dataset_task
            task = filter_dataset_task.apply_async(
                args=[job_id, data["input_path"], min_score],
                queue="training"
            )
            job.celery_task_id = task.id
            job.status = "running"
            db.session.commit()
            logger.info(f"Started filter task for job {job_id}: {task.id}")
        except Exception as e:
            logger.error(f"Failed to start filter task: {e}", exc_info=True)
            return _not_started(job, "filter", e)

        logger.info(f"Created filter job: {job_id}")
        return success_response(job.to_dict(), status_code=201)
    except Exception as e:
        logger.error(f"Error starting filter job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/export-anyway", methods=["POST"])
@ensure_db_session_cleanup
def export_anyway(job_id):
    """Release a run the export gate held, on a person's explicit choice.

    Body (optional): {"by": who chose, "via": where from}. The override is
    recorded in the job's config as ``export_override`` beside the gate's own
    verdict, and the job becomes "completed" so the export routes accept it.
    Nothing is exported until one of them is called."""
    try:
        from backend.tasks.training_tasks import WORSE_THAN_BASE
        from backend.utils.clock import utcnow

        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)
        if job.status != WORSE_THAN_BASE:
            return error_response(
                f"Only a run held as '{WORSE_THAN_BASE}' can be exported anyway (status: {job.status})",
                400
            )
        if not job.lora_path or not Path(job.lora_path).exists():
            return error_response(f"LoRA adapter path not found: {job.lora_path}", 400)

        data = request.get_json(silent=True) or {}
        config = json.loads(job.config_json) if job.config_json else {}
        config["export_override"] = {
            "by": str(data.get("by") or "person")[:100],
            "via": str(data.get("via") or "api")[:50],
            "at": utcnow().isoformat(timespec="seconds") + "Z",
            "from_address": request.remote_addr,
            "held_status": job.status,
            "gate_note": job.error_message,
        }
        job.config_json = json.dumps(config)
        job.status = "completed"
        job.error_message = None
        db.session.commit()

        logger.info(f"Export gate overridden for training job {job_id} by "
                    f"{config['export_override']['by']} via {config['export_override']['via']}")
        return success_response(job.to_dict())
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error overriding the export gate for job {job_id}: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error overriding the export gate for job {job_id}: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/export", methods=["POST"])
@ensure_db_session_cleanup
def export_job_to_gguf(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if job.status != "completed" or job.pipeline_stage not in ["training", "completed"]:
            return error_response(
                f"Job must be completed before export (status: {job.status}, stage: {job.pipeline_stage})",
                400
            )

        if not job.lora_path:
            return error_response("No LoRA adapter found for this job", 400)

        lora_path = Path(job.lora_path)
        if not lora_path.exists():
            return error_response(f"LoRA adapter path not found: {job.lora_path}", 400)

        data = request.get_json() or {}
        quantization = data.get("quantization", "q4_k_m")

        before = (job.status, job.pipeline_stage)
        job.pipeline_stage = "exporting"
        job.status = "running"
        job.quantization_level = quantization
        db.session.commit()

        from backend.celery_dispatch import TaskNotStarted
        try:
            from backend.tasks.training_tasks import export_gguf_task
            # export_gguf_task takes the training output folder, which holds lora/.
            task = export_gguf_task.apply_async(
                args=[job.job_id, str(lora_path.parent), quantization],
                queue="training"
            )
            job.celery_task_id = task.id
            db.session.commit()
            logger.info(f"Started GGUF export task for job {job_id}: {task.id}")
        except TaskNotStarted as e:
            return _put_back(job, before, "export", e)
        except Exception as e:
            job.status = "failed"
            job.error_message = f"Failed to start export task: {str(e)}"
            db.session.commit()
            logger.error(f"Failed to start export task: {e}", exc_info=True)
            return error_response(f"Failed to start export: {str(e)}", 500)

        return success_response({
            "message": f"Export started for job {job_id}",
            "job": job.to_dict(),
            "quantization": quantization
        }, status_code=202)
    except Exception as e:
        logger.error(f"Error exporting job {job_id}: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/import-ollama", methods=["POST"])
@ensure_db_session_cleanup
def import_job_to_ollama(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if not job.gguf_path:
            return error_response("No GGUF file found. Export to GGUF first.", 400)

        gguf_path = Path(job.gguf_path)
        if not gguf_path.exists():
            return error_response(f"GGUF file not found: {job.gguf_path}", 400)

        data = request.get_json() or {}
        model_name = data.get("model_name") or job.output_model_name or f"guaardvark-{job.name.lower().replace(' ', '-')}"

        before = (job.status, job.pipeline_stage)
        job.pipeline_stage = "importing"
        job.status = "running"
        db.session.commit()

        from backend.celery_dispatch import TaskNotStarted
        try:
            from backend.tasks.training_tasks import import_ollama_task
            # import_ollama_task looks for the .gguf files in a folder.
            task = import_ollama_task.apply_async(
                args=[job.job_id, str(gguf_path.parent), model_name],
                queue="training"
            )
            job.celery_task_id = task.id
            db.session.commit()
            logger.info(f"Started Ollama import task for job {job_id}: {task.id}")
        except TaskNotStarted as e:
            return _put_back(job, before, "import", e)
        except Exception as e:
            job.status = "failed"
            job.error_message = f"Failed to start import task: {str(e)}"
            db.session.commit()
            logger.error(f"Failed to start import task: {e}", exc_info=True)
            return error_response(f"Failed to start import: {str(e)}", 500)

        return success_response({
            "message": f"Ollama import started for job {job_id}",
            "job": job.to_dict(),
            "model_name": model_name
        }, status_code=202)
    except Exception as e:
        logger.error(f"Error importing job {job_id} to Ollama: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/export-to-ollama", methods=["POST"])
@ensure_db_session_cleanup
def export_to_ollama(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if job.status != "completed" or job.pipeline_stage not in ["training", "completed", "exporting"]:
            if not job.gguf_path:
                return error_response(
                    f"Job must be completed before export (status: {job.status}, stage: {job.pipeline_stage})",
                    400
                )

        data = request.get_json() or {}
        quantization = data.get("quantization", "q4_k_m")
        model_name = data.get("model_name") or job.output_model_name or f"guaardvark-{job.name.lower().replace(' ', '-')}"

        if job.gguf_path and Path(job.gguf_path).exists():
            logger.info(f"GGUF already exists at {job.gguf_path}, skipping to import")
            before = (job.status, job.pipeline_stage)
            job.pipeline_stage = "importing"
            job.status = "running"
            db.session.commit()

            from backend.celery_dispatch import TaskNotStarted
            from backend.tasks.training_tasks import import_ollama_task
            try:
                task = import_ollama_task.apply_async(
                    args=[job.job_id, str(Path(job.gguf_path).parent), model_name],
                    queue="training"
                )
            except TaskNotStarted as e:
                return _put_back(job, before, "import", e)
            job.celery_task_id = task.id
            db.session.commit()

            return success_response({
                "message": f"Ollama import started (GGUF already exists)",
                "job": job.to_dict(),
                "model_name": model_name,
                "skipped_export": True
            }, status_code=202)

        if not job.lora_path:
            return error_response("No LoRA adapter found for this job", 400)

        lora_path = Path(job.lora_path)
        if not lora_path.exists():
            return error_response(f"LoRA adapter path not found: {job.lora_path}", 400)

        before = (job.status, job.pipeline_stage)
        job.pipeline_stage = "exporting"
        job.status = "running"
        job.output_model_name = model_name
        job.quantization_level = quantization
        db.session.commit()

        from backend.celery_dispatch import TaskNotStarted
        try:
            from backend.tasks.training_tasks import export_gguf_task, import_ollama_task
            from celery import chain

            # Both steps take the training output folder (export reads lora/
            # in it and writes the .gguf beside it). The import signature is
            # immutable so the chain does not prepend the export's result.
            model_dir = str(lora_path.parent)
            workflow = chain(
                export_gguf_task.s(job.job_id, model_dir, quantization),
                import_ollama_task.si(job.job_id, model_dir, model_name)
            )
            result = workflow.apply_async(queue="training")
            job.celery_task_id = result.id
            db.session.commit()
            logger.info(f"Started export-to-ollama workflow for job {job_id}: {result.id}")
        except TaskNotStarted as e:
            return _put_back(job, before, "export to Ollama", e)
        except Exception as e:
            job.status = "failed"
            job.error_message = f"Failed to start export-to-ollama workflow: {str(e)}"
            db.session.commit()
            logger.error(f"Failed to start export-to-ollama workflow: {e}", exc_info=True)
            return error_response(f"Failed to start workflow: {str(e)}", 500)

        return success_response({
            "message": f"Export to Ollama started for job {job_id}",
            "job": job.to_dict(),
            "model_name": model_name,
            "quantization": quantization
        }, status_code=202)
    except Exception as e:
        logger.error(f"Error in export-to-ollama for job {job_id}: {e}", exc_info=True)
        return error_response(str(e), 500)
