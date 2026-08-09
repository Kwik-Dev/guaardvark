
import logging
import json
import subprocess
import time
import os
import shutil
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import uuid

import requests

logger = logging.getLogger(__name__)

try:
    from backend.config import CACHE_DIR, COMFYUI_URL, COMFYUI_OUTPUT_DIR
    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"

# Wan loader filenames are DERIVED from the shared registry (issue #36) so the
# generator always loads exactly what the downloader wrote — no third hand-edited
# copy to drift. Falls back to {} if the registry can't be imported; the loaders
# already tolerate a missing entry, so import never hard-fails over this.
try:
    from backend.services.video_model_registry import wan_comfyui_map as _wan_comfyui_map
    from backend.services.video_model_registry import ltx_comfyui_map as _ltx_comfyui_map
except Exception:  # pragma: no cover - defensive
    def _wan_comfyui_map():
        return {}

    def _ltx_comfyui_map():
        return {}


def _looks_like_blank_video(video_path) -> Optional[str]:
    """Zero-placebo guard for the ComfyUI/Wan path (issue #36 Phase 3).

    Returns a human-readable REASON string if the rendered file is obviously not a
    real video — missing, an empty/stub mux, or fully black for ~its whole
    duration — else None (looks fine). ComfyUI can emit a black clip when a loader
    silently fails (e.g. a missing model quant), and the old code reported that as
    success. This mirrors the offline path's no-placebo guard.

    FAIL-OPEN: if ffmpeg is unavailable or we can't decode/measure the file, return
    None. Never block a real render just because the *checker* couldn't run — the
    point is to catch the obvious blank, not to gate on inspection success.
    """
    import re
    import subprocess
    try:
        from pathlib import Path as _P
        p = _P(video_path)
        if not p.exists():
            return "render produced no output file"
        size = p.stat().st_size
        if size < 10 * 1024:  # a real clip is far larger; <10KB is a stub/empty mux
            return f"render output is only {size} bytes — an empty/failed clip"
        if p.suffix.lower() not in (".mp4", ".webm", ".avi", ".mov"):
            return None  # not a container we can black-scan; size check already passed

        # blackdetect with pic_th=0.98 flags ~fully-black frames only. A real clip —
        # even a dark/cinematic one — is not 98%-black pixels for ~its whole runtime,
        # so this only trips on a genuinely blank render (very low false-positive).
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
             "-vf", "blackdetect=d=0.1:pic_th=0.98", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        stderr = proc.stderr or ""
        dur_m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
        if not dur_m:
            return None  # couldn't read duration -> fail open
        h, m, s = dur_m.groups()
        total = int(h) * 3600 + int(m) * 60 + float(s)
        black = sum(float(x) for x in re.findall(r"black_duration:(\d+(?:\.\d+)?)", stderr))
        if total > 0 and black >= 0.95 * total:
            return f"render is black for {black:.1f}s of {total:.1f}s — a blank/failed clip"
        return None
    except Exception as e:  # noqa: BLE001 — fail open on a broken checker
        logger.debug(f"blank-video check skipped ({e})")
        return None


@dataclass
class VideoGenerationRequest:
    prompt: str = ""
    negative_prompt: str = ""
    model: str = "cogvideox-5b"
    duration_frames: int = 25
    fps: int = 24  # 24fps default — Wan 5B is 24fps-native (was 7 → choppy slow-motion)
    width: int = 512
    height: int = 512
    motion_strength: float = 1.0
    num_inference_steps: int = 25
    guidance_scale: float = 7.5
    seed: Optional[int] = None
    generate_frames_only: bool = False
    frames_per_batch: int = 1
    combine_frames: bool = False
    output_dir: Optional[Path] = None
    metadata: Dict[str, str] = field(default_factory=dict)
    interpolation_multiplier: int = 2  # 1 = no interpolation, 2 = double fps, 4 = quad fps
    prompt_style: str = "cinematic"   # Enhancement style: cinematic, realistic, artistic, anime, none
    enhance_prompt: bool = True       # Whether to run prompt through the enhancer
    fidelity_mode: bool = False       # Light enhancement only (Exact text / preserve fidelity mode)
    freeu: bool = False
    face_restore: bool = False
    lora_name: Optional[str] = None
    lora_strength: float = 1.0


@dataclass
class VideoGenerationResult:
    success: bool
    prompt_used: str = ""
    video_path: Optional[str] = None
    frame_paths: List[str] = field(default_factory=list)
    thumbnail_path: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)


class ComfyUIVideoGenerator:

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent

        self.comfy_url = COMFYUI_URL if config_available else os.environ.get("GUAARDVARK_COMFYUI_URL", "http://127.0.0.1:8188")

        self.templates_dir = project_root / "data" / "templates"
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        self.cache_dir = Path(CACHE_DIR) / "generated_videos"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Default output dir for standalone (non-batch) video generation
        try:
            from backend.config import UPLOAD_DIR as _upload_dir
            self.default_output_dir = Path(_upload_dir) / "Videos"
        except ImportError:
            self.default_output_dir = self.cache_dir
        self.default_output_dir.mkdir(parents=True, exist_ok=True)

        self.comfy_output_dir = Path(COMFYUI_OUTPUT_DIR if config_available else os.environ.get('COMFYUI_OUTPUT_DIR', os.path.join(os.environ.get('GUAARDVARK_ROOT', '.'), 'data', 'outputs', 'video')))

        self.service_available = self._check_comfyui_connection()
        self._object_info_cache: Optional[dict] = None

        if self.service_available:
            logger.info(f"ComfyUI video generator connected to {self.comfy_url}")
        else:
            logger.warning(f"ComfyUI not available at {self.comfy_url}. Video generation will fail unless ComfyUI is started.")

    def _get_object_info(self) -> dict:
        """Fetch ComfyUI /object_info once and cache (node class availability probe)."""
        if self._object_info_cache is not None:
            return self._object_info_cache
        try:
            response = requests.get(f"{self.comfy_url}/object_info", timeout=5)
            response.raise_for_status()
            self._object_info_cache = response.json()
        except Exception as e:
            logger.debug(f"Could not fetch ComfyUI object_info: {e}")
            self._object_info_cache = {}
        return self._object_info_cache

    def comfy_node_available(self, class_type: str) -> bool:
        if not self.service_available and not self._check_comfyui_connection():
            return False
        return class_type in self._get_object_info()

    def _check_comfyui_connection(self) -> bool:
        try:
            response = requests.get(self.comfy_url, timeout=2)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def _upload_image_to_comfyui(self, image_path: str) -> Optional[str]:
        try:
            with open(image_path, 'rb') as f:
                files = {'image': f}
                data = {'type': 'input', 'overwrite': 'true'}
                response = requests.post(
                    f"{self.comfy_url}/upload/image",
                    files=files,
                    data=data,
                    timeout=30
                )
                response.raise_for_status()

            result = response.json()
            uploaded_name = result.get("name")
            logger.info(f"Uploaded image to ComfyUI as: {uploaded_name}")
            return uploaded_name

        except Exception as e:
            logger.error(f"Failed to upload image to ComfyUI: {e}")
            return None

    def _create_svd_workflow(
        self,
        image_filename: str,
        num_frames: int = 25,
        motion_bucket_id: int = 127,
        fps: int = 7,
        seed: Optional[int] = None,
    ) -> dict:
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        workflow = {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": 20,
                    "cfg": 2.5,
                    "sampler_name": "euler",
                    "scheduler": "karras",
                    "denoise": 1,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0]
                }
            },
            "4": {
                "class_type": "ImageOnlyCheckpointLoader",
                "inputs": {
                    "ckpt_name": "svd_xt.safetensors"
                }
            },
            "5": {
                "class_type": "SVD_img2vid_Conditioning",
                "inputs": {
                    "width": 512,
                    "height": 512,
                    "video_frames": num_frames,
                    "motion_bucket_id": motion_bucket_id,
                    "fps": fps,
                    "augmentation_level": 0,
                    "clip_vision": ["4", 1],
                    "init_image": ["8", 0],
                    "vae": ["4", 2]
                }
            },
            "6": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": 512,
                    "height": 512,
                    "batch_size": 1
                }
            },
            "7": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": 512,
                    "height": 512,
                    "batch_size": 1
                }
            },
            "8": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": image_filename
                }
            },
            "9": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["3", 0],
                    "vae": ["4", 2]
                }
            },
            "10": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "svd_video",
                    "format": "video/h264-mp4",
                    "images": ["9", 0]
                }
            }
        }

        return workflow

    # ── CogVideoX model mapping ──────────────────────────────────────────────

    COGVIDEOX_MODELS = {
        "cogvideox-5b": "THUDM/CogVideoX-5b",
        "cogvideox-5b-i2v": "kijai/CogVideoX-5b-1.5-I2V",
    }

    # Conservative best-effort floor for the TOTAL VRAM a model needs to run at
    # all (not headroom-for-comfort). Used by the preflight in generate_video to
    # turn a silent OOM into an honest "this model needs ~N GB" message on the
    # install base. Keyed by the model id; family fallbacks below cover aliases.
    #
    # Note on real hardware (2026-06): "16 GB" consumer cards (e.g. 4070 Ti SUPER)
    # commonly report 15900-16400 MB total via pynvml/nvidia-smi/ComfyUI because
    # of driver/display reservation. The GGUF Q5 WAN 14B paths + music-video's
    # 832x480 preview res were explicitly built for this class of card.
    # Preflight therefore uses a small tolerance (see _vram_preflight) so it only
    # hard-blocks on truly under-spec hardware while still giving clear guidance.
    MODEL_MIN_VRAM_GB = {
        "cogvideox-2b": 8,
        "cogvideox-5b": 16,
        "cogvideox-5b-i2v": 16,
        "wan22-14b": 16,
        "wan22-14b-i2v": 16,
        "ltx23-distilled-fp8": 16,
    }
    # Family floors when an exact id isn't in the table (aliases like "wan22").
    _FAMILY_MIN_VRAM_GB = {"wan": 16, "cogvideox": 16, "ltx": 16}

    # ── Wan 2.2 model mapping ────────────────────────────────────────────────
    # DERIVED from the shared registry (backend/services/video_model_registry.py)
    # so these loader paths can never drift from what the downloader writes
    # (issue #36). To change a Wan filename, edit the registry's `files` — not here.
    WAN22_MODELS = _wan_comfyui_map()
    # LTX-2.3 loader map — same SSOT pattern as Wan.
    LTX_MODELS = _ltx_comfyui_map()

    # CogVideoX/Wan are 8x VAE × 2x patch → /16. SVD is U-Net only → /8.
    # LTX-2.3 spatial downscale is 32 (see EmptyLTXVLatentVideo).
    # Mirror of MODEL_OPTIONS[*].dimensionAlignment in VideoGeneratorPage.jsx —
    # the frontend should already snap, this is the defense-in-depth seam for
    # API/MCP/agent callers that go straight to the workflow builders.
    _DIMENSION_ALIGNMENT_BY_FAMILY = {
        "cogvideox": 16,
        "wan": 16,
        "svd": 8,
        "ltx": 32,
    }

    @classmethod
    def _ensure_wan_models(cls) -> dict:
        """The Wan loader map is derived from the registry at IMPORT time (`WAN22_MODELS`
        above); if that derivation froze to {} (a transient registry/circular-import hiccup
        at module load) Wan generation would be silently dead for the whole process. Re-resolve
        lazily here and cache the first non-empty result, so a one-time import-order problem
        can't permanently break Wan video. No-op once the map is populated."""
        if not cls.WAN22_MODELS:
            try:
                from backend.services.video_model_registry import wan_comfyui_map
                fresh = wan_comfyui_map() or {}
                if fresh:
                    cls.WAN22_MODELS = fresh
            except Exception:  # pragma: no cover - defensive
                pass
        return cls.WAN22_MODELS

    @classmethod
    def _ensure_ltx_models(cls) -> dict:
        """Same lazy re-resolve as `_ensure_wan_models` for the LTX-2.3 map."""
        if not cls.LTX_MODELS:
            try:
                from backend.services.video_model_registry import ltx_comfyui_map
                fresh = ltx_comfyui_map() or {}
                if fresh:
                    cls.LTX_MODELS = fresh
            except Exception:  # pragma: no cover - defensive
                pass
        return cls.LTX_MODELS

    @classmethod
    def _model_family(cls, model: str) -> str:
        cls._ensure_wan_models()  # unfreeze the map if it froze empty at import
        cls._ensure_ltx_models()
        if model in cls.LTX_MODELS or str(model).startswith("ltx"):
            return "ltx"
        if model in cls.WAN22_MODELS or model in ("wan22", "wan2.2"):
            return "wan"
        if model in cls.COGVIDEOX_MODELS:
            return "cogvideox"
        return "cogvideox"  # SVD retired; unknown models default to the cogvideox family

    @staticmethod
    def _ltx_frame_count(num_frames: int) -> int:
        """LTX-2.3 latent length must be 8n+1 (65, 97, 121, 161, …)."""
        n = max(9, int(num_frames or 65))
        snapped = ((n - 1) // 8) * 8 + 1
        if snapped < 9:
            snapped = 9
        return snapped

    # Timeout guard per family: ~1.0 MPx (1280×736) is proven on 16GB cards;
    # 3.7 MPx (1920×1920) never finished on either Wan. Aspect is preserved.
    _MAX_PIXEL_AREA_BY_FAMILY = {"wan": 1_050_000, "ltx": 1_050_000}

    @classmethod
    def _clamp_pixel_area(cls, width: int, height: int, model: str) -> tuple[int, int]:
        """Scale (width, height) down to the family's pixel-area budget,
        preserving aspect ratio. No-op when unbudgeted or already within it."""
        cap = cls._MAX_PIXEL_AREA_BY_FAMILY.get(cls._model_family(model))
        area = int(width) * int(height)
        if not cap or area <= cap:
            return width, height
        scale = (cap / area) ** 0.5
        new_w, new_h = int(width * scale), int(height * scale)
        logger.warning(
            "Clamped %s video dims %dx%d (%.1f MPx) → %dx%d to stay within the "
            "%.1f MPx budget — larger frames time out on this hardware",
            model, width, height, area / 1e6, new_w, new_h, cap / 1e6,
        )
        return new_w, new_h

    @classmethod
    def _align_dimensions(cls, width: int, height: int, model: str) -> tuple[int, int]:
        """Snap (width, height) to the model family's required alignment.

        Logs a WARNING when the input wasn't already aligned — that's our
        breadcrumb if a caller bypasses the frontend's snap.
        """
        align = cls._DIMENSION_ALIGNMENT_BY_FAMILY.get(cls._model_family(model), 16)
        new_w = max(align, round(width / align) * align)
        new_h = max(align, round(height / align) * align)
        if (new_w, new_h) != (width, height):
            logger.warning(
                "Aligned video dims for %s: %dx%d → %dx%d (must be multiple of %d)",
                model, width, height, new_w, new_h, align,
            )
        return new_w, new_h

    @classmethod
    def _min_vram_gb_for(cls, model: str) -> int:
        """Conservative TOTAL-VRAM floor (GB) for `model`. Exact id wins; falls
        back to the model family; 0 means 'no floor known' (don't block)."""
        if model in cls.MODEL_MIN_VRAM_GB:
            return cls.MODEL_MIN_VRAM_GB[model]
        return cls._FAMILY_MIN_VRAM_GB.get(cls._model_family(model), 0)

    # Wan UMT5 TE is ~6.4 GB fully resident. On 16–20 GB cards that leaves the
    # ~10 GB GGUF UNet with ~300 MB usable → CPU offload thrash (~150 s/step).
    # CLIPLoader supports device="cpu" (same weights/math; encode is slower once,
    # sample runs at full GPU speed). Above this total we leave TE on GPU.
    # Override: GUAARDVARK_WAN_CLIP_DEVICE=cpu|default
    WAN_CLIP_CPU_TOTAL_VRAM_MB = 20 * 1024

    @classmethod
    def _wan_clip_device(cls, total_vram_mb: Optional[int] = None) -> str:
        """Where Wan CLIP/UMT5 should load: ``cpu`` on consumer VRAM, else default.

        Quality-preserving residency control (mirrors CogVideoX force_offload):
        TE on CPU frees the full card for UNet+VAE instead of stacking TE+UNet
        until Comfy partial-loads the diffusion model. Same fp/quant weights —
        no model downshift.
        """
        override = (os.environ.get("GUAARDVARK_WAN_CLIP_DEVICE") or "").strip().lower()
        if override in ("cpu", "default"):
            return override

        total_mb = total_vram_mb
        if total_mb is None:
            try:
                from backend.services.gpu_resource_coordinator import get_available_vram
                info = get_available_vram()
                if info.get("success"):
                    total_mb = int(info.get("total_mb") or 0) or None
            except Exception:  # noqa: BLE001
                total_mb = None

        # Unknown probe → prefer CPU (safe on 16 GB; harmless quality-wise on larger).
        if total_mb is None or total_mb <= 0:
            return "cpu"
        if total_mb <= cls.WAN_CLIP_CPU_TOTAL_VRAM_MB:
            return "cpu"
        return "default"

    @staticmethod
    def _wan_dynamic_shift(width: int, height: int) -> float:
        """ModelSamplingSD3 shift for Wan, scaled to resolution.

        SD3/flow-matching shift should scale with resolution/sequence-length.
        Base shift 8.0 is tuned for 1280x704; at lower resolutions (e.g. 736x416)
        8.0 is too strong, leading to blurry output and poor motion.
        """
        base_area = 1280 * 704
        return round(max(3.0, min(12.0, 8.0 * ((width * height) / base_area))), 1)

    def _vram_preflight(self, model: str) -> Optional[str]:
        """Read-only VRAM gate run BEFORE queuing a ComfyUI job, so an
        under-spec card gets an honest message instead of a silent OOM mid-render.

        Returns an error string to surface (caller turns it into a failed
        VideoGenerationResult), or None to proceed. Fail-OPEN: if the probe
        itself errors we return None — never block a working render because the
        probe threw. Reuses the coordinator's pynvml/nvidia-smi probe (READ
        ONLY, allocates nothing).
        """
        try:
            from backend.services.gpu_resource_coordinator import get_available_vram
            info = get_available_vram()
        except Exception as e:  # noqa: BLE001 — fail open on a broken probe
            logger.warning("VRAM preflight probe errored (%s); proceeding without gate", e)
            return None

        # Probe didn't succeed. Distinguish "no NVIDIA hardware at all" (an
        # honest hard error — video gen needs a GPU) from a transient/unknown
        # probe failure (fail open — don't block a card we just can't read).
        if not info.get("success"):
            reason = info.get("reason") or info.get("error") or ""
            if reason == "no_gpu_hardware" or "no NVIDIA" in str(reason):
                return "GPU required for video generation: no NVIDIA GPU detected on this host."
            logger.warning("VRAM preflight: probe unavailable (%s); proceeding", reason)
            return None

        total_mb = info.get("total_mb") or 0
        free_mb = info.get("available_mb") or info.get("free_mb") or 0
        if total_mb <= 0:
            return None  # unknown total → fail open
        total_gb = total_mb / 1024.0
        need = self._min_vram_gb_for(model)
        # Tolerance for real "16 GB" consumer cards (common 15.5-16.0 GB reported
        # total after driver/display reservation). The quantized GGUF paths and
        # music-video's 832x480 preview res target exactly this hardware class.
        # We still hard-block true under-spec cards (e.g. 12 GB or less) and any
        # probe failure is fail-open (existing behavior).
        # Use MB math for the tolerance check to avoid float edge cases.
        need_mb = need * 1024
        if need and total_mb + 512 < need_mb:  # ~0.5 GB grace
            return (
                f"{model} needs ~{need}g GB VRAM; detected {total_gb:.2f}g GB "
                f"({total_mb} MB total). "
                "Try a lighter model or preview resolution."
            )
        # Advisory only: free VRAM after callers should already have run
        # gpu_session reclaim. Low free means another consumer is still resident;
        # we do not hard-fail (staged TE-on-CPU is the thrash fix, not refuse).
        if free_mb and free_mb < 2048:
            logger.warning(
                "VRAM preflight: only ~%s MB free of %s MB total before queue "
                "(clip_device=%s). Ensure gpu_session reclaimed Ollama/Comfy.",
                free_mb, total_mb, self._wan_clip_device(total_mb),
            )
        return None

    def _add_cogvideox_optional_nodes(
        self,
        workflow: dict,
        sampler_node_id: str,
        teacache_threshold: Optional[float] = None,
        feta_weight: Optional[float] = None,
    ) -> dict:
        """Add optional TeaCache and/or FETA nodes to a CogVideoX workflow.

        Args:
            workflow: The ComfyUI workflow dict (modified in-place).
            sampler_node_id: Node ID of the CogVideoSampler.
            teacache_threshold: If set, add TeaCache with this rel_l1_thresh (0.1-1.0).
            feta_weight: If set, add Enhance-A-Video with this weight (0.1-3.0).

        Returns:
            The modified workflow dict.
        """
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        next_id = max(existing_ids) + 1

        if teacache_threshold is not None:
            tea_id = str(next_id)
            next_id += 1
            workflow[tea_id] = {
                "class_type": "CogVideoXTeaCache",
                "inputs": {
                    "rel_l1_thresh": float(teacache_threshold),
                }
            }
            workflow[sampler_node_id]["inputs"]["teacache_args"] = [tea_id, 0]
            logger.info(f"Added TeaCache (threshold={teacache_threshold}) to CogVideoX workflow")

        if feta_weight is not None:
            feta_id = str(next_id)
            next_id += 1
            workflow[feta_id] = {
                "class_type": "CogVideoEnhanceAVideo",
                "inputs": {
                    "weight": float(feta_weight),
                    "start_percent": 0.0,
                    "end_percent": 1.0,
                }
            }
            workflow[sampler_node_id]["inputs"]["feta_args"] = [feta_id, 0]
            logger.info(f"Added Enhance-A-Video (weight={feta_weight}) to CogVideoX workflow")

        return workflow

    def _create_cogvideox_text2video_workflow(
        self,
        prompt: str,
        negative_prompt: str = "",
        model_name: str = "THUDM/CogVideoX-2b",
        num_frames: int = 49,
        num_inference_steps: int = 50,
        guidance_scale: float = 6.0,
        width: int = 720,
        height: int = 480,
        seed: Optional[int] = None,
        fps: int = 8,
        interpolation_multiplier: int = 2,
    ) -> dict:
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        workflow = {
            "1": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": "t5/google_t5-v1_1-xxl_encoderonly-fp8_e4m3fn.safetensors",
                    "type": "sd3",
                }
            },
            "2": {
                "class_type": "CogVideoTextEncode",
                "inputs": {
                    "clip": ["1", 0],
                    "prompt": prompt,
                    "strength": 1,
                    # Offload the T5 encoder off-GPU after the positive encode too
                    # (was False) so T5 isn't co-resident with the transformer+VAE
                    # — cuts the CogVideoX OOM hotspot on a 16GB card.
                    "force_offload": True,
                }
            },
            "3": {
                "class_type": "CogVideoTextEncode",
                "inputs": {
                    "clip": ["2", 1],
                    "prompt": negative_prompt,
                    "strength": 1,
                    "force_offload": True,
                }
            },
            "4": {
                "class_type": "DownloadAndLoadCogVideoModel",
                "inputs": {
                    "model": model_name,
                    "precision": "bf16",
                    "fp8_transformer": "disabled",
                    "compile": False,
                    "attention_mode": "sdpa",
                    "device": "main_device",
                }
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1,
                }
            },
            "6": {
                "class_type": "CogVideoSampler",
                "inputs": {
                    "model": ["4", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "samples": ["5", 0],
                    "num_frames": num_frames,
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "seed": seed,
                    "control_after_generate": "fixed",
                    "scheduler": "CogVideoXDDIM",
                    "denoise_strength": 1.0,
                }
            },
            "7": {
                "class_type": "CogVideoDecode",
                "inputs": {
                    "vae": ["4", 1],
                    "samples": ["6", 0],
                    "enable_vae_tiling": True,
                    "tile_sample_min_height": 240,
                    "tile_sample_min_width": 360,
                    "tile_overlap_factor_height": 0.2,
                    "tile_overlap_factor_width": 0.2,
                    "auto_tile_size": True,
                }
            },
            "8": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["7", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "cogvideo",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {
                        "hidden": False,
                        "paused": False,
                        "params": {},
                    },
                }
            },
        }

        # Add RIFE frame interpolation if multiplier > 1
        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="7",        # CogVideoDecode
                video_combine_node_id="8",  # VHS_VideoCombine
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )

        return workflow

    def _create_cogvideox_i2v_workflow(
        self,
        image_filename: str,
        prompt: str,
        negative_prompt: str = "",
        model_name: str = "kijai/CogVideoX-5b-1.5-I2V",
        num_frames: int = 49,
        num_inference_steps: int = 50,
        guidance_scale: float = 6.0,
        width: int = 720,
        height: int = 480,
        seed: Optional[int] = None,
        fps: int = 8,
        interpolation_multiplier: int = 2,
    ) -> dict:
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        workflow = {
            "1": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": "t5/google_t5-v1_1-xxl_encoderonly-fp8_e4m3fn.safetensors",
                    "type": "sd3",
                }
            },
            "2": {
                "class_type": "CogVideoTextEncode",
                "inputs": {
                    "clip": ["1", 0],
                    "prompt": prompt,
                    "strength": 1,
                    # Offload the T5 encoder off-GPU after the positive encode too
                    # (was False) so T5 isn't co-resident with the transformer+VAE
                    # — cuts the CogVideoX OOM hotspot on a 16GB card.
                    "force_offload": True,
                }
            },
            "3": {
                "class_type": "CogVideoTextEncode",
                "inputs": {
                    "clip": ["2", 1],
                    "prompt": negative_prompt,
                    "strength": 1,
                    "force_offload": True,
                }
            },
            "4": {
                "class_type": "DownloadAndLoadCogVideoModel",
                "inputs": {
                    "model": model_name,
                    "precision": "bf16",
                    "fp8_transformer": "disabled",
                    "compile": False,
                    "attention_mode": "sdpa",
                    "device": "main_device",
                }
            },
            "5": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": image_filename,
                }
            },
            "10": {
                "class_type": "ImageResizeKJ",
                "inputs": {
                    # KJNodes ImageResizeKJ schema drifted: fields used to be
                    # width_input/height_input/interpolation; now they're
                    # width/height/upscale_method (with upscale_method as a
                    # required enum). divisible_by stays required as well.
                    "image": ["5", 0],
                    "width": width,
                    "height": height,
                    "upscale_method": "lanczos",
                    "keep_proportion": False,
                    "divisible_by": 16,
                }
            },
            "9": {
                "class_type": "CogVideoImageEncode",
                "inputs": {
                    "vae": ["4", 1],
                    "start_image": ["10", 0],
                }
            },
            "6": {
                "class_type": "CogVideoSampler",
                "inputs": {
                    "model": ["4", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "image_cond_latents": ["9", 0],
                    "num_frames": num_frames,
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "seed": seed,
                    "control_after_generate": "fixed",
                    "scheduler": "CogVideoXDDIM",
                    "denoise_strength": 1.0,
                }
            },
            "7": {
                "class_type": "CogVideoDecode",
                "inputs": {
                    "vae": ["4", 1],
                    "samples": ["6", 0],
                    "enable_vae_tiling": True,
                    "tile_sample_min_height": 240,
                    "tile_sample_min_width": 360,
                    "tile_overlap_factor_height": 0.2,
                    "tile_overlap_factor_width": 0.2,
                    "auto_tile_size": True,
                }
            },
            "8": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["7", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "cogvideo_i2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {
                        "hidden": False,
                        "paused": False,
                        "params": {},
                    },
                }
            },
        }

        # Add RIFE frame interpolation if multiplier > 1
        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="7",        # CogVideoDecode
                video_combine_node_id="8",  # VHS_VideoCombine
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )

        return workflow

    def _create_wan22_t2v_workflow(
        self,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "wan22-14b",
        num_frames: int = 81,
        num_inference_steps: int = 20,
        guidance_scale: float = 3.5,
        width: int = 640,
        height: int = 640,
        seed: Optional[int] = None,
        fps: int = 16,
        interpolation_multiplier: int = 2,
    ) -> dict:
        """Build a ComfyUI API-format workflow for Wan 2.2 MoE text-to-video.

        Uses two-pass architecture: HighNoise expert for layout/motion,
        LowNoise expert for detail refinement. GGUF models loaded via
        ComfyUI-GGUF custom node (UnetLoaderGGUF).
        """
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        model_files = self.WAN22_MODELS.get(model_key, self.WAN22_MODELS["wan22-14b"])
        clip_device = self._wan_clip_device()

        # Default negative prompt for anatomy quality
        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
                "bad proportions, extra limbs, missing limbs, mutated hands, fused fingers, "
                "extra fingers, deformed face, asymmetrical eyes, weird body, static, "
                "overexposed, "
            )

        midpoint = num_inference_steps // 2
        logger.info(
            "Wan T2V MoE workflow clip_device=%s (TE off-GPU frees UNet residency on 16GB)",
            clip_device,
        )

        shift = self._wan_dynamic_shift(width, height)

        workflow = {
            # ── Model Loading ──────────────────────────────────────────────
            # Node 1: Load HighNoise GGUF expert
            "1": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {
                    "unet_name": model_files["unet_high"],
                }
            },
            # Node 2: Load LowNoise GGUF expert
            "2": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {
                    "unet_name": model_files["unet_low"],
                }
            },
            # Node 3: Load UMT5 text encoder (Wan clip type).
            # device=cpu on ≤20GB cards: same weights, TE never occupies GPU VRAM
            # so the ~10GB UNet is not forced into CPU offload thrash.
            "3": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": model_files["clip"],
                    "type": "wan",
                    "device": clip_device,
                }
            },
            # Node 4: Load Wan VAE
            "4": {
                "class_type": "VAELoader",
                "inputs": {
                    "vae_name": model_files["vae"],
                }
            },

            # ── Text Encoding ──────────────────────────────────────────────
            # Node 5: Positive prompt
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["3", 0],
                    "text": prompt,
                }
            },
            # Node 6: Negative prompt
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["3", 0],
                    "text": negative_prompt,
                }
            },

            # ── Latent ─────────────────────────────────────────────────────
            # Node 7: Empty video latent
            "7": {
                "class_type": "EmptyHunyuanLatentVideo",
                "inputs": {
                    "width": width,
                    "height": height,
                    "length": num_frames,
                    "batch_size": 1,
                }
            },

            # ── Noise Scheduling ───────────────────────────────────────────
            # Node 8: ModelSamplingSD3 for HighNoise expert
            "8": {
                "class_type": "ModelSamplingSD3",
                "inputs": {
                    "model": ["1", 0],
                    "shift": shift,
                }
            },
            # Node 9: ModelSamplingSD3 for LowNoise expert
            "9": {
                "class_type": "ModelSamplingSD3",
                "inputs": {
                    "model": ["2", 0],
                    "shift": shift,
                }
            },

            # ── Two-Pass Sampling (MoE) ────────────────────────────────────
            # Steps are SPLIT at midpoint: HighNoise does steps 0→mid,
            # LowNoise continues from mid→end. Total steps = num_inference_steps.

            # Node 10: Pass 1 — HighNoise expert (layout + motion, first half)
            "10": {
                "class_type": "KSamplerAdvanced",
                "inputs": {
                    "model": ["8", 0],
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "latent_image": ["7", 0],
                    "add_noise": "enable",
                    "noise_seed": seed,
                    "control_after_generate": "randomize",
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "start_at_step": 0,
                    "end_at_step": midpoint,
                    "return_with_leftover_noise": "enable",
                }
            },
            # Node 11: Pass 2 — LowNoise expert (detail refinement, second half)
            "11": {
                "class_type": "KSamplerAdvanced",
                "inputs": {
                    "model": ["9", 0],
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "latent_image": ["10", 0],
                    "add_noise": "disable",
                    "noise_seed": 0,
                    "control_after_generate": "fixed",
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "start_at_step": midpoint,
                    "end_at_step": 10000,
                    "return_with_leftover_noise": "disable",
                }
            },

            # ── Decode + Output ────────────────────────────────────────────
            # Node 12: VAE Decode — tiled for HD+ so your GPU doesn't rage-quit
            "12": self._build_vae_decode_node("11", "4", width, height),
            # Node 13: Create video from frames
            "13": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["12", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "wan22_t2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {
                        "hidden": False,
                        "paused": False,
                        "params": {},
                    },
                }
            },
        }

        # Add RIFE frame interpolation if multiplier > 1
        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="12",       # VAEDecode
                video_combine_node_id="13", # VHS_VideoCombine
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )

        return workflow

    def _create_wan22_i2v_workflow(
        self,
        image_filename: str,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "wan22-14b-i2v",
        num_frames: int = 81,
        num_inference_steps: int = 20,
        guidance_scale: float = 3.5,
        width: int = 832,
        height: int = 480,
        seed: Optional[int] = None,
        fps: int = 16,
        interpolation_multiplier: int = 2,
    ) -> dict:
        # Same MoE two-pass dance as Wan T2V, but the empty latent gets swapped
        # for WanImageToVideo — that node bakes the start frame into the
        # conditioning and hands back a properly-shaped image-conditioned latent.
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        model_files = self.WAN22_MODELS.get(model_key, self.WAN22_MODELS["wan22-14b-i2v"])
        clip_device = self._wan_clip_device()

        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
                "bad proportions, extra limbs, missing limbs, mutated hands, fused fingers, "
                "extra fingers, deformed face, asymmetrical eyes, weird body, static, "
                "overexposed"
            )

        midpoint = num_inference_steps // 2
        shift = self._wan_dynamic_shift(width, height)
        logger.info(
            "Wan I2V MoE workflow clip_device=%s, dynamic_shift=%.1f (TE off-GPU frees UNet residency on 16GB)",
            clip_device,
            shift,
        )

        workflow = {
            "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": model_files["unet_high"]}},
            "2": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": model_files["unet_low"]}},
            "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": model_files["clip"], "type": "wan", "device": clip_device}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": model_files["vae"]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": negative_prompt}},
            "14": {"class_type": "LoadImage", "inputs": {"image": image_filename}},
            # WanImageToVideo: takes pos/neg cond + start image + vae →
            # returns image-conditioned pos/neg + a length-N latent.
            "7": {
                "class_type": "WanImageToVideo",
                "inputs": {
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "vae": ["4", 0],
                    "width": width,
                    "height": height,
                    "length": num_frames,
                    "batch_size": 1,
                    "start_image": ["14", 0],
                },
            },
            "8": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": shift}},
            "9": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": shift}},
            "10": {
                "class_type": "KSamplerAdvanced",
                "inputs": {
                    "model": ["8", 0],
                    "positive": ["7", 0],
                    "negative": ["7", 1],
                    "latent_image": ["7", 2],
                    "add_noise": "enable",
                    "noise_seed": seed,
                    "control_after_generate": "randomize",
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "start_at_step": 0,
                    "end_at_step": midpoint,
                    "return_with_leftover_noise": "enable",
                },
            },
            "11": {
                "class_type": "KSamplerAdvanced",
                "inputs": {
                    "model": ["9", 0],
                    "positive": ["7", 0],
                    "negative": ["7", 1],
                    "latent_image": ["10", 0],
                    "add_noise": "disable",
                    "noise_seed": 0,
                    "control_after_generate": "fixed",
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "start_at_step": midpoint,
                    "end_at_step": 10000,
                    "return_with_leftover_noise": "disable",
                },
            },
            "12": self._build_vae_decode_node("11", "4", width, height),
            "13": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["12", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "wan22_i2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="12",
                video_combine_node_id="13",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )

        return workflow

    def _create_wan22_5b_workflow(
        self,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "wan22-5b",
        image_filename: Optional[str] = None,
        num_frames: int = 121,
        num_inference_steps: int = 20,
        guidance_scale: float = 5.0,
        width: int = 1280,
        height: int = 704,
        seed: Optional[int] = None,
        fps: int = 24,
        interpolation_multiplier: int = 1,
    ) -> dict:
        """Wan 2.2 TI2V-5B — single-model text+image-to-video that FITS 16GB (no MoE
        two-pass, no CPU offload → none of the 38-min-per-clip A14B pain). Graph mirrors
        ComfyUI's bundled `video_wan2_2_5B_ti2v` template: UNETLoader + CLIPLoader(wan) +
        VAELoader(wan2.2 VAE) → CLIPTextEncode ×2 → Wan22ImageToVideoLatent (start_image
        optional) → ModelSamplingSD3(dynamic shift) → single KSampler (euler/simple, denoise 1)
        → decode. image_filename set → image-to-video; else text-to-video.
        """
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        cfg = self.WAN22_MODELS.get(model_key, {})
        unet = cfg.get("unet") or "wan2.2_ti2v_5B_fp16.safetensors"
        clip = cfg.get("clip") or "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
        vae = cfg.get("vae") or "wan2.2_vae.safetensors"
        clip_device = self._wan_clip_device()

        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
                "bad proportions, extra limbs, missing limbs, mutated hands, fused fingers, "
                "extra fingers, deformed face, asymmetrical eyes, weird body, static, "
                "overexposed"
            )

        # Wan22ImageToVideoLatent: vae + dims (+ optional start_image) → conditioned LATENT.
        latent_inputs = {
            "vae": ["4", 0],
            "width": width,
            "height": height,
            "length": num_frames,
            "batch_size": 1,
        }
        
        shift = self._wan_dynamic_shift(width, height)

        logger.info("Wan TI2V-5B workflow clip_device=%s, dynamic_shift=%.1f", clip_device, shift)

        workflow = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "wan", "device": clip_device}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": negative_prompt}},
            "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": latent_inputs},
            "8": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": shift}},
            # Single sampling pass — the whole point (no high/low expert swap).
            "10": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["8", 0],
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "latent_image": ["7", 0],
                    "seed": seed,
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": 1.0,
                },
            },
            "12": self._build_vae_decode_node("10", "4", width, height),
            "13": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["12", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "wan22_5b",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

        # Image-to-video: bake the start frame into the latent node.
        if image_filename:
            workflow["14"] = {"class_type": "LoadImage", "inputs": {"image": image_filename}}
            workflow["7"]["inputs"]["start_image"] = ["14", 0]

        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="12",
                video_combine_node_id="13",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )

        return workflow

    def _create_ltx23_t2v_workflow(
        self,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "ltx23-distilled-fp8",
        num_frames: int = 65,
        num_inference_steps: int = 8,
        guidance_scale: float = 1.0,
        width: int = 768,
        height: int = 512,
        seed: Optional[int] = None,
        fps: float = 16.0,
        interpolation_multiplier: int = 1,
    ) -> dict:
        """LTX-2.3 distilled T2V — AV-aware core ComfyUI graph.

        LTX-2.3 is an audio-video model: sampling on a video-only latent crashes
        in rotary PE with T=0 audio (`reshape [2, 0, 32, -1]`). We always concat
        an empty audio latent, sample the AV pair, then separate and decode video.

        Graph: UNETLoader + DualCLIPLoader(ltxv) + video VAE + audio VAE →
        CLIPTextEncode ×2 → EmptyLTXVLatentVideo + LTXVEmptyLatentAudio →
        LTXVConcatAVLatent → LTXVConditioning → ModelSamplingLTXV → KSampler →
        LTXVSeparateAVLatent → VAEDecode → VHS_VideoCombine.
        """
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        self._ensure_ltx_models()
        cfg = self.LTX_MODELS.get(model_key, {})
        unet = cfg.get("unet") or "ltx-2.3-22b-distilled-1.1_transformer_only_fp8_scaled.safetensors"
        clip = cfg.get("clip") or "gemma_3_12B_it_fp4_mixed.safetensors"
        text_proj = cfg.get("text_projection") or "ltx-2.3_text_projection_bf16.safetensors"
        vae = cfg.get("vae") or "LTX23_video_vae_bf16.safetensors"
        audio_vae = cfg.get("audio_vae") or "LTX23_audio_vae_bf16.safetensors"
        length = self._ltx_frame_count(num_frames)

        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
                "bad proportions, extra limbs, static, jitter, morphing, watermark"
            )

        clip_device = self._wan_clip_device()

        workflow = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": unet, "weight_dtype": "default"},
            },
            "2": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": clip,
                    "clip_name2": text_proj,
                    "type": "ltxv",
                    "device": clip_device,
                },
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "4": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": audio_vae}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative_prompt}},
            "7": {
                "class_type": "EmptyLTXVLatentVideo",
                "inputs": {
                    "width": width,
                    "height": height,
                    "length": length,
                    "batch_size": 1,
                },
            },
            "8": {
                "class_type": "LTXVEmptyLatentAudio",
                "inputs": {
                    "frames_number": length,
                    "frame_rate": float(fps),
                    "batch_size": 1,
                    "audio_vae": ["4", 0],
                },
            },
            "9": {
                "class_type": "LTXVConcatAVLatent",
                "inputs": {"video_latent": ["7", 0], "audio_latent": ["8", 0]},
            },
            "10": {
                "class_type": "LTXVConditioning",
                "inputs": {
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "frame_rate": float(fps),
                },
            },
            "11": {
                "class_type": "ModelSamplingLTXV",
                "inputs": {
                    "model": ["1", 0],
                    "max_shift": 2.05,
                    "base_shift": 0.95,
                    "latent": ["9", 0],
                },
            },
            "12": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["11", 0],
                    "positive": ["10", 0],
                    "negative": ["10", 1],
                    "latent_image": ["9", 0],
                    "seed": seed,
                    "steps": max(1, int(num_inference_steps or 8)),
                    "cfg": float(guidance_scale if guidance_scale is not None else 1.0),
                    "sampler_name": "euler_ancestral_cfg_pp",
                    "scheduler": "simple",
                    "denoise": 1.0,
                },
            },
            "13": {
                "class_type": "LTXVSeparateAVLatent",
                "inputs": {"av_latent": ["12", 0]},
            },
            "14": self._build_vae_decode_node("13", "3", width, height),
            "15": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["14", 0],
                    "frame_rate": float(fps),
                    "loop_count": 0,
                    "filename_prefix": "ltx23_t2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="14",
                video_combine_node_id="15",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )
        return workflow

    def _create_ltx23_i2v_workflow(
        self,
        image_filename: str,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "ltx23-distilled-fp8",
        num_frames: int = 65,
        num_inference_steps: int = 8,
        guidance_scale: float = 1.0,
        width: int = 768,
        height: int = 512,
        seed: Optional[int] = None,
        fps: float = 16.0,
        interpolation_multiplier: int = 1,
        strength: float = 1.0,
    ) -> dict:
        """LTX-2.3 distilled I2V — AV concat path with LTXVImgToVideo start frame."""
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        self._ensure_ltx_models()
        cfg = self.LTX_MODELS.get(model_key, {})
        unet = cfg.get("unet") or "ltx-2.3-22b-distilled-1.1_transformer_only_fp8_scaled.safetensors"
        clip = cfg.get("clip") or "gemma_3_12B_it_fp4_mixed.safetensors"
        text_proj = cfg.get("text_projection") or "ltx-2.3_text_projection_bf16.safetensors"
        vae = cfg.get("vae") or "LTX23_video_vae_bf16.safetensors"
        audio_vae = cfg.get("audio_vae") or "LTX23_audio_vae_bf16.safetensors"
        length = self._ltx_frame_count(num_frames)
        clip_device = self._wan_clip_device()

        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
                "bad proportions, extra limbs, static, jitter, morphing, watermark"
            )

        workflow = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": unet, "weight_dtype": "default"},
            },
            "2": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": clip,
                    "clip_name2": text_proj,
                    "type": "ltxv",
                    "device": clip_device,
                },
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "4": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": audio_vae}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative_prompt}},
            "7": {"class_type": "LoadImage", "inputs": {"image": image_filename}},
            "8": {
                "class_type": "LTXVPreprocess",
                "inputs": {"image": ["7", 0], "img_compression": 18},
            },
            "9": {
                "class_type": "LTXVImgToVideo",
                "inputs": {
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "vae": ["3", 0],
                    "image": ["8", 0],
                    "width": width,
                    "height": height,
                    "length": length,
                    "batch_size": 1,
                    "strength": float(strength),
                },
            },
            "10": {
                "class_type": "LTXVEmptyLatentAudio",
                "inputs": {
                    "frames_number": length,
                    "frame_rate": float(fps),
                    "batch_size": 1,
                    "audio_vae": ["4", 0],
                },
            },
            "11": {
                "class_type": "LTXVConcatAVLatent",
                "inputs": {"video_latent": ["9", 2], "audio_latent": ["10", 0]},
            },
            "12": {
                "class_type": "LTXVConditioning",
                "inputs": {
                    "positive": ["9", 0],
                    "negative": ["9", 1],
                    "frame_rate": float(fps),
                },
            },
            "13": {
                "class_type": "ModelSamplingLTXV",
                "inputs": {
                    "model": ["1", 0],
                    "max_shift": 2.05,
                    "base_shift": 0.95,
                    "latent": ["11", 0],
                },
            },
            "14": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["13", 0],
                    "positive": ["12", 0],
                    "negative": ["12", 1],
                    "latent_image": ["11", 0],
                    "seed": seed,
                    "steps": max(1, int(num_inference_steps or 8)),
                    "cfg": float(guidance_scale if guidance_scale is not None else 1.0),
                    "sampler_name": "euler_ancestral_cfg_pp",
                    "scheduler": "simple",
                    "denoise": 1.0,
                },
            },
            "15": {
                "class_type": "LTXVSeparateAVLatent",
                "inputs": {"av_latent": ["14", 0]},
            },
            "16": self._build_vae_decode_node("15", "3", width, height),
            "17": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["16", 0],
                    "frame_rate": float(fps),
                    "loop_count": 0,
                    "filename_prefix": "ltx23_i2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="16",
                video_combine_node_id="17",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )
        return workflow

    def _build_vae_decode_node(self, samples_node: str, vae_node: str, width: int, height: int) -> dict:
        """Pick the right VAE decode strategy based on resolution.

        Standard VAEDecode works fine for tiny tests. Above that, tiled decoding
        saves your VRAM from a very bad day. Lowered threshold to 720 for video.
        """
        use_tiled = width >= 720 or height >= 720
        if use_tiled:
            return {
                "class_type": "VAEDecodeTiled",
                "inputs": {
                    "samples": [samples_node, 0],
                    "vae": [vae_node, 0],
                    "tile_size": 480,
                    "overlap": 64,
                    "temporal_size": 64,
                    "temporal_overlap": 8,
                }
            }
        return {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": [samples_node, 0],
                "vae": [vae_node, 0],
            }
        }

    def _add_rife_interpolation(
        self,
        workflow: dict,
        source_node_id: str,
        video_combine_node_id: str,
        base_fps: int,
        multiplier: int = 2,
    ) -> dict:
        """Insert a RIFE VFI interpolation node between a frame source and VHS_VideoCombine.

        Args:
            workflow: The ComfyUI workflow dict (modified in-place).
            source_node_id: Node ID that outputs IMAGE frames (e.g. VAEDecode).
            video_combine_node_id: Node ID of VHS_VideoCombine to rewire.
            base_fps: The original frame rate before interpolation.
            multiplier: Frame multiplier (2 = double FPS, 4 = quad FPS).

        Returns:
            The modified workflow dict.
        """
        # Pick the next available node ID
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        rife_node_id = str(max(existing_ids) + 1)

        # Insert RIFE VFI node
        workflow[rife_node_id] = {
            "class_type": "RIFE VFI",
            "inputs": {
                "frames": [source_node_id, 0],
                "ckpt_name": "rife49.pth",
                "clear_cache_after_n_frames": 10,
                "multiplier": multiplier,
                "fast_mode": True,
                "ensemble": True,
                "scale_factor": 1.0,
                "dtype": "float32",
                "torch_compile": False,
                "batch_size": 1,
            }
        }

        # Rewire VHS_VideoCombine to take frames from RIFE instead of source
        workflow[video_combine_node_id]["inputs"]["images"] = [rife_node_id, 0]
        workflow[video_combine_node_id]["inputs"]["frame_rate"] = base_fps * multiplier

        logger.info(
            f"Added RIFE interpolation (x{multiplier}): "
            f"node {source_node_id} -> RIFE({rife_node_id}) -> VHS_VideoCombine({video_combine_node_id}), "
            f"FPS {base_fps} -> {base_fps * multiplier}"
        )

        return workflow

    def _add_upscale_node(
        self,
        workflow: dict,
        source_node_id: str,
        video_combine_node_id: str,
    ) -> dict:
        """Insert Real-ESRGAN 2x upscale between a frame source and VHS_VideoCombine.

        Args:
            workflow: The ComfyUI workflow dict (modified in-place).
            source_node_id: Node ID that outputs IMAGE frames (e.g. RIFE or VAEDecode).
            video_combine_node_id: Node ID of VHS_VideoCombine to rewire.

        Returns:
            The modified workflow dict.
        """
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        loader_id = str(max(existing_ids) + 1)
        upscale_id = str(max(existing_ids) + 2)

        # Load the upscale model
        workflow[loader_id] = {
            "class_type": "UpscaleModelLoader",
            "inputs": {
                "model_name": "RealESRGAN_x2.pth",
            }
        }

        # Apply upscaling to frames
        workflow[upscale_id] = {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {
                "upscale_model": [loader_id, 0],
                "image": [source_node_id, 0],
            }
        }

        # Rewire VHS_VideoCombine to take frames from upscaler
        workflow[video_combine_node_id]["inputs"]["images"] = [upscale_id, 0]

        logger.info(
            f"Added Real-ESRGAN 2x upscale: "
            f"node {source_node_id} -> Upscale({upscale_id}) -> VHS_VideoCombine({video_combine_node_id})"
        )

        return workflow

    def _add_freeu_node(self, workflow: dict, model_node_id: str, is_cogvideo: bool = False) -> str:
        """Insert FreeU_V2 node to improve generation quality.
        Returns the ID of the new FreeU node.
        """
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        freeu_id = str(max(existing_ids) + 1)
        
        # FreeU V2 defaults (tuned for video models generally)
        b1, b2, s1, s2 = 1.01, 1.02, 0.99, 0.95
        if is_cogvideo:
            b1, b2, s1, s2 = 1.1, 1.2, 0.9, 0.2

        workflow[freeu_id] = {
            "class_type": "FreeU_V2",
            "inputs": {
                "model": [model_node_id, 0],
                "b1": b1,
                "b2": b2,
                "s1": s1,
                "s2": s2,
            }
        }
        logger.info(f"Added FreeU_V2 node ({freeu_id}) after model node {model_node_id}")
        return freeu_id

    def _add_lora_loader(self, workflow: dict, model_node_id: str, clip_node_id: str, lora_name: str, strength: float = 1.0) -> tuple[str, str]:
        """Insert a LoraLoader node.
        Returns the new (model_node_id, clip_node_id) to use in downstream nodes.
        """
        if not lora_name:
            return model_node_id, clip_node_id
            
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        lora_id = str(max(existing_ids) + 1)
        
        workflow[lora_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": [model_node_id, 0],
                "clip": [clip_node_id, 0],
                "lora_name": lora_name,
                "strength_model": strength,
                "strength_clip": strength,
            }
        }
        logger.info(f"Added LoraLoader node ({lora_id}) for {lora_name} (strength: {strength})")
        return lora_id, lora_id

    def _add_face_detailer_node(self, workflow: dict, source_node_id: str, video_combine_node_id: str) -> dict:
        """Insert FaceRestoreWithModel node for human realism before VHS_VideoCombine.
        """
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        restore_loader_id = str(max(existing_ids) + 1)
        restore_node_id = str(max(existing_ids) + 2)
        
        workflow[restore_loader_id] = {
            "class_type": "FaceRestoreModelLoader",
            "inputs": {
                "model_name": "codeformer.pth"
            }
        }
        
        workflow[restore_node_id] = {
            "class_type": "FaceRestoreCFWithModel",
            "inputs": {
                "facerestore_model": [restore_loader_id, 0],
                "image": [source_node_id, 0],
                "facedetection": "retinaface_resnet50",
                "codeformer_fidelity": 0.5,
            },
        }

        # Rewire VHS_VideoCombine to take frames from FaceRestore
        workflow[video_combine_node_id]["inputs"]["images"] = [restore_node_id, 0]

        logger.info(f"Added FaceRestoreCFWithModel ({restore_node_id}) after node {source_node_id}")
        return workflow

    def interrupt(self) -> bool:
        """Force-stop whatever ComfyUI is currently sampling.

        Yells "ABORT!" at the kitchen — ComfyUI bails on the current sampler,
        history gets a partial entry, and our wait loop returns.
        """
        try:
            requests.post(f"{self.comfy_url}/interrupt", timeout=5)
            try:
                requests.post(
                    f"{self.comfy_url}/queue",
                    json={"clear": True},
                    timeout=5,
                )
            except Exception as clear_err:
                logger.debug(f"Queue clear failed (non-fatal): {clear_err}")
            logger.info("Sent interrupt + queue-clear to ComfyUI")
            return True
        except Exception as e:
            logger.warning(f"Failed to interrupt ComfyUI: {e}")
            return False

    def _queue_prompt(self, workflow: dict, client_id: Optional[str] = None) -> Optional[str]:
        try:
            payload = {"prompt": workflow}
            # client_id scopes ComfyUI's /ws progress messages back to us so the
            # progress bridge can hear this generation. (server.py:883)
            if client_id:
                payload["client_id"] = client_id
            response = requests.post(
                f"{self.comfy_url}/prompt",
                json=payload,
                timeout=10
            )
            response.raise_for_status()

            result = response.json()
            prompt_id = result.get("prompt_id")
            logger.info(f"Queued workflow in ComfyUI: {prompt_id}")
            return prompt_id

        except requests.HTTPError as e:
            detail = ""
            try:
                if e.response is not None:
                    body = e.response.json()
                    if isinstance(body, dict):
                        err = body.get("error", body)
                        if isinstance(err, dict):
                            detail = err.get("message") or str(err)
                        else:
                            detail = str(err)
            except Exception:
                if e.response is not None:
                    detail = (e.response.text or "")[:500]
            logger.error(
                "Failed to queue workflow in ComfyUI: %s%s",
                e,
                f" — {detail}" if detail else "",
            )
            return None
        except Exception as e:
            logger.error(f"Failed to queue workflow in ComfyUI: {e}")
            return None

    def _comfyui_alive(self) -> bool:
        """Quick liveness probe — distinct from service_available cache."""
        return self._check_comfyui_connection()

    def _prompt_in_queue(self, prompt_id: str) -> Optional[bool]:
        """Return True/False if prompt is running or pending; None if queue probe failed."""
        try:
            response = requests.get(f"{self.comfy_url}/queue", timeout=5)
            response.raise_for_status()
            data = response.json()
            ids: set[str] = set()
            for key in ("queue_running", "queue_pending"):
                for entry in data.get(key) or []:
                    if isinstance(entry, (list, tuple)) and len(entry) > 1:
                        ids.add(str(entry[1]))
                    elif isinstance(entry, dict) and entry.get("prompt_id"):
                        ids.add(str(entry["prompt_id"]))
            return prompt_id in ids
        except Exception as e:
            logger.debug(f"ComfyUI queue probe failed: {e}")
            return None

    @staticmethod
    def _history_execution_error(entry: dict) -> Optional[str]:
        """Extract a human error from a ComfyUI history entry, if any."""
        status = entry.get("status") or {}
        if status.get("completed") is True and status.get("status_str") != "error":
            return None
        messages = status.get("messages") or []
        for msg in messages:
            if not isinstance(msg, (list, tuple)) or len(msg) < 2:
                continue
            tag, payload = msg[0], msg[1]
            if tag in ("execution_error", "execution_interrupted"):
                if isinstance(payload, dict):
                    text = payload.get("exception_message") or payload.get("message") or str(payload)
                else:
                    text = str(payload)
                return f"ComfyUI {tag}: {text}"
        if status.get("status_str") == "error":
            return "ComfyUI reported execution error"
        return None

    def _wait_for_completion(self, prompt_id: str, timeout: int = 600) -> Optional[dict]:
        start_time = time.time()
        last_log_time = start_time
        poll_count = 0
        orphan_grace_s = 30  # allow queue/history to populate after POST /prompt
        # The liveness probe (GET / with a 2s timeout) can miss while ComfyUI is
        # pegged on the final VHS/ffmpeg encode — one transient miss must not
        # abandon a render (a real 331s clip was orphaned 2s before completion).
        # Only sustained silence (~20s+) means the server is actually gone.
        consecutive_dead = 0
        dead_probe_limit = 5

        while time.time() - start_time < timeout:
            poll_count += 1

            if not self._comfyui_alive():
                consecutive_dead += 1
                if consecutive_dead >= dead_probe_limit:
                    logger.error(
                        "ComfyUI unreachable (%d consecutive probes) while waiting for %s — prompt orphaned",
                        consecutive_dead,
                        prompt_id,
                    )
                    return None
                logger.warning(
                    "ComfyUI liveness probe missed (%d/%d) while waiting for %s — retrying",
                    consecutive_dead,
                    dead_probe_limit,
                    prompt_id,
                )
                time.sleep(2)
                continue
            consecutive_dead = 0

            try:
                response = requests.get(
                    f"{self.comfy_url}/history/{prompt_id}",
                    timeout=5,
                )
                response.raise_for_status()
                history = response.json()

                if prompt_id in history:
                    entry = history[prompt_id]
                    exec_err = self._history_execution_error(entry)
                    if exec_err:
                        logger.error("Generation failed for %s: %s", prompt_id, exec_err)
                        return None
                    outputs = entry.get("outputs") or {}
                    if outputs:
                        logger.info(f"Generation complete: {prompt_id}")
                        return outputs

                elapsed = time.time() - start_time
                if elapsed > orphan_grace_s:
                    in_queue = self._prompt_in_queue(prompt_id)
                    if in_queue is False and prompt_id not in history:
                        logger.error(
                            "ComfyUI lost prompt %s (not in queue or history) — "
                            "likely restarted mid-render",
                            prompt_id,
                        )
                        return None

                current_time = time.time()
                if current_time - last_log_time > 10:
                    logger.info(
                        "Waiting for generation... (%ds elapsed, prompt_id=%s)",
                        int(elapsed),
                        prompt_id,
                    )
                    last_log_time = current_time

            except Exception as e:
                logger.warning(f"Error checking generation status: {e}")

            time.sleep(2)

        logger.error(f"Generation timed out after {timeout}s")
        return None

    def _download_result(self, outputs: dict, destination_dir: Path) -> List[str]:
        downloaded_files = []

        try:
            for node_id, node_output in outputs.items():
                if 'gifs' in node_output:
                    for item in node_output['gifs']:
                        filename = item.get('filename')
                        if filename:
                            downloaded_files.extend(
                                self._download_file(filename, destination_dir, file_type='output', subfolder=item.get('subfolder', ''))
                            )

                if 'images' in node_output:
                    for item in node_output['images']:
                        filename = item.get('filename')
                        if filename:
                            downloaded_files.extend(
                                self._download_file(filename, destination_dir, file_type='output', subfolder=item.get('subfolder', ''))
                            )

            logger.info(f"Downloaded {len(downloaded_files)} files from ComfyUI")
            return downloaded_files

        except Exception as e:
            logger.error(f"Failed to download results from ComfyUI: {e}")
            return []

    def _download_file(self, filename: str, destination_dir: Path, file_type: str = 'output', subfolder: str = '') -> List[str]:
        try:
            params = {"filename": filename, "type": file_type}
            if subfolder:
                params["subfolder"] = subfolder

            query = urllib.parse.urlencode(params)
            url = f"{self.comfy_url}/view?{query}"

            destination_path = destination_dir / filename
            destination_path.parent.mkdir(parents=True, exist_ok=True)

            logger.info(f"Downloading from ComfyUI: {url}")
            urllib.request.urlretrieve(url, destination_path)

            return [str(destination_path)]

        except Exception as e:
            logger.error(f"Failed to download file {filename}: {e}")
            return []

    def _extract_thumbnail(self, video_path: Path, thumbnail_path: Path) -> bool:
        """Extract the first frame from a video as a JPEG thumbnail using ffmpeg."""
        try:
            result = subprocess.run(
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
            logger.warning(f"ffmpeg ran but thumbnail not created (rc={result.returncode})")
            return False
        except FileNotFoundError:
            logger.warning("ffmpeg not found on system, cannot extract thumbnail")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg thumbnail extraction timed out")
            return False
        except Exception as e:
            logger.warning(f"Failed to extract thumbnail: {e}")
            return False

    def _add_frame_export(self, workflow: dict, item_id) -> bool:
        """Q3: tee the FINAL frames (the exact images feeding VHS_VideoCombine — after
        any upscale / face-restore / RIFE interpolation) into a lossless PNG sequence
        ALONGSIDE the MP4. Additive: never disturbs the video path; if the frame source
        can't be found we just skip. The PNGs let the user stitch in their own editor
        without the h264/yuv420p loss the MP4 carries."""
        try:
            vhs_node_id = next(
                (nid for nid, node in workflow.items() if node.get("class_type") == "VHS_VideoCombine"),
                None,
            )
            if not vhs_node_id:
                return False
            source_ref = workflow[vhs_node_id]["inputs"].get("images", [None])[0]
            if not source_ref:
                return False
            import re as _re
            slug = _re.sub(r"[^A-Za-z0-9_-]", "_", str(item_id or "clip"))
            existing_ids = [int(k) for k in workflow.keys() if str(k).isdigit()]
            nid = (max(existing_ids) + 1) if existing_ids else 9000
            while str(nid) in workflow:
                nid += 1
            workflow[str(nid)] = {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": f"frames/{slug}_frame",
                    "images": [source_ref, 0],
                },
            }
            logger.info("Frame export enabled: SaveImage(%s) tapped from frame source %s", nid, source_ref)
            return True
        except Exception as e:
            logger.warning("Frame export node not added (non-fatal): %s", e)
            return False

    def generate_video(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Re-check live connection if the cached flag says unavailable.
        # ComfyUI may have been started on-demand by the router since init.
        if not self.service_available:
            self.service_available = self._check_comfyui_connection()
        if not self.service_available:
            return VideoGenerationResult(
                success=False,
                error="ComfyUI service not available. Please start ComfyUI at http://127.0.0.1:8188",
                prompt_used=request.prompt,
            )

        # Refresh node registry — ComfyUI loads custom nodes only at startup.
        self._object_info_cache = None
        if not self.comfy_node_available("VHS_VideoCombine"):
            return VideoGenerationResult(
                success=False,
                error=(
                    "ComfyUI is missing the VHS_VideoCombine node (Video Helper Suite). "
                    "Most often the ComfyUI-VideoHelperSuite custom node is not installed at all "
                    "(fresh installs before 2026-08 never received custom nodes). "
                    "Fix: run plugins/comfyui/scripts/install_deps.sh — it installs every required "
                    "node from plugins/comfyui/custom_nodes.manifest — then restart ComfyUI. "
                    "If custom_nodes/ComfyUI-VideoHelperSuite/ IS present, its import failed "
                    "(commonly missing cv2: backend/venv/bin/pip install opencv-python); "
                    "check logs/comfyui.log for the import error, then restart ComfyUI."
                ),
                prompt_used=request.prompt,
            )

        # ── Prompt enhancement ───────────────────────────────────────
        # Settings → Verbatim Prompts (or VERBATIM_PROMPTS=1) means exact user text.
        _verbatim_video = False
        if request.enhance_prompt and request.prompt:
            try:
                from backend.services.media_director import verbatim_prompts_enabled
                _verbatim_video = bool(verbatim_prompts_enabled())
            except Exception:
                _verbatim_video = False
        if request.enhance_prompt and request.prompt and not _verbatim_video:
            try:
                from backend.utils.prompt_enhancer import enhance_video_prompt, get_default_negative_prompt
                # Pass model_family for motion-aware hints (wan vs cogvideox)
                mf = self._model_family(request.model)
                request.prompt = enhance_video_prompt(
                    request.prompt,
                    style=request.prompt_style,
                    width=request.width,
                    height=request.height,
                    model_family=mf,
                    fidelity_mode=getattr(request, "fidelity_mode", False),
                )
                if not request.negative_prompt:
                    request.negative_prompt = get_default_negative_prompt(style=request.prompt_style)
                logger.info(f"Prompt enhanced (style={request.prompt_style}, family={mf}): {request.prompt[:120]}...")
            except Exception as e:
                logger.warning(f"Prompt enhancement failed, using original prompt: {e}")
        elif _verbatim_video:
            logger.info("verbatim prompts ON — skipping video prompt enhancement")

        if request.output_dir:
            batch_dir = Path(request.output_dir)
        else:
            # Standalone generation — Bates-stamped folder in Videos/
            try:
                from backend.services.output_registration import bates_name
                folder_name = bates_name("video_batch", "", self.default_output_dir)
            except Exception:
                # Bates failed — fall back to a date-stamped name rather than
                # raw uuid hex so the user-visible folder name stays readable.
                from datetime import datetime as _dt
                folder_name = f"VideoBatch_{_dt.now().strftime('%m-%d-%Y_%H-%M-%S')}"
            batch_dir = self.default_output_dir / folder_name
        batch_dir = Path(batch_dir)

        item_id = request.metadata.get("item_id") if request.metadata else None
        if not item_id:
            # Bates-stamped item ID instead of UUID soup
            try:
                from backend.services.output_registration import bates_name
                item_id = bates_name("video", "", batch_dir)
            except Exception:
                # Same fallback shape as folder_name above — readable names
                # over uuid hex when the Bates path fails for any reason.
                from datetime import datetime as _dt
                item_id = f"VideoGen_{_dt.now().strftime('%m-%d-%Y_%H-%M-%S')}"
            if request.metadata:
                request.metadata["item_id"] = item_id

        item_dir = batch_dir / item_id
        videos_dir = item_dir / "videos"
        frames_dir = item_dir / "frames"
        thumbs_dir = item_dir / "thumbnails"

        videos_dir.mkdir(parents=True, exist_ok=True)
        frames_dir.mkdir(parents=True, exist_ok=True)
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        result = VideoGenerationResult(
            success=False,
            prompt_used=request.prompt,
            metadata=request.metadata or {},
        )

        try:
            image_path = request.metadata.get("image_path") if request.metadata else None
            model = request.model or "cogvideox-5b"
            seed = request.seed if request.seed is not None else int(time.time() * 1000) % (2**31)

            # VRAM preflight: turn a known-under-spec card into an honest error
            # instead of queuing into a silent mid-render OOM. Fail-open on a
            # broken probe (see _vram_preflight); read-only, allocates nothing.
            preflight_error = self._vram_preflight(model)
            if preflight_error:
                result.error = preflight_error
                return result

            # Defense-in-depth: cap pixel area, then snap dims, before they enter
            # any workflow builder. 1920×1920 (3.7 MPx) requests ran until the
            # watchdog timeout on both Wan variants and read as a hang — and old
            # batch retry_data can replay those dims verbatim.
            # Off-by-one in the snap is the "tensor a (51) must match tensor b (50)" crash.
            request.width, request.height = self._clamp_pixel_area(
                request.width, request.height, model
            )
            request.width, request.height = self._align_dimensions(
                request.width, request.height, model
            )

            interpolation = request.interpolation_multiplier

            # ── Route by model type ──────────────────────────────────
            self._ensure_wan_models()
            self._ensure_ltx_models()
            if model in self.WAN22_MODELS or model in ("wan22", "wan2.2"):
                model_key = model if model in self.WAN22_MODELS else "wan22-14b"
                cfg = self.WAN22_MODELS[model_key]

                # Wan 14B MoE uses ComfyUI-GGUF (UnetLoaderGGUF). TI2V-5B uses UNETLoader.
                if not cfg.get("single") and not self.comfy_node_available("UnetLoaderGGUF"):
                    return VideoGenerationResult(
                        success=False,
                        error=(
                            "ComfyUI is missing UnetLoaderGGUF (ComfyUI-GGUF custom node). "
                            "Wan 14B GGUF models need gguf in the backend venv. "
                            "Fix: backend/venv/bin/pip install 'gguf>=0.13.0' sentencepiece protobuf, "
                            "then restart ComfyUI."
                        ),
                        prompt_used=request.prompt,
                    )

                is_i2v = cfg.get("type") == "i2v"

                if cfg.get("single"):
                    # Wan 2.2 TI2V-5B: ONE model does both — image-to-video if a start
                    # image is given, else text-to-video. Fits 16GB, no MoE two-pass.
                    img_name = None
                    if image_path and Path(image_path).exists():
                        img_name = self._upload_image_to_comfyui(image_path)
                        if not img_name:
                            result.error = "Failed to upload image to ComfyUI"
                            return result
                    workflow = self._create_wan22_5b_workflow(
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        model_key=model_key,
                        image_filename=img_name,
                        num_frames=request.duration_frames,
                        num_inference_steps=request.num_inference_steps,
                        guidance_scale=request.guidance_scale,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps,
                        interpolation_multiplier=interpolation,
                    )
                    logger.info(f"Using Wan 2.2 TI2V-5B ({'i2v' if img_name else 't2v'}, {model_key}) via ComfyUI")
                elif is_i2v:
                    if not image_path or not Path(image_path).exists():
                        result.error = "Wan 2.2 I2V requires an input image."
                        return result
                    uploaded_image = self._upload_image_to_comfyui(image_path)
                    if not uploaded_image:
                        result.error = "Failed to upload image to ComfyUI"
                        return result
                    workflow = self._create_wan22_i2v_workflow(
                        image_filename=uploaded_image,
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        model_key=model_key,
                        num_frames=request.duration_frames,
                        num_inference_steps=request.num_inference_steps,
                        guidance_scale=request.guidance_scale,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps,
                        interpolation_multiplier=interpolation,
                    )
                    logger.info(f"Using Wan 2.2 image-to-video ({model_key}) via ComfyUI GGUF")
                else:
                    if image_path:
                        result.error = f"{model_key} is text-to-video only. Use wan22-14b-i2v for image-to-video."
                        return result
                    workflow = self._create_wan22_t2v_workflow(
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        model_key=model_key,
                        num_frames=request.duration_frames,
                        num_inference_steps=request.num_inference_steps,
                        guidance_scale=request.guidance_scale,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps,
                        interpolation_multiplier=interpolation,
                    )
                    logger.info(f"Using Wan 2.2 text-to-video ({model_key}) via ComfyUI GGUF")

            elif model == "cogvideox-5b":
                if image_path:
                    result.error = f"{model} is text-to-video only. Use cogvideox-5b-i2v for image-to-video."
                    return result
                # Text-to-video via CogVideoX
                hf_model = self.COGVIDEOX_MODELS.get(model, "THUDM/CogVideoX-5b")
                workflow = self._create_cogvideox_text2video_workflow(
                    prompt=request.prompt,
                    model_name=hf_model,
                    num_frames=request.duration_frames,
                    num_inference_steps=request.num_inference_steps,
                    guidance_scale=request.guidance_scale,
                    width=request.width,
                    height=request.height,
                    seed=seed,
                    fps=request.fps,
                    interpolation_multiplier=interpolation,
                )
                # Add optional TeaCache / FETA nodes for CogVideoX
                meta = request.metadata or {}
                self._add_cogvideox_optional_nodes(
                    workflow, sampler_node_id="6",
                    teacache_threshold=meta.get("teacache_threshold"),
                    feta_weight=meta.get("feta_weight"),
                )
                logger.info(f"Using CogVideoX text-to-video ({model}) via ComfyUI")

            elif model == "cogvideox-5b-i2v":
                # Image-to-video via CogVideoX
                if not image_path or not Path(image_path).exists():
                    result.error = "CogVideoX image-to-video requires an input image."
                    return result
                uploaded_image = self._upload_image_to_comfyui(image_path)
                if not uploaded_image:
                    result.error = "Failed to upload image to ComfyUI"
                    return result
                hf_model = self.COGVIDEOX_MODELS.get(model, "THUDM/CogVideoX-5b-I2V")
                workflow = self._create_cogvideox_i2v_workflow(
                    image_filename=uploaded_image,
                    prompt=request.prompt,
                    model_name=hf_model,
                    num_frames=request.duration_frames,
                    num_inference_steps=request.num_inference_steps,
                    guidance_scale=request.guidance_scale,
                    width=request.width,
                    height=request.height,
                    seed=seed,
                    fps=request.fps,
                    interpolation_multiplier=interpolation,
                )
                # Add optional TeaCache / FETA nodes for CogVideoX I2V
                meta = request.metadata or {}
                self._add_cogvideox_optional_nodes(
                    workflow, sampler_node_id="6",
                    teacache_threshold=meta.get("teacache_threshold"),
                    feta_weight=meta.get("feta_weight"),
                )
                logger.info(f"Using CogVideoX image-to-video via ComfyUI")

            elif model in self.LTX_MODELS or str(model).startswith("ltx23"):
                model_key = model if model in self.LTX_MODELS else "ltx23-distilled-fp8"
                # Distilled defaults: 8 steps, CFG=1. Don't silently inherit Cog/Wan defaults.
                ltx_steps = request.num_inference_steps or 8
                ltx_cfg = request.guidance_scale if request.guidance_scale is not None else 1.0
                if ltx_cfg > 1.5:
                    logger.info(
                        "LTX distilled prefers CFG=1 (got %.2f); keeping caller value but "
                        "quality may degrade.",
                        ltx_cfg,
                    )
                if image_path and Path(image_path).exists():
                    uploaded_image = self._upload_image_to_comfyui(image_path)
                    if not uploaded_image:
                        result.error = "Failed to upload image to ComfyUI"
                        return result
                    workflow = self._create_ltx23_i2v_workflow(
                        image_filename=uploaded_image,
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        model_key=model_key,
                        num_frames=request.duration_frames,
                        num_inference_steps=ltx_steps,
                        guidance_scale=ltx_cfg,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps or 16,
                        interpolation_multiplier=interpolation,
                    )
                    logger.info("Using LTX-2.3 distilled I2V (%s) via ComfyUI", model_key)
                else:
                    workflow = self._create_ltx23_t2v_workflow(
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        model_key=model_key,
                        num_frames=request.duration_frames,
                        num_inference_steps=ltx_steps,
                        guidance_scale=ltx_cfg,
                        width=request.width,
                        height=request.height,
                        seed=seed,
                        fps=request.fps or 16,
                        interpolation_multiplier=interpolation,
                    )
                    logger.info("Using LTX-2.3 distilled T2V (%s) via ComfyUI", model_key)

            else:
                # SVD retired 2026-05-29. Supported: wan22-*, cogvideox-*, ltx23-*.
                result.error = (
                    f"Unsupported video model '{model}'. Use wan22-5b, wan22-14b, "
                    f"wan22-14b-i2v, cogvideox-5b, cogvideox-5b-i2v, or ltx23-distilled-fp8."
                )
                return result

            # Apply Real-ESRGAN 2x upscale if requested
            upscale = request.metadata.get("upscale", False) if request.metadata else False
            if upscale:
                # Find VHS_VideoCombine and its current frame source
                vhs_node_id = next(
                    (nid for nid, node in workflow.items() if node.get("class_type") == "VHS_VideoCombine"),
                    None
                )
                if vhs_node_id:
                    # The node currently feeding images to VHS_VideoCombine
                    source_ref = workflow[vhs_node_id]["inputs"].get("images", [None])[0]
                    if source_ref:
                        self._add_upscale_node(workflow, source_ref, vhs_node_id)

            # Apply FaceRestore if requested (requires facerestore_cf custom node)
            if request.face_restore:
                try:
                    from backend.services.video_model_registry import is_model_installed
                    model_ready = is_model_installed("codeformer")
                except Exception:
                    model_ready = False
                if (
                    self.comfy_node_available("FaceRestoreModelLoader")
                    and self.comfy_node_available("FaceRestoreCFWithModel")
                    and model_ready
                ):
                    vhs_node_id = next(
                        (nid for nid, node in workflow.items() if node.get("class_type") == "VHS_VideoCombine"),
                        None
                    )
                    if vhs_node_id:
                        source_ref = workflow[vhs_node_id]["inputs"].get("images", [None])[0]
                        if source_ref:
                            self._add_face_detailer_node(workflow, source_ref, vhs_node_id)
                else:
                    if not self.comfy_node_available("FaceRestoreModelLoader"):
                        logger.warning(
                            "Face restore requested but ComfyUI node FaceRestoreModelLoader is not "
                            "installed. Restart ComfyUI to load facerestore_cf. Continuing without "
                            "face restore."
                        )
                    else:
                        logger.warning(
                            "Face restore requested but codeformer.pth is not installed. "
                            "Install CodeFormer from Manage Video Models. Continuing without face restore."
                        )

            # Apply FreeU if requested
            if request.freeu:
                model_node_id = None
                for nid, node in workflow.items():
                    if node.get("class_type") == "DownloadAndLoadCogVideoModel":
                        model_node_id = nid
                        break
                if model_node_id:
                    # CogVideoX uses a custom typed model (COGVIDEOMODEL) from the wrapper.
                    # Generic FreeU_V2 outputs plain MODEL, which causes ComfyUI prompt
                    # validation to fail with "Return type mismatch ... MODEL vs COGVIDEOMODEL"
                    # on the CogVideoSampler input. Skip for Cog (Wan never reaches here).
                    family = self._model_family(model)
                    if family == "cogvideox":
                        logger.warning(
                            "FreeU Enhance requested for CogVideoX model but skipped: "
                            "incompatible with custom COGVIDEOMODEL typing (would produce "
                            "invalid prompt for CogVideoSampler). General options like "
                            "interpolation, upscale, and prompt enhancement still apply. "
                            "FreeU works on supported Wan paths."
                        )
                    else:
                        freeu_id = self._add_freeu_node(workflow, model_node_id, is_cogvideo=True)
                        for nid, node in workflow.items():
                            if node.get("class_type") == "CogVideoSampler":
                                if node["inputs"].get("model", [None])[0] == model_node_id:
                                    node["inputs"]["model"] = [freeu_id, 0]

            # Apply Lora if requested. Only the CogVideoX backbone has a LoRA hook
            # here (DownloadAndLoadCogVideoModel + CLIPLoader → LoraLoader chain).
            # Wan 2.2's GGUF backbone (UnetLoaderGGUF) has NO matching hook and no
            # base-matched Wan LoRAs exist, so wiring would be a no-op at best —
            # be HONEST about the skip instead of silently dropping it. Identity on
            # the Wan i2v path comes from the init (keyframe) image, not a LoRA.
            if request.lora_name:
                model_node_id = None
                clip_node_id = None
                for nid, node in workflow.items():
                    if node.get("class_type") == "DownloadAndLoadCogVideoModel":
                        model_node_id = nid
                    elif node.get("class_type") == "CLIPLoader":
                        clip_node_id = nid
                family = self._model_family(model)
                if family == "cogvideox":
                    # Same type incompatibility as FreeU: LoraLoader produces generic
                    # MODEL; CogVideoSampler expects COGVIDEOMODEL from the custom loader.
                    # This produces the exact "Return type mismatch MODEL vs COGVIDEOMODEL"
                    # validation error seen in logs. Skip with explanation.
                    logger.warning(
                        "LoRA '%s' requested for CogVideoX but not applied: "
                        "incompatible with custom COGVIDEOMODEL typing used by "
                        "DownloadAndLoadCogVideoModel + CogVideoSampler (causes prompt "
                        "validation failure). The option is ignored for Cog models. "
                        "Use Wan models if LoRA character consistency is needed, or "
                        "rely on the I2V starting image for identity.",
                        request.lora_name,
                    )
                elif model_node_id and clip_node_id:
                    new_model, new_clip = self._add_lora_loader(workflow, model_node_id, clip_node_id, request.lora_name, request.lora_strength)
                    for nid, node in workflow.items():
                        if node.get("class_type") == "CogVideoSampler":
                            if node["inputs"].get("model", [None])[0] == model_node_id:
                                node["inputs"]["model"] = [new_model, 0]
                        elif "TextEncode" in node.get("class_type", ""):
                            if node["inputs"].get("clip", [None])[0] == clip_node_id:
                                node["inputs"]["clip"] = [new_clip, 0]
                else:
                    logger.warning(
                        "LoRA '%s' not applied: backbone=%s has no LoRA hook; "
                        "identity comes from the init frame.",
                        request.lora_name, family,
                    )

            # Frame-sequence export (Q3): when frames are requested, tee the FINAL
            # frames (post upscale/face-restore/interpolation — the exact frames the
            # MP4 receives) to a lossless PNG sequence ALONGSIDE the MP4, so they can be
            # stitched in an external editor without the h264/yuv420p loss. Additive —
            # the MP4 is still produced.
            if getattr(request, "generate_frames_only", False):
                self._add_frame_export(workflow, item_id)

            logger.info("Sending workflow to ComfyUI...")
            # ── Layer 1: live progress bridge ────────────────────────────────
            # Listen to ComfyUI's /ws so the UI sees per-step progress instead of
            # a silent /history poll. Additive + flag-gated + self-terminating —
            # if it fails, generation proceeds exactly as before.
            import uuid as _uuid

            class _NoOpProgressBridge:
                def start(self, *args, **kwargs):
                    return None

                def stop(self):
                    return None

            client_id = _uuid.uuid4().hex
            progress_bridge = _NoOpProgressBridge()
            try:
                from backend.services.comfyui_progress_bridge import ComfyUIProgressBridge

                progress_bridge = ComfyUIProgressBridge()
                progress_bridge.start(
                    client_id=client_id,
                    process_id=item_id,
                    comfy_url=self.comfy_url,
                    workflow=workflow,
                    extra={"batch_id": (request.metadata or {}).get("batch_id", "")},
                )
            except Exception as _be:
                logger.warning(f"Progress bridge unavailable (non-fatal): {_be}")

            prompt_id = self._queue_prompt(workflow, client_id=client_id)

            if not prompt_id:
                progress_bridge.stop()
                result.error = "Failed to queue workflow in ComfyUI"
                return result

            # Timeouts scaled to what the GPU actually needs. If you're rendering
            # 1080p at 50 steps with upscaling, go make a sandwich. Or two.
            is_wan = model in self.WAN22_MODELS or model in ("wan22", "wan2.2")
            steps = request.num_inference_steps or 30
            has_upscale = request.metadata.get("upscale", False) if request.metadata else False
            is_high_res = max(request.width or 0, request.height or 0) >= 1280
            fpb = getattr(request, "frames_per_batch", 1) or 1
            interp = getattr(request, "interpolation_multiplier", 1) or 1
            # Rough estimator: base * (frames/81) * (steps/25) * (1 + 0.6*(has_upscale)) * (1 + 0.3*(fpb>1)) etc.
            base = 1200 if is_wan else 600
            scale = (max(1, request.duration_frames or 49) / 81.0) * (max(10, steps) / 25.0)
            scale *= (1.7 if has_upscale else 1.0)
            scale *= (1.2 if fpb > 1 else 1.0)
            scale *= (1.15 if interp > 1 else 1.0)
            if is_high_res:
                gen_timeout = max(3600, int(base * scale * 2.2))  # HD path
            elif is_wan and (steps >= 40 or has_upscale):
                gen_timeout = max(1800, int(base * scale * 1.8))
            elif is_wan:
                gen_timeout = max(900, int(base * scale))
            else:
                gen_timeout = max(400, int(base * scale * 0.7))
            logger.info(f"Waiting for ComfyUI to complete generation (prompt_id: {prompt_id}, timeout: {gen_timeout}s, steps: {steps}, upscale: {has_upscale}, high_res: {is_high_res}, fpb={fpb})...")
            outputs = self._wait_for_completion(prompt_id, timeout=gen_timeout)
            progress_bridge.stop()  # /history poll owns completion; bridge is done

            if not outputs:
                result.error = "ComfyUI generation timed out or failed"
                return result

            logger.info("Downloading results from ComfyUI...")
            downloaded_files = self._download_result(outputs, videos_dir)

            if not downloaded_files:
                result.error = "No files were generated by ComfyUI"
                return result

            # Separate the rendered video(s) from any exported PNG frame sequence:
            # Q3 frame export adds a SaveImage node alongside VHS_VideoCombine, so the
            # download set can now contain both an MP4 and a PNG sequence. The video is
            # the primary artifact (and what the blank-render guard must inspect).
            _vid_exts = (".mp4", ".webm", ".avi", ".mov", ".gif")
            video_files = [f for f in downloaded_files if Path(f).suffix.lower() in _vid_exts]
            frame_files = sorted(f for f in downloaded_files if Path(f).suffix.lower() == ".png")
            primary = video_files[0] if video_files else downloaded_files[0]

            # Zero-placebo guard (issue #36 Phase 3 / NO-MOCKS charter): never report
            # success for a blank/empty/all-black render. ComfyUI can emit a black clip
            # when a model/loader fails silently. No opt-out — we do not ship fake output.
            blank_reason = _looks_like_blank_video(Path(primary))
            if blank_reason:
                result.error = (
                    f"ComfyUI produced an invalid video: {blank_reason}. This usually "
                    "means a model/loader failed silently — verify the model is fully "
                    "installed."
                )
                logger.error(f"Zero-placebo guard rejected ComfyUI output: {blank_reason}")
                return result  # success stays False — no fake 'done'

            result.video_path = str(Path(primary).relative_to(batch_dir))
            # frame_paths exposes the lossless PNG sequence when it was exported (for
            # self-stitching); otherwise it stays the produced video file(s), preserving
            # prior behavior.
            _fp_source = frame_files if frame_files else (video_files or downloaded_files)
            result.frame_paths = [str(Path(f).relative_to(batch_dir)) for f in _fp_source]
            result.success = True

            # Extract thumbnail from the primary video file
            video_file = Path(primary)
            if video_file.exists() and video_file.suffix.lower() in (".mp4", ".webm", ".avi", ".mov"):
                thumb_filename = video_file.stem + "_thumb.jpg"
                thumb_path = thumbs_dir / thumb_filename
                if self._extract_thumbnail(video_file, thumb_path):
                    result.thumbnail_path = str(thumb_path.relative_to(batch_dir))

            logger.info(f"Video generation successful: {result.video_path}")

            # Register into Documents/Files system if not batch-controlled
            # (batch-controlled videos get registered by the batch_video_generator)
            is_batch_controlled = (request.metadata or {}).get("batch_controlled", False)
            if not is_batch_controlled and result.success:
                try:
                    from backend.services.output_registration import register_file, ensure_subfolder
                    batch_folder_name = batch_dir.name
                    ensure_subfolder("Videos", batch_folder_name)
                    for vid_file in videos_dir.glob("*.mp4"):
                        register_file(
                            physical_path=str(vid_file),
                            folder_name="Videos",
                            subfolder_name=batch_folder_name,
                            file_metadata={"source": "comfyui", "prompt": request.prompt[:200]},
                        )
                except Exception as reg_err:
                    logger.warning(f"Video registration into Documents failed (non-critical): {reg_err}")

            # Post-frame (incl. post-upscale) VRAM hygiene to prevent leaks across batches/frames.
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

            return result

        except Exception as e:
            logger.error(f"Error during video generation: {e}")
            import traceback
            logger.error(traceback.format_exc())
            err_str = str(e)
            is_oom = (
                "out of memory" in err_str.lower()
                or "OutOfMemory" in err_str
                or "CUDA out of memory" in err_str
                or "torch.cuda.OutOfMemoryError" in err_str
                or "MPS backend out of memory" in err_str  # Apple Silicon (#43)
                or ("RuntimeError" in str(type(e)) and "memory" in err_str.lower())
            )
            if is_oom:
                result.error = "OOM during ComfyUI video generation (VRAM exhausted; reduce res/steps, disable upscale/LoRA, or free other models first)"
                try:
                    self.service_available = False
                except Exception:
                    pass
            else:
                result.error = err_str
            result.success = False
            return result


_video_generator_instance: Optional[ComfyUIVideoGenerator] = None


def get_video_generator() -> ComfyUIVideoGenerator:
    global _video_generator_instance
    if _video_generator_instance is None:
        _video_generator_instance = ComfyUIVideoGenerator()
    return _video_generator_instance


def resolve_generated_video_path(result, output_dir) -> Path:
    """Absolute path to the file produced by ``ComfyUIVideoGenerator.generate_video``.

    generate_video returns ``result.video_path`` RELATIVE to ``request.output_dir``
    (it does ``relative_to(batch_dir)`` at the very end). Any caller that set
    ``output_dir`` MUST rejoin it here before touching the file — otherwise the bare
    relative path is read against cwd and ``shutil.copyfile`` dies with ENOENT. This
    is the single source of truth for that resolution (shared by both i2v adapters
    and the music-video clip path). Absolute paths pass through unchanged.
    """
    vp = Path(result.video_path)
    return vp if vp.is_absolute() else (Path(output_dir) / vp)


class SvdI2VGenerator:
    """Adapts the SVD image-to-video path to the Editor's I2VGenerator protocol.

    Character identity rides in via the seed image — the storyboard frame is
    already LoRA-consistent — so SVD (which animates a single image and takes no
    text prompt or LoRA) is the right tool. `prompt`/`loras` are accepted to
    satisfy the protocol but intentionally ignored: the frame carries identity.
    """

    def __init__(self, fps: int = 7):
        self.fps = fps

    def i2v_from_image(
        self, *, image_path: str, prompt: str, loras: list[str],
        duration_seconds: float, output_path: str,
    ) -> str:
        # SVD retired — use CogVideoX-5b I2V to animate the single identity frame.
        # Clamp to a short clip (≤25 frames) to keep VRAM in budget on 16 GB.
        frames = max(14, min(25, int(round(duration_seconds * self.fps)) or 25))
        out_dir = Path(output_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        gen = get_video_generator()
        req = VideoGenerationRequest(
            model="cogvideox-5b-i2v",
            duration_frames=frames,
            fps=self.fps,
            enhance_prompt=False,
            output_dir=out_dir,                      # known base → result path resolves
            metadata={"image_path": image_path},
        )
        result = gen.generate_video(req)
        if not result.success or not result.video_path:
            raise RuntimeError(f"I2V failed: {result.error or 'no video produced'}")
        shutil.copyfile(resolve_generated_video_path(result, out_dir), output_path)
        return output_path


class Wan22I2VGenerator:
    """Wan 2.2 image-to-video adapter for the Editor's I2VGenerator protocol.

    This is the film pipeline's preferred animator (Layer 2 of the film-orchestrator
    plan). Identity rides ENTIRELY in the LoRA-locked storyboard/keyframe image that
    seeds the animation — Wan 2.2 takes a text prompt (motion guidance) plus that
    init frame. It does NOT apply a LoRA: the Wan GGUF backbone (UnetLoaderGGUF) has
    no LoRA hook (see _build_workflow's "no LoRA hook" skip), so any lora_name passed
    here is currently inert — it's threaded through only for forward-compat if a
    Wan-format LoRA loader is ever added. Lock the character in the keyframe upstream,
    not here. Per-step progress is surfaced automatically by the Layer-1 ws bridge
    inside generate_video. Short clips keep identity stable and VRAM in budget on 16 GB.
    """

    def __init__(self, fps: int = 24):
        self.fps = fps

    def i2v_from_image(
        self, *, image_path: str, prompt: str, loras: list[str],
        duration_seconds: float, output_path: str,
    ) -> str:
        # Clamp to a short clip — long Wan I2V drifts the face and blows 16 GB.
        # generate_video handles Wan's "frames % 8 == 1" alignment internally.
        frames = max(17, min(49, int(round(duration_seconds * self.fps)) or 25))
        out_dir = Path(output_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        gen = get_video_generator()
        req = VideoGenerationRequest(
            model="wan22-14b-i2v",
            prompt=prompt or "",
            duration_frames=frames,
            fps=self.fps,
            enhance_prompt=False,
            output_dir=out_dir,                      # known base → result path resolves
            # NOTE: the Wan GGUF backbone applies NO LoRA (no loader hook — see
            # _build_workflow). Identity comes from the LoRA-locked init frame, not
            # from this. lora_name is passed through inert for forward-compat only.
            lora_name=(loras[0] if loras else None),
            metadata={"image_path": image_path},
        )
        result = gen.generate_video(req)
        if not result.success or not result.video_path:
            raise RuntimeError(f"Wan 2.2 I2V failed: {result.error or 'no video produced'}")
        shutil.copyfile(resolve_generated_video_path(result, out_dir), output_path)
        return output_path
