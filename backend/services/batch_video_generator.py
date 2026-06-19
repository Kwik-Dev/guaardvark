"""
Batch Video Generator Service.

Provides batch orchestration for video generation tasks using the
OfflineVideoGenerator. Supports text-to-video and image-to-video
workflows, with frame-by-frame generation for memory-constrained
environments.
"""

import json
import logging
import queue
import subprocess
import threading
import uuid
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from backend.services.video_generation_router import (
    VideoGenerationRequest,
    VideoGenerationResult,
    get_video_generator,
)
from backend.services.gpu_resource_coordinator import get_gpu_coordinator

try:
    from backend.config import UPLOAD_DIR
except ImportError:
    UPLOAD_DIR = "/tmp/guaardvark_uploads"

logger = logging.getLogger(__name__)


def _derive_display_name(text: str, max_len: int = 40) -> str:
    """Trim a prompt down to something the Media Library card can show."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"

# Dedicated video generation log file
_video_log_handler = None
def _get_video_logger():
    global _video_log_handler
    if _video_log_handler is None:
        try:
            from backend.config import LOG_DIR
            log_path = Path(LOG_DIR) / "video_generation.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            _video_log_handler = logging.FileHandler(str(log_path))
            _video_log_handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s"
            ))
            # Attach to this module (high-level batch orchestration)
            logger.addHandler(_video_log_handler)
            logger.setLevel(logging.INFO)

            # Also attach to the detailed generator so "Using Wan/Cog...", "Added TeaCache",
            # "Prompt enhanced", VAE/RIFE/ post-proc logs, etc. end up in the dedicated file
            # instead of only backend.log or being lost to stdout/Comfy capture.
            try:
                comfy_log = logging.getLogger("backend.services.comfyui_video_generator")
                comfy_log.addHandler(_video_log_handler)
                comfy_log.setLevel(logging.INFO)
            except Exception:
                pass
        except Exception:
            pass
    return logger


@dataclass
class BatchVideoItem:
    id: str
    prompt: Optional[str] = None
    image_path: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


@dataclass
class BatchVideoRequest:
    batch_id: str
    items: List[BatchVideoItem]
    output_dir: str
    model: str = "cogvideox-5b"
    duration_frames: int = 25
    fps: int = 7
    width: int = 512
    height: int = 512
    motion_strength: float = 1.0
    num_inference_steps: int = 25
    guidance_scale: float = 7.5
    seed: Optional[int] = None
    generate_frames_only: bool = False
    frames_per_batch: int = 1
    combine_frames: bool = False
    interpolation_multiplier: int = 2
    prompt_style: str = "cinematic"
    enhance_prompt: bool = True
    fidelity_mode: bool = False  # "Exact text / preserve fidelity" — light enhancement only
    negative_prompt: str = ""
    freeu: bool = False
    face_restore: bool = False
    lora_name: Optional[str] = None
    lora_strength: float = 1.0
    # Quality pipeline (v2.6.2 — ported from the music-video generator). All opt-in;
    # defaults preserve the existing fast single-pass text-to-video behavior.
    director_mode: bool = False           # rewrite each prompt via the Video Director (cinematic)
    cinematic_keyframe: bool = False      # FLUX still -> Wan2.2 I2V per clip (forces serial render)
    director_guidance: Optional[str] = None  # optional free-text steer for the director
    storyboard_concept: Optional[str] = None  # expand ONE concept into len(items) connected shots
    metadata: Dict = field(default_factory=dict)


@dataclass
class BatchVideoResult:
    item_id: str
    success: bool
    video_path: Optional[str] = None
    frame_paths: List[str] = field(default_factory=list)
    thumbnail_path: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


@dataclass
class BatchVideoStatus:
    batch_id: str
    status: str  # "pending", "running", "completed", "error", "cancelled"
    total_videos: int
    completed_videos: int = 0
    failed_videos: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    results: List[BatchVideoResult] = field(default_factory=list)
    error: Optional[str] = None
    output_dir: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    retry_data: Optional[Dict] = None  # persisted original prompts/image_paths + params for one-click retry on failed batches


class BatchVideoGenerator:
    """Service for generating multiple videos in batch with basic progress tracking."""

    def __init__(self):
        # Videos land directly in data/uploads/Videos/ so DocumentsPage sees them
        self.base_output_dir = Path(UPLOAD_DIR) / "Videos"
        self.base_output_dir.mkdir(parents=True, exist_ok=True)

        self.active_batches: Dict[str, BatchVideoStatus] = {}
        self.batch_lock = threading.Lock()

        # Queue plumbing — one batch runs at a time, the rest stack up.
        self.batch_queue: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_events: Dict[str, threading.Event] = {}
        self.queue_order: List[str] = []  # batch_ids in submission order, oldest first
        self._running_batch_id: Optional[str] = None

        self.video_generator = get_video_generator()
        self.service_available = getattr(self.video_generator, 'service_available', True) and video_generator_available if 'video_generator_available' in dir() else self.video_generator.service_available
        # Edge graceful: on no-GPU, batch video (which uses offline or Comfy) will inherit unavailable with reason from underlying generator.
        _get_video_logger()  # Initialize dedicated log file

        # Single daemon worker drains the queue. GPU coordinator stays as
        # defense-in-depth inside _run_batch.
        self._worker_thread = threading.Thread(
            target=self._queue_worker, daemon=True, name="batch-video-worker"
        )
        self._worker_thread.start()

        logger.info(f"BatchVideoGenerator initialized - Service available: {self.service_available}")

    def _queue_worker(self) -> None:
        """Pulls one batch off the queue at a time. Bouncer at the GPU door."""
        while True:
            try:
                batch_request, status = self.batch_queue.get()
            except Exception as e:
                logger.error(f"Queue worker get() failed: {e}")
                continue

            try:
                cancel_event = self.cancel_events.get(batch_request.batch_id)
                if cancel_event and cancel_event.is_set():
                    status.status = "cancelled"
                    status.end_time = datetime.now()
                    if not status.error:
                        status.error = "Cancelled before start"
                    self._save_metadata(status)
                    logger.info(f"Skipped cancelled batch {batch_request.batch_id}")
                    continue

                self._running_batch_id = batch_request.batch_id
                self._run_batch(batch_request, status)
            except Exception as e:
                logger.error(f"Queue worker crashed on batch {batch_request.batch_id}: {e}")
                status.status = "error"
                status.error = str(e)
                status.end_time = datetime.now()
                self._save_metadata(status)
            finally:
                self._running_batch_id = None
                with self.batch_lock:
                    if batch_request.batch_id in self.queue_order:
                        # Keep completed batches in queue_order for /queue snapshot
                        # so the UI can show recent history. _cleanup_stale_batches
                        # already trims old data.
                        pass

    @staticmethod
    def _extract_thumbnail(video_path: Path, thumbnail_path: Path) -> bool:
        """Extract the first frame from a video as a JPEG thumbnail using ffmpeg."""
        try:
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "ffmpeg", "-i", str(video_path),
                    "-vf", "select=eq(n\\,0)",
                    "-frames:v", "1",
                    "-q:v", "2",
                    "-y", str(thumbnail_path),
                ],
                capture_output=True,
                timeout=30,
            )
            if thumbnail_path.exists() and thumbnail_path.stat().st_size > 0:
                logger.info(f"Extracted thumbnail: {thumbnail_path}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Failed to extract thumbnail: {e}")
            return False

    def _get_batch_dir(self, batch_id: str) -> Path:
        return self.base_output_dir / batch_id

    def _save_metadata(self, batch_status: BatchVideoStatus) -> None:
        try:
            batch_dir = Path(batch_status.output_dir or self._get_batch_dir(batch_status.batch_id))
            batch_dir.mkdir(parents=True, exist_ok=True)
            metadata_file = batch_dir / "batch_metadata.json"
            serializable = asdict(batch_status)
            # Convert datetime to isoformat
            if batch_status.start_time:
                serializable["start_time"] = batch_status.start_time.isoformat()
            if batch_status.end_time:
                serializable["end_time"] = batch_status.end_time.isoformat()
            with open(metadata_file, "w") as f:
                json.dump(serializable, f, indent=2)

            try:
                from backend.socketio_instance import socketio
                # The event name matches frontend expectations and uses the batch_id as the room
                socketio.emit("video_batch:update", serializable, room=batch_status.batch_id)
            except Exception as e:
                logger.debug(f"Failed to emit WebSocket update for batch {batch_status.batch_id}: {e}")

            self._emit_canonical_job_event(batch_status)

        except Exception as e:  # pragma: no cover - best effort
            logger.warning(f"Failed to save batch metadata: {e}")

    def _apply_director(self, batch_request: BatchVideoRequest) -> None:
        """Rewrite each text item's prompt via the Video Director (cinematic enrichment).

        Mutates ``batch_request.items[*].prompt`` in place and disables the lighter
        downstream enhancer (the director already produced a full shot prompt, so the
        generic boilerplate would just dilute it). Never raises — on any failure the
        original prompts stand and generation proceeds unchanged."""
        try:
            from backend.services.video_director import direct_prompts
            text_items = [it for it in batch_request.items if (it.prompt or "").strip()]
            if not text_items:
                return
            style = (batch_request.metadata or {}).get("look_and_feel") or batch_request.prompt_style
            directed = direct_prompts(
                [it.prompt for it in text_items],
                style=style,
                extra_guidance=getattr(batch_request, "director_guidance", None),
            )
            changed = 0
            for it, new_prompt in zip(text_items, directed):
                if new_prompt and new_prompt.strip() and new_prompt.strip() != (it.prompt or "").strip():
                    it.prompt = new_prompt.strip()
                    changed += 1
            # Director output is already a complete cinematic prompt; don't double-enhance.
            batch_request.enhance_prompt = False
            logger.info(
                f"Video Director enhanced {changed}/{len(text_items)} prompt(s) for batch "
                f"{batch_request.batch_id}"
            )
        except Exception as e:  # noqa: BLE001 — director must never fail a render
            logger.warning(f"Director pass skipped for batch {batch_request.batch_id} (non-fatal): {e}")

    def _apply_storyboard(self, batch_request: BatchVideoRequest) -> None:
        """Expand a single concept into one connected shot per item.

        Mutates each item's prompt in place with the Storyboard agent's output and turns
        off the lighter downstream enhancer (the shots are already full cinematic prompts).
        Never raises — on failure the placeholder prompts (the raw concept) stand."""
        try:
            from backend.services.video_director import storyboard_from_concept
            concept = (batch_request.storyboard_concept or "").strip()
            n = len(batch_request.items)
            if not concept or n == 0:
                return
            style = (batch_request.metadata or {}).get("look_and_feel") or batch_request.prompt_style
            shots = storyboard_from_concept(
                concept, n, style=style,
                extra_guidance=getattr(batch_request, "director_guidance", None),
            )
            for it, shot in zip(batch_request.items, shots):
                if shot and shot.strip():
                    it.prompt = shot.strip()
            batch_request.enhance_prompt = False
            logger.info(
                f"Storyboard expanded one concept into {len(shots)} shot(s) for batch "
                f"{batch_request.batch_id}"
            )
        except Exception as e:  # noqa: BLE001 — storyboard must never fail a render
            logger.warning(f"Storyboard expansion skipped for batch {batch_request.batch_id} (non-fatal): {e}")

    @staticmethod
    def _to_i2v_model(model: Optional[str]) -> str:
        """Map a text-to-video model to its image-to-video sibling for cinematic mode.
        Defaults to the music-video quality animator (Wan 2.2 I2V)."""
        m = (model or "").lower()
        if "i2v" in m:
            return model  # already an I2V model
        if m.startswith("cogvideox"):
            return "cogvideox-5b-i2v"
        return "wan22-14b-i2v"

    def _generate_keyframe_still(self, *, prompt: str, width: int, height: int,
                                 out_path: str, seed: int,
                                 keyframe_model: Optional[str] = None) -> Optional[str]:
        """Cinematic mode: render a keyframe still for ``prompt``, then evict it from VRAM
        so the I2V animator can load (the music-video FLUX->i2v handoff — the i2v nodes
        don't ask ComfyUI to make room, so without this they OOM on a still-full card).

        Returns the still path on success, or None to fall back to plain text-to-video.
        Never raises."""
        try:
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator
            from backend.services.gpu_resource_policy import free_comfyui_vram
            # Snap to /16 (Wan/diffusion alignment); keep the still at the clip's aspect.
            w = max(256, (int(width) // 16) * 16)
            h = max(256, (int(height) // 16) * 16)
            # FLUX-schnell is the keyframe model (high aesthetic, low steps) — it uses the
            # same env-baked FLUX_* defaults the music-video keyframe path relies on, so no
            # settings need threading here. Operator can override via metadata.keyframe_model
            # (e.g. "sdxl"). If FLUX models aren't present, generate_image fails and we fall
            # back to plain text-to-video below.
            model = keyframe_model or "flux-schnell"
            steps = 8 if "flux" in model.lower() else 30  # flux-schnell is an 8-step model
            still = ComfyUIImageGenerator().generate_image(
                prompt=prompt,
                output_path=out_path,
                width=w,
                height=h,
                seed=int(seed),
                steps=steps,
                model=model,
            )
            # Evict the still model BEFORE the animator loads.
            try:
                free_comfyui_vram()
            except Exception:
                pass
            return still if still and Path(still).exists() else None
        except Exception as e:  # noqa: BLE001 — fall back to T2V, never fail the item here
            logger.warning(
                f"Cinematic keyframe generation failed ({e}); falling back to text-to-video"
            )
            return None

    def _run_batch(self, batch_request: BatchVideoRequest, status: BatchVideoStatus) -> None:
        # TWO GPU arbiters guard this batch:
        #   1. JobOperationGate.gpu_exclusive (in-memory) — serializes against
        #      production renders / training, which only know the in-memory gate.
        #   2. GPUResourceCoordinator file-lock — the cross-process arbiter that
        #      also stops Ollama. The two are otherwise mutually blind; claiming
        #      the in-memory gate here makes batch video visible to (and
        #      serialized with) the other in-memory surfaces.
        # Lock-ordering rule: acquire the GPU-exclusive (in-memory) gate FIRST,
        # then the file-lock. On in-memory contention -> GpuBusyError -> mark the
        # batch errored, same as a file-lock acquire failure.
        from backend.services.job_operation_gate import get_gate, GpuBusyError
        from backend.services.job_types import JobKind
        gate = get_gate()
        try:
            gate_cm = gate.gpu_exclusive(JobKind.VIDEO_RENDER, batch_request.batch_id)
            gate_cm.__enter__()
        except GpuBusyError as e:
            status.status = "error"
            status.error = f"Could not acquire GPU (in-memory gate busy): {e}"
            status.end_time = datetime.now()
            self._save_metadata(status)
            logger.error(f"Batch {batch_request.batch_id} blocked by in-memory GPU gate: {e}")
            return

        # Acquire GPU lock before starting video generation
        gpu_coordinator = get_gpu_coordinator()
        lock_result = gpu_coordinator.acquire_for_video_generation(
            batch_id=batch_request.batch_id,
            lease_seconds=3600  # 1 hour max
        )

        if not lock_result.get("success"):
            status.status = "error"
            status.error = f"Could not acquire GPU: {lock_result.get('error')}"
            status.end_time = datetime.now()
            self._save_metadata(status)
            logger.error(f"Batch {batch_request.batch_id} failed to acquire GPU lock: {lock_result.get('error')}")
            gate_cm.__exit__(None, None, None)  # release the in-memory gate
            return

        cancel_event = self.cancel_events.get(batch_request.batch_id)

        try:
            status.start_time = datetime.now()
            status.status = "running"
            self._save_metadata(status)

            batch_dir = Path(batch_request.output_dir)
            batch_dir.mkdir(parents=True, exist_ok=True)

            # Storyboard / Director pass: rewrite prompts into cinematic shot prompts before
            # generation. Runs here (background worker, not the HTTP handler) and never
            # raises. Storyboard expands ONE concept into N connected shots; it already
            # produces directed prompts, so it's mutually exclusive with the per-prompt
            # director (running both would just re-direct already-directed shots).
            if getattr(batch_request, "storyboard_concept", None):
                self._apply_storyboard(batch_request)
            elif getattr(batch_request, "director_mode", False):
                self._apply_director(batch_request)

            # Parallel processing of items within the batch (major P0 perf win).
            # The batch-level GPU locks are still held for the whole batch (safety),
            # but we overlap python work, status updates, and allow the Comfy queue
            # to see multiple jobs. Per-item cancel is checked.
            items = list(batch_request.items)
            if items:
                def _process_item(item):
                    """Inner worker: returns (batch_result, completed_delta, failed_delta, oom_flag)"""
                    if cancel_event and cancel_event.is_set():
                        return (BatchVideoResult(item_id=item.id, success=False, error="cancelled before start"), 0, 0, False)

                    try:
                        meta = dict(item.metadata or {})
                        meta.setdefault("item_id", item.id)
                        meta["batch_controlled"] = True
                        if item.image_path:
                            meta.setdefault("image_path", item.image_path)

                        # Cinematic mode: for a TEXT item, synthesize a keyframe still and
                        # animate it with Wan 2.2 I2V (the music-video quality path). An item
                        # that already brought its own image just uses that image as-is.
                        item_model = batch_request.model
                        if (getattr(batch_request, "cinematic_keyframe", False)
                                and not item.image_path and (item.prompt or "").strip()):
                            still_path = str(Path(batch_dir) / f"keyframe_{item.id}.png")
                            kf = self._generate_keyframe_still(
                                prompt=item.prompt,
                                width=batch_request.width,
                                height=batch_request.height,
                                out_path=still_path,
                                seed=(batch_request.seed if batch_request.seed is not None else 1000),
                                keyframe_model=(batch_request.metadata or {}).get("keyframe_model"),
                            )
                            if kf:
                                meta["image_path"] = kf
                                meta["cinematic_keyframe"] = True
                                item_model = self._to_i2v_model(batch_request.model)
                            # else: keyframe failed -> fall through to plain text-to-video

                        gen_request = VideoGenerationRequest(
                            prompt=item.prompt or "",
                            negative_prompt=batch_request.negative_prompt,
                            model=item_model,
                            duration_frames=batch_request.duration_frames,
                            fps=batch_request.fps,
                            width=batch_request.width,
                            height=batch_request.height,
                            motion_strength=batch_request.motion_strength,
                            num_inference_steps=batch_request.num_inference_steps,
                            guidance_scale=batch_request.guidance_scale,
                            seed=batch_request.seed,
                            generate_frames_only=batch_request.generate_frames_only,
                            frames_per_batch=batch_request.frames_per_batch,
                            combine_frames=batch_request.combine_frames,
                            output_dir=batch_dir,
                            metadata=meta,
                            interpolation_multiplier=batch_request.interpolation_multiplier,
                            prompt_style=batch_request.prompt_style,
                            enhance_prompt=batch_request.enhance_prompt,
                            fidelity_mode=batch_request.fidelity_mode,
                            freeu=batch_request.freeu,
                            face_restore=batch_request.face_restore,
                            lora_name=batch_request.lora_name,
                            lora_strength=batch_request.lora_strength,
                        )

                        result: VideoGenerationResult = self.video_generator.generate_video(gen_request)
                        br = BatchVideoResult(
                            item_id=item.id,
                            success=result.success,
                            video_path=result.video_path,
                            frame_paths=result.frame_paths,
                            thumbnail_path=result.thumbnail_path,
                            error=result.error,
                            metadata=result.metadata,
                        )
                        return (br, 1 if result.success else 0, 0 if result.success else 1, False)
                    except Exception as e:  # pragma: no cover
                        err_str = str(e)
                        logger.error(f"Error generating video for item {item.id}: {e}")
                        is_oom = (
                            isinstance(e, RuntimeError) and ("out of memory" in err_str.lower() or "cuda" in err_str.lower() and "memory" in err_str.lower())
                            or "torch.cuda.OutOfMemoryError" in str(type(e)) or "OutOfMemory" in err_str
                            or "CUDA out of memory" in err_str
                        )
                        oom_note = " (OOM - VRAM exhausted; try smaller res/fewer steps or evict other models)" if is_oom else ""
                        if is_oom and hasattr(self, "video_generator") and hasattr(self.video_generator, "service_available"):
                            try:
                                self.video_generator.service_available = False
                            except Exception:
                                pass
                        br = BatchVideoResult(
                            item_id=item.id,
                            success=False,
                            error=err_str + oom_note,
                        )
                        return (br, 0, 1, is_oom)

                # Cinematic mode does a stateful FLUX-still -> evict -> I2V handoff per item
                # on the shared GPU; concurrent items would evict each other's loaded model,
                # so it must run strictly serially.
                if getattr(batch_request, "cinematic_keyframe", False):
                    max_workers = 1
                else:
                    max_workers = max(1, min(4, len(items)))
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="video-item") as ex:
                    future_map = {ex.submit(_process_item, it): it for it in items}
                    for fut in as_completed(future_map):
                        if cancel_event and cancel_event.is_set():
                            status.status = "cancelled"
                            break
                        try:
                            br, dc, df, oom = fut.result()
                            status.results.append(br)
                            status.completed_videos += dc
                            status.failed_videos += df
                            if oom:
                                status.error = (status.error or "") + "OOM in batch item; "
                        except Exception as e:
                            it = future_map[fut]
                            logger.error(f"Item worker {it.id} failed: {e}")
                            status.failed_videos += 1
                            status.results.append(BatchVideoResult(item_id=it.id, success=False, error=str(e)))
                        finally:
                            self._save_metadata(status)

            if status.status != "cancelled":
                status.status = "completed" if status.failed_videos == 0 else "error"
            status.end_time = datetime.now()
            self._save_metadata(status)

            # Register videos into Documents/Files system
            if status.completed_videos > 0:
                try:
                    from flask import current_app
                    from backend.services.output_registration import ensure_subfolder, register_file
                    try:
                        app = current_app._get_current_object()
                    except RuntimeError:
                        # Worker thread has no request context — grab the singleton
                        # instead of rebuilding the entire Flask app from scratch.
                        from backend.app import get_or_create_app
                        app = get_or_create_app()
                    with app.app_context():
                        try:
                            batch_id = batch_request.batch_id
                            ensure_subfolder("Videos", batch_id)
                            batch_dir = Path(batch_request.output_dir)
                            # Register all video files found in the batch directory
                            for vid_file in sorted(batch_dir.rglob("*.mp4")):
                                register_file(
                                    physical_path=str(vid_file),
                                    folder_name="Videos",
                                    subfolder_name=batch_id,
                                    file_metadata={"source": "batch_generation", "batch_id": batch_id},
                                )
                            logger.info(f"Registered batch {batch_id} videos into Documents system")
                        finally:
                            from backend.models import db as _db
                            _db.session.remove()
                except Exception as reg_err:
                    logger.error(f"Failed to register batch videos: {reg_err}")

        finally:
            # Always release GPU lock when batch completes (success or failure).
            # Release in REVERSE acquire order: file-lock first, then the
            # in-memory gate (acquired first, released last).
            gpu_coordinator.release_video_generation_lock(restart_ollama=True)
            gate_cm.__exit__(None, None, None)
            logger.info(f"Batch {batch_request.batch_id} released GPU lock")

    def start_batch_from_prompts(
        self,
        prompts: List[str],
        **params,
    ) -> BatchVideoStatus:
        from backend.services.output_registration import bates_name
        batch_id = params.get("batch_id") or bates_name("video_batch", "", self.base_output_dir)
        items = [
            BatchVideoItem(id=str(uuid.uuid4()), prompt=p, metadata={"source": "prompt"})
            for p in prompts
        ]
        metadata = dict(params.get("metadata") or {})
        if not metadata.get("display_name") and prompts:
            metadata["display_name"] = _derive_display_name(prompts[0])
        params["metadata"] = metadata
        return self._start_batch(batch_id=batch_id, items=items, **params)

    def start_batch_from_images(
        self,
        image_paths: List[str],
        **params,
    ) -> BatchVideoStatus:
        from backend.services.output_registration import bates_name
        batch_id = params.get("batch_id") or bates_name("video_batch", "", self.base_output_dir)
        user_prompt = params.pop("prompt", "")
        items = [
            BatchVideoItem(
                id=str(uuid.uuid4()),
                prompt=user_prompt or f"Image-to-video: {Path(path).name}",
                image_path=path,
                metadata={"source": "image", "image_path": path},
            )
            for path in image_paths
        ]
        metadata = dict(params.get("metadata") or {})
        if not metadata.get("display_name"):
            seed_text = user_prompt or (Path(image_paths[0]).name if image_paths else "")
            if seed_text:
                metadata["display_name"] = _derive_display_name(seed_text)
        params["metadata"] = metadata
        return self._start_batch(batch_id=batch_id, items=items, **params)

    def _start_batch(self, batch_id: str, items: List[BatchVideoItem], **params) -> BatchVideoStatus:
        batch_dir = self._get_batch_dir(batch_id)
        batch_dir.mkdir(parents=True, exist_ok=True)

        seed_param = params.get("seed")
        seed_value = None
        if seed_param not in (None, ""):
            try:
                seed_value = int(seed_param)
            except Exception:
                seed_value = None

        batch_request = BatchVideoRequest(
            batch_id=batch_id,
            items=items,
            output_dir=str(batch_dir),
            model=params.get("model", "cogvideox-5b"),
            duration_frames=int(params.get("duration_frames", 25)),
            fps=int(params.get("fps", 7)),
            width=int(params.get("width", 512)),
            height=int(params.get("height", 512)),
            motion_strength=float(params.get("motion_strength", 1.0)),
            num_inference_steps=int(params.get("num_inference_steps", 25)),
            guidance_scale=float(params.get("guidance_scale", 7.5)),
            seed=seed_value,
            generate_frames_only=bool(params.get("generate_frames_only", False)),
            frames_per_batch=int(params.get("frames_per_batch", 1)),
            combine_frames=bool(params.get("combine_frames", False)),
            interpolation_multiplier=int(params.get("interpolation_multiplier", 2)),
            prompt_style=params.get("prompt_style", "cinematic"),
            enhance_prompt=bool(params.get("enhance_prompt", True)),
            fidelity_mode=bool(params.get("fidelity_mode", False)),
            negative_prompt=params.get("negative_prompt", "") or "",
            freeu=bool(params.get("freeu", False)),
            face_restore=bool(params.get("face_restore", False)),
            lora_name=params.get("lora_name"),
            lora_strength=float(params.get("lora_strength", 1.0)),
            director_mode=bool(params.get("director_mode", False)),
            cinematic_keyframe=bool(params.get("cinematic_keyframe", False)),
            director_guidance=params.get("director_guidance") or None,
            storyboard_concept=params.get("storyboard_concept") or None,
            metadata=params.get("metadata", {}),
        )

        status = BatchVideoStatus(
            batch_id=batch_id,
            status="queued",
            total_videos=len(items),
            output_dir=str(batch_dir),
            metadata=params.get("metadata", {}),
        )

        # Persist enough info to allow one-click retry for failed batches without
        # user re-entering all prompts, images, model, steps, fidelity, lora, freeu, tiers etc.
        try:
            is_image_mode = any(getattr(i, "image_path", None) for i in items)
            prompts_list = [i.prompt for i in items]
            image_paths_list = [i.image_path for i in items if getattr(i, "image_path", None)]
            retry_params = {
                "model": batch_request.model,
                "duration_frames": batch_request.duration_frames,
                "fps": batch_request.fps,
                "width": batch_request.width,
                "height": batch_request.height,
                "motion_strength": batch_request.motion_strength,
                "num_inference_steps": batch_request.num_inference_steps,
                "guidance_scale": batch_request.guidance_scale,
                "seed": batch_request.seed,
                "generate_frames_only": batch_request.generate_frames_only,
                "frames_per_batch": batch_request.frames_per_batch,
                "combine_frames": batch_request.combine_frames,
                "interpolation_multiplier": batch_request.interpolation_multiplier,
                "prompt_style": batch_request.prompt_style,
                "enhance_prompt": batch_request.enhance_prompt,
                "fidelity_mode": batch_request.fidelity_mode,
                "negative_prompt": batch_request.negative_prompt,
                "freeu": batch_request.freeu,
                "face_restore": batch_request.face_restore,
                "lora_name": batch_request.lora_name,
                "lora_strength": batch_request.lora_strength,
                "metadata": dict(batch_request.metadata or {}),
            }
            if is_image_mode:
                status.retry_data = {
                    "mode": "image",
                    "image_paths": image_paths_list,
                    "prompt": prompts_list[0] if prompts_list else "",
                    "params": retry_params,
                }
            else:
                status.retry_data = {
                    "mode": "text",
                    "prompts": prompts_list,
                    "params": retry_params,
                }
        except Exception:
            # best effort; old batches without it are still loadable
            pass

        with self.batch_lock:
            self.active_batches[batch_id] = status
            self.cancel_events[batch_id] = threading.Event()
            self.queue_order.append(batch_id)

        # Persist queued state immediately so a restart leaves a discoverable trail
        # (Phase 2 will use this for opt-in resume).
        self._save_metadata(status)

        # Stack it on the queue. The single worker thread drains one batch at a time.
        self.batch_queue.put((batch_request, status))
        logger.info(
            f"Enqueued batch {batch_id} ({len(items)} items) — "
            f"queue depth ~{self.batch_queue.qsize()}"
        )

        return status

    def get_batch_status(self, batch_id: str) -> Optional[BatchVideoStatus]:
        with self.batch_lock:
            status = self.active_batches.get(batch_id)
        if status:
            return status

        # Try to load from disk
        batch_dir = self._get_batch_dir(batch_id)
        metadata_file = batch_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r") as f:
                    data = json.load(f)
                results = [
                    BatchVideoResult(**res)
                    for res in data.get("results", [])
                ]
                # Retroactively extract thumbnails for results that have videos but no thumbnail
                metadata_changed = False
                for res in results:
                    if res.video_path and not res.thumbnail_path:
                        video_file = batch_dir / res.video_path
                        if video_file.exists() and video_file.suffix.lower() in (".mp4", ".webm", ".avi", ".mov"):
                            thumb_filename = video_file.stem + "_thumb.jpg"
                            # Place thumbnail in a thumbnails subdir next to the video
                            thumbs_dir = video_file.parent.parent / "thumbnails"
                            thumb_path = thumbs_dir / thumb_filename
                            if self._extract_thumbnail(video_file, thumb_path):
                                res.thumbnail_path = str(thumb_path.relative_to(batch_dir))
                                metadata_changed = True
                if metadata_changed:
                    # Persist the updated thumbnail paths back to metadata
                    try:
                        for i, res in enumerate(results):
                            if res.thumbnail_path and i < len(data.get("results", [])):
                                data["results"][i]["thumbnail_path"] = res.thumbnail_path
                        with open(metadata_file, "w") as f:
                            json.dump(data, f, indent=2)
                    except Exception:
                        pass  # Best effort

                start_time = datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None
                end_time = datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None
                return BatchVideoStatus(
                    batch_id=data["batch_id"],
                    status=data.get("status", "completed"),
                    total_videos=data.get("total_videos", len(results)),
                    completed_videos=data.get("completed_videos", 0),
                    failed_videos=data.get("failed_videos", 0),
                    start_time=start_time,
                    end_time=end_time,
                    results=results,
                    error=data.get("error"),
                    output_dir=data.get("output_dir"),
                    metadata=data.get("metadata", {}),
                    retry_data=data.get("retry_data"),
                )
            except Exception as e:  # pragma: no cover
                logger.error(f"Failed to load batch status for {batch_id}: {e}")
                return None
        return None

    def cancel_batch(self, batch_id: str) -> bool:
        """Cancel a queued or running batch.

        Two-layer interrupt: flip the cancel event (so the worker bails out
        between items) and, if the batch is mid-render, yell at ComfyUI's
        /interrupt endpoint so the current sampler aborts immediately.
        """
        cancellable = ("queued", "running", "pending", "processing")

        # In-memory path — covers anything queued or running
        with self.batch_lock:
            status = self.active_batches.get(batch_id)
            event = self.cancel_events.get(batch_id)

        if status and status.status in cancellable:
            if event:
                event.set()

            was_running = (status.status == "running") or (self._running_batch_id == batch_id)
            status.status = "cancelled"
            status.end_time = datetime.now()
            if not status.error:
                status.error = "Cancelled by user"
            self._save_metadata(status)

            if was_running:
                # Force ComfyUI to abort the current sampler. Without this,
                # cancel only fires between items — useless for a 20-min Wan run.
                try:
                    interrupted = self.video_generator.interrupt()
                    logger.info(
                        f"Cancel batch {batch_id}: running, interrupt sent "
                        f"(ack={interrupted})"
                    )
                except Exception as e:
                    logger.warning(f"Cancel batch {batch_id}: interrupt call failed: {e}")
            else:
                logger.info(f"Cancel batch {batch_id}: queued, will skip when worker reaches it")
            return True

        # Fall back to on-disk metadata for batches no longer tracked in memory
        batch_dir = self._get_batch_dir(batch_id)
        metadata_file = batch_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r") as f:
                    data = json.load(f)
                if data.get("status") in cancellable:
                    data["status"] = "cancelled"
                    data["end_time"] = datetime.now().isoformat()
                    if not data.get("error"):
                        data["error"] = "Cancelled by user"
                    with open(metadata_file, "w") as f:
                        json.dump(data, f, indent=2)
                    logger.info(f"Cancelled on-disk batch {batch_id}")
                    return True
            except Exception as e:
                logger.error(f"Failed to cancel batch {batch_id}: {e}")
                return False
        return False

    @staticmethod
    def _emit_canonical_job_event(batch_status: BatchVideoStatus) -> None:
        """Push video_gen updates to the unified jobs:* socket channel."""
        try:
            from backend.services.job_registry import adapt_video_gen
            from backend.socketio_instance import socketio
            job_dict = adapt_video_gen(batch_status).to_dict()
            socketio.emit("job:event", job_dict, to="jobs:all", namespace="/")
            socketio.emit("job:event", job_dict, to="jobs:video_gen", namespace="/")
        except Exception as e:
            logger.debug(f"Failed to emit canonical job event for {batch_status.batch_id}: {e}")

    _ACTIVE_STATUSES = frozenset({"queued", "pending", "running", "processing"})

    def list_active_batches(self) -> List[BatchVideoStatus]:
        """In-memory and on-disk batches that are still queued or running."""
        seen: set[str] = set()
        active: List[BatchVideoStatus] = []

        with self.batch_lock:
            running_id = self._running_batch_id
            for batch_id, status in self.active_batches.items():
                if status.status in self._ACTIVE_STATUSES:
                    active.append(status)
                    seen.add(batch_id)

        try:
            for batch_dir in self.base_output_dir.iterdir():
                if not batch_dir.is_dir():
                    continue
                batch_id = batch_dir.name
                if batch_id in seen:
                    continue
                metadata_file = batch_dir / "batch_metadata.json"
                if not metadata_file.exists():
                    continue
                try:
                    with open(metadata_file, "r") as f:
                        data = json.load(f)
                except Exception:
                    continue
                if data.get("status") not in self._ACTIVE_STATUSES:
                    continue
                loaded = self.get_batch_status(batch_id)
                if loaded:
                    active.append(loaded)
                    seen.add(batch_id)
        except Exception as e:
            logger.warning(f"Failed to scan for active batches: {e}")

        return active

    def list_batches_for_jobs(self, *, limit: int = 100) -> List[Dict]:
        """Snapshot for /api/jobs — active batches first, then recent history."""
        snapshot: List[Dict] = []
        seen: set[str] = set()

        with self.batch_lock:
            running_id = self._running_batch_id
            order = list(self.queue_order)

        position = 0
        for batch_id in order:
            with self.batch_lock:
                status = self.active_batches.get(batch_id)
            if not status:
                continue
            position += 1
            entry = self._batch_status_to_job_row(status, position=position, is_running=batch_id == running_id)
            snapshot.append(entry)
            seen.add(batch_id)

        for status in self.list_active_batches():
            if status.batch_id in seen:
                continue
            entry = self._batch_status_to_job_row(
                status,
                position=None,
                is_running=(status.batch_id == self._running_batch_id),
            )
            snapshot.append(entry)
            seen.add(status.batch_id)

        for row in self.list_batches():
            batch_id = row.get("batch_id")
            if not batch_id or batch_id in seen:
                continue
            row = dict(row)
            row.setdefault("metadata", {})
            if row.get("display_name"):
                row["metadata"]["display_name"] = row["display_name"]
            snapshot.append(row)
            seen.add(batch_id)
            if len(snapshot) >= limit:
                break

        return snapshot[:limit]

    @staticmethod
    def _batch_status_to_job_row(
        status: BatchVideoStatus,
        *,
        position: int | None,
        is_running: bool,
    ) -> Dict:
        metadata = dict(status.metadata or {})
        if position is not None:
            metadata["queue_position"] = position
        metadata["is_running"] = is_running
        return {
            "batch_id": status.batch_id,
            "status": status.status,
            "total_videos": status.total_videos,
            "completed_videos": status.completed_videos,
            "failed_videos": status.failed_videos,
            "start_time": status.start_time.isoformat() if status.start_time else None,
            "end_time": status.end_time.isoformat() if status.end_time else None,
            "error": status.error,
            "metadata": metadata,
            "display_name": metadata.get("display_name"),
            "is_running": is_running,
        }

    def cancel_all_active(self, reason: str = "Cancelled by system shutdown") -> List[str]:
        """Cancel every queued/running batch and release GPU resources."""
        cancelled: List[str] = []
        for status in self.list_active_batches():
            batch_id = status.batch_id
            if self.cancel_batch(batch_id):
                cancelled.append(batch_id)
                if not status.error:
                    status.error = reason
                    self._save_metadata(status)

        if cancelled:
            try:
                self.video_generator.interrupt()
            except Exception as e:
                logger.warning(f"cancel_all_active: ComfyUI interrupt failed: {e}")

        try:
            gpu_coordinator = get_gpu_coordinator()
            gpu_coordinator.release_video_generation_lock(restart_ollama=False)
        except Exception as e:
            logger.warning(f"cancel_all_active: GPU lock release failed: {e}")

        try:
            from backend.services.job_operation_gate import get_gate
            from backend.services.job_types import JobKind
            gate = get_gate()
            snap = gate.snapshot()
            holder = snap.get("gpu_holder") or {}
            if holder.get("kind") == JobKind.VIDEO_RENDER.value:
                gate.release_gpu_exclusive(JobKind.VIDEO_RENDER, str(holder.get("native_id", "")))
        except Exception as e:
            logger.warning(f"cancel_all_active: gate release failed: {e}")

        logger.info(f"cancel_all_active: cancelled {len(cancelled)} batch(es): {cancelled}")
        return cancelled

    def list_queue(self) -> List[Dict]:
        """Snapshot of the current queue for the UI panel.

        Returns batches in submission order with a position number.
        Includes queued, running, and recently completed/cancelled batches
        from the in-memory active set.
        """
        snapshot = []
        with self.batch_lock:
            order = list(self.queue_order)
            running_id = self._running_batch_id

        position = 0
        for batch_id in order:
            with self.batch_lock:
                status = self.active_batches.get(batch_id)
            if not status:
                continue
            position += 1
            snapshot.append({
                "position": position,
                "batch_id": batch_id,
                "status": status.status,
                "total_videos": status.total_videos,
                "completed_videos": status.completed_videos,
                "failed_videos": status.failed_videos,
                "is_running": batch_id == running_id,
                "start_time": status.start_time.isoformat() if status.start_time else None,
                "end_time": status.end_time.isoformat() if status.end_time else None,
                "display_name": (status.metadata or {}).get("display_name"),
                "error": status.error,
            })
        return snapshot

    def _cleanup_stale_batches(self) -> None:
        """Mark batches stuck in running/pending/processing as cancelled.

        Called during list_batches to auto-recover from crashes/restarts.
        Only affects batches that are NOT actively tracked in memory.
        """
        try:
            for batch_dir in self.base_output_dir.iterdir():
                if not batch_dir.is_dir():
                    continue
                metadata_file = batch_dir / "batch_metadata.json"
                if not metadata_file.exists():
                    continue
                batch_id = batch_dir.name

                # Skip batches that are actively tracked in memory
                with self.batch_lock:
                    if batch_id in self.active_batches:
                        continue

                try:
                    with open(metadata_file, "r") as f:
                        data = json.load(f)
                    if data.get("status") in ("running", "pending", "processing"):
                        data["status"] = "cancelled"
                        data["end_time"] = datetime.now().isoformat()
                        data["error"] = "Interrupted by system restart"
                        with open(metadata_file, "w") as f:
                            json.dump(data, f, indent=2)
                        logger.info(f"Auto-cancelled stale batch {batch_id}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup stale batch {batch_id}: {e}")
        except Exception as e:
            logger.warning(f"Failed to scan for stale batches: {e}")

    def list_batches(self) -> List[Dict]:
        # Auto-cleanup stale batches from previous runs
        self._cleanup_stale_batches()

        batches = []
        try:
            for batch_dir in self.base_output_dir.iterdir():
                if not batch_dir.is_dir():
                    continue
                metadata_file = batch_dir / "batch_metadata.json"
                batch_id = batch_dir.name
                entry = {"batch_id": batch_id, "status": "unknown"}
                if metadata_file.exists():
                    try:
                        with open(metadata_file, "r") as f:
                            data = json.load(f)
                        entry.update(
                            {
                                "status": data.get("status", "unknown"),
                                "total_videos": data.get("total_videos", 0),
                                "completed_videos": data.get("completed_videos", 0),
                                "failed_videos": data.get("failed_videos", 0),
                                "start_time": data.get("start_time"),
                                "end_time": data.get("end_time"),
                                "display_name": data.get("metadata", {}).get("display_name"),
                                "can_retry": bool(data.get("retry_data")),
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to read metadata for {batch_id}: {e}")
                batches.append(entry)
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to list video batches: {e}")
        # Newest first so the Video Library shows recent work at the top (and any
        # caller capping the list keeps the most recent batches). start_time is an
        # ISO-8601 string when present; missing/unknown sort to the bottom.
        batches.sort(key=lambda b: b.get("start_time") or b.get("end_time") or "", reverse=True)
        return batches

    def delete_batch(self, batch_id: str) -> bool:
        batch_dir = self._get_batch_dir(batch_id)
        if not batch_dir.exists():
            return False
        try:
            shutil.rmtree(batch_dir)
            with self.batch_lock:
                self.active_batches.pop(batch_id, None)
            return True
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to delete batch {batch_id}: {e}")
            return False

    def rename_batch(self, batch_id: str, new_name: str) -> bool:
        batch_dir = self._get_batch_dir(batch_id)
        if not batch_dir.exists():
            return False
        metadata_file = batch_dir / "batch_metadata.json"
        try:
            if metadata_file.exists():
                with open(metadata_file, "r") as f:
                    data = json.load(f)
                data.setdefault("metadata", {})["display_name"] = new_name
                with open(metadata_file, "w") as f:
                    json.dump(data, f, indent=2)
            return True
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to rename batch {batch_id}: {e}")
            return False

    def get_preview_thumbnail(self, batch_id: str) -> Optional[Path]:
        batch_dir = self._get_batch_dir(batch_id)
        if batch_dir.exists():
            thumbs = sorted(batch_dir.glob("**/thumbnails/*.jpg"))
            if thumbs:
                return thumbs[0]
        return None

    def combine_frames(self, batch_id: str, item_id: Optional[str] = None, fps: int = 7) -> Optional[str]:
        batch_dir = self._get_batch_dir(batch_id)
        if not batch_dir.exists():
            return None

        # Determine target item directory
        item_dir: Optional[Path] = None
        if item_id:
            candidate = batch_dir / item_id
            if candidate.exists():
                item_dir = candidate
        else:
            # Best-effort fallback: use the first item frames directory
            candidates = sorted(batch_dir.glob("*/frames"))
            if candidates:
                item_dir = candidates[0].parent

        if not item_dir:
            return None

        frames_dir = item_dir / "frames"
        videos_dir = item_dir / "videos"
        if not frames_dir.exists():
            return None
        videos_dir.mkdir(parents=True, exist_ok=True)

        # Each item_dir holds a single rendered video — give it a clean,
        # predictable name. The collision resolver in register_file applies
        # if anything ends up registered into the same DB folder twice.
        # Legacy: f"video_{uuid.uuid4().hex}.mp4" left hex visible in the UI.
        video_path = videos_dir / "video.mp4"
        if video_path.exists():
            # Same item_dir got re-rendered (rare). Add a sequential suffix
            # using the same Files-app convention the resolver uses.
            from backend.utils.filename_resolver import _split_existing_suffix
            stem, n = _split_existing_suffix("video")
            while video_path.exists():
                n += 1
                video_path = videos_dir / f"{stem} ({n}).mp4"
        combined = self.video_generator._combine_frames_to_video(frames_dir, video_path, fps)
        if not combined:
            return None

        rel_path = str(Path(combined).relative_to(batch_dir))

        # Update in-memory status if present
        with self.batch_lock:
            status = self.active_batches.get(batch_id)
            if status:
                for res in status.results:
                    if res.item_id == item_dir.name:
                        res.video_path = rel_path
                        res.success = res.success or bool(res.frame_paths)
                self._save_metadata(status)
                return rel_path

        # Update persisted metadata if batch not active
        metadata_file = batch_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r") as f:
                    data = json.load(f)
                for res in data.get("results", []):
                    if res.get("item_id") == item_dir.name:
                        res["video_path"] = rel_path
                with open(metadata_file, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to update metadata after combining frames: {e}")

        return rel_path


_batch_video_generator_instance: Optional[BatchVideoGenerator] = None


def get_batch_video_generator() -> BatchVideoGenerator:
    global _batch_video_generator_instance
    if _batch_video_generator_instance is None:
        _batch_video_generator_instance = BatchVideoGenerator()
    return _batch_video_generator_instance

