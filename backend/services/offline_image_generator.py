
import logging
import os
import uuid
import time
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
import tempfile
import threading

logger = logging.getLogger(__name__)

try:
    import torch
    from diffusers import (
        StableDiffusionPipeline,
        StableDiffusionXLPipeline,
        StableDiffusionImg2ImgPipeline,
        StableDiffusionXLImg2ImgPipeline,
        DPMSolverMultistepScheduler
    )
    from PIL import Image
    import safetensors
    diffusion_available = True
    logger.info("Diffusion dependencies loaded successfully")
except ImportError as e:
    diffusion_available = False
    logger.warning(f"Diffusion dependencies not available: {e}")

# Z-Image (Tongyi-MAI) ships in diffusers >= 0.38. Import separately so an older
# diffusers that lacks ZImagePipeline doesn't disable the whole diffusion stack.
try:
    from diffusers import ZImagePipeline, ZImageImg2ImgPipeline
    zimage_available = True
except Exception:  # ImportError on older diffusers
    ZImagePipeline = None
    ZImageImg2ImgPipeline = None
    zimage_available = False

# Krea 2 Turbo (krea.ai): 12B DiT, CFG-distilled 8-step inference. Ships in
# diffusers >= 0.39. Import separately so missing support doesn't break SD/SDXL.
try:
    from diffusers import Krea2Pipeline
    krea2_available = True
except Exception:
    Krea2Pipeline = None
    krea2_available = False

try:
    from backend.config import CACHE_DIR
    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"

try:
    from backend.services.face_restoration_service import get_face_restoration_service
    face_restoration_available = True
except ImportError as e:
    face_restoration_available = False
    logger.warning(f"Face restoration service not available: {e}")

@dataclass
class ImageGenerationRequest:
    prompt: str
    negative_prompt: str = ""
    width: int = 512
    height: int = 512
    num_inference_steps: int = 20
    guidance_scale: float = 7.5
    style: str = "realistic"
    seed: Optional[int] = None
    model: str = "auto"
    content_preset: Optional[str] = None
    auto_enhance: bool = True
    enhance_anatomy: bool = True
    enhance_faces: bool = True
    enhance_hands: bool = True
    restore_faces: bool = True
    face_restoration_weight: float = 0.5
    remove_background: bool = False  # post-process with rembg -> transparent RGBA PNG
    # Batch runs set this so we don't reload/unload a 6B pipeline every image —
    # that peak (especially Z-Image CPU offload) was OOM-killing the Flask process.
    keep_pipeline_loaded: bool = False

@dataclass
class ImageGenerationResult:
    success: bool
    image_path: Optional[str] = None
    image_data: Optional[bytes] = None
    prompt_used: str = ""
    negative_prompt_used: str = ""
    model_used: str = ""
    generation_time: float = 0.0
    image_size: Tuple[int, int] = (512, 512)
    seed_used: Optional[int] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = None

class OfflineImageGenerator:

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent
        self.models_dir = project_root / "data" / "models" / "stable_diffusion"
        self.cache_dir = Path(CACHE_DIR) / "generated_images"

        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.default_model = "runwayml/stable-diffusion-v1-5"
        # Curated quality lineup (2026-05-29 cull). Outdated SD1.5/2.1-era models
        # (sd-2.1, sd-turbo, dreamlike, deliberate, openjourney, analog) were removed.
        # sd-1.5 is kept ONLY as a hidden internal fallback (see hidden_models).
        self.available_models = {
            # Z-Image-Turbo (Tongyi-MAI): 6B, Apache-2.0, ungated. Best prompt
            # adherence per VRAM on a 16 GB card — the preferred all-rounder.
            "zimage-turbo": "Tongyi-MAI/Z-Image-Turbo",
            "krea2-turbo": "krea/Krea-2-Turbo",
            "krea2-raw": "krea/Krea-2-Raw",
            "sd-xl": "stabilityai/stable-diffusion-xl-base-1.0",
            "sdxl-turbo": "stabilityai/sdxl-turbo",
            "realistic-vision": "SG161222/Realistic_Vision_V5.1_noVAE",
            "epic-realism": "emilianJR/epiCRealism",
            # Hidden fallback — resolvable for default_model / load-failure recovery
            # but excluded from user-facing menus (see hidden_models below).
            "sd-1.5": "runwayml/stable-diffusion-v1-5",
        }
        # Models resolvable internally but NOT shown in menus / list_available_models.
        self.hidden_models = {"sd-1.5"}
        # UI metadata for the visible models (label/description/recommended/order).
        # Drives the centralized dropdown via get_available_models().
        self.model_meta = {
            "zimage-turbo": {"label": "Z-Image Turbo (Best)", "description": "Strongest prompt adherence + text, fast (~8 steps). Recommended.", "recommended": True, "order": 0},
            "krea2-turbo": {"label": "Krea 2 Turbo", "description": "12B aesthetic-first model, native 2K, 8 steps CFG-free. Fast inference.", "recommended": False, "order": 1},
            "krea2-raw": {"label": "Krea 2 Raw", "description": "Base 12B checkpoint — less post-trained than Turbo (~52 steps, CFG 3.5). Use for mature/creative prompts or LoRA base.", "recommended": False, "order": 2},
            "sd-xl": {"label": "SDXL Base", "description": "High-res 1024, reliable, huge LoRA ecosystem.", "recommended": False, "order": 3},
            "sdxl-turbo": {"label": "SDXL Turbo (Fast)", "description": "Fast 1024 previews, few steps.", "recommended": False, "order": 4},
            "realistic-vision": {"label": "Realistic Vision", "description": "Top photoreal faces & portraits.", "recommended": False, "order": 5},
            "epic-realism": {"label": "Epic Realism", "description": "Cinematic photorealism.", "recommended": False, "order": 6},
        }

        self.anatomy_negative = "deformed body, distorted anatomy, extra limbs, missing limbs, extra arms, missing arms, extra legs, missing legs, fused limbs, disconnected limbs, floating limbs, asymmetrical body, disproportionate limbs, twisted torso, broken spine, impossible pose, malformed body, mutated anatomy, gross proportions, extra heads, conjoined, siamese, bad anatomy, cropped body, out of frame body, duplicate person, clone"

        self.face_negative = "asymmetrical face, lopsided face, distorted facial features, bad teeth, cross-eyed, lazy eye, eyes looking different directions, uneven eyes, floating eyes, deformed face, malformed face, poorly drawn eyes, poorly drawn nose, poorly drawn mouth, missing eyes, extra eyes, blurry face, low quality face, ugly face"

        self.hands_negative = "bad hands, deformed hands, malformed hands, extra fingers, missing fingers, fused fingers, webbed fingers, too many fingers, wrong number of fingers, six fingers, four fingers, three fingers, mutant hands, claw hands, backwards hands, wrong hand orientation, floating hands, disconnected hands, hands with no wrist, poorly drawn hands"

        self.body_negative = "wrong proportions, head too big, head too small, torso too long, arms too long, arms too short, legs too long, legs too short, unnatural stance, impossible posture, broken joints, dislocated joints, reverse joints"

        self.logic_negative = "floating objects, disconnected elements, impossible physics, wrong perspective, incorrect scale, illogical scene, inconsistent lighting, impossible poses, wrong object placement"

        self.base_negative = "low quality, blurry, distorted, watermark, signature, text, low resolution, pixelated, artifacts, noise, oversaturated, jpeg artifacts"

        self.style_configs = {
            "realistic": {
                "positive_suffix": "photorealistic, high quality, detailed, sharp focus, professional photography, natural lighting, realistic textures, correct proportions",
                "negative_prompt": f"cartoon, anime, illustration, painting, drawing, art, sketch, 3d render, cgi, {self.anatomy_negative}, {self.base_negative}"
            },
            "artistic": {
                "positive_suffix": "artistic, beautiful, creative, masterpiece, fine art, professional artwork, balanced composition, artistic lighting",
                "negative_prompt": f"amateur, {self.anatomy_negative}, {self.base_negative}"
            },
            "cartoon": {
                "positive_suffix": "cartoon style, animated, colorful, clean lines, cel shading, vector illustration, flat design, geometric forms",
                "negative_prompt": f"realistic, photographic, {self.base_negative}"
            },
            "sketch": {
                "positive_suffix": "pencil sketch, hand-drawn, artistic lines, monochrome, detailed linework, professional illustration",
                "negative_prompt": f"colored, photographic, {self.base_negative}"
            },
            "infographic": {
                "positive_suffix": "flat vector illustration, infographic style, clean geometric forms, minimal shadows, professional design, clear composition, no people",
                "negative_prompt": f"photorealism, realistic faces, realistic people, {self.base_negative}"
            },
            "technical": {
                "positive_suffix": "technical illustration, clean lines, precise details, professional diagram, clear composition, minimal style",
                "negative_prompt": f"artistic, {self.base_negative}"
            }
        }

        self.content_presets = {
            "person_portrait": {
                "positive_suffix": "professional portrait photography, natural skin texture, realistic lighting, sharp focus on face, proper facial proportions, symmetrical features",
                "negative_prompt": f"{self.anatomy_negative}, {self.face_negative}, {self.base_negative}",
                "recommended_steps": 30,
                "recommended_guidance": 7.5,
                "recommended_dimensions": (512, 768)
            },
            "person_full_body": {
                "positive_suffix": "full body shot, proper human proportions, natural pose, correct anatomy, realistic stance, balanced composition, anatomically correct",
                "negative_prompt": f"{self.anatomy_negative}, {self.hands_negative}, {self.body_negative}, {self.logic_negative}, floating limbs, disconnected body parts, {self.base_negative}",
                "recommended_steps": 35,
                "recommended_guidance": 8.0,
                "recommended_dimensions": (512, 768)
            },
            "person_athletic": {
                "positive_suffix": "athletic activity, natural movement, dynamic pose, proper body mechanics, focused action, correct body proportions",
                "negative_prompt": f"{self.anatomy_negative}, {self.hands_negative}, {self.body_negative}, {self.logic_negative}, stiff pose, unnatural stance, {self.base_negative}",
                "recommended_steps": 30,
                "recommended_guidance": 7.5,
                "recommended_dimensions": (768, 512)
            },
            "person_working": {
                "positive_suffix": "realistic work scene, natural work pose, logical workspace, proper body posture",
                "negative_prompt": f"{self.anatomy_negative}, {self.hands_negative}, {self.body_negative}, {self.logic_negative}, floating tools, disconnected actions, impossible poses, {self.base_negative}",
                "recommended_steps": 35,
                "recommended_guidance": 8.0,
                "recommended_dimensions": (768, 512)
            },
            "product_photo": {
                "positive_suffix": "product photography, clean background, studio lighting, commercial quality, sharp focus, professional presentation",
                "negative_prompt": f"blurry, distorted, {self.base_negative}",
                "recommended_steps": 25,
                "recommended_guidance": 7.0,
                "recommended_dimensions": (512, 512)
            },
            "landscape": {
                "positive_suffix": "landscape photography, scenic, natural lighting, high dynamic range, beautiful composition, vivid colors",
                "negative_prompt": f"blurry, oversaturated, artificial, {self.base_negative}",
                "recommended_steps": 25,
                "recommended_guidance": 7.0,
                "recommended_dimensions": (768, 512)
            },
            "infographic_preset": {
                "positive_suffix": "flat vector design, clean geometric shapes, minimal design, professional infographic, clear icons, simple composition",
                "negative_prompt": f"photorealistic, 3d, shadows, gradients, complex textures, realistic people, {self.base_negative}",
                "recommended_steps": 20,
                "recommended_guidance": 7.5,
                "recommended_dimensions": (768, 768)
            },
            "general": {
                "positive_suffix": "high quality, detailed, professional, sharp focus",
                "negative_prompt": f"{self.base_negative}",
                "recommended_steps": 20,
                "recommended_guidance": 7.5,
                "recommended_dimensions": (512, 512)
            }
        }

        self._pipeline = None
        self._img2img_pipeline = None
        self._img2img_family = None
        self._current_model = None
        # Offload mode of the resident pipeline: None | "sequential" | "model" | "full"
        self._pipeline_offload_mode = None
        # One-shot force sequential reload after a mid-inference OOM.
        self._force_sequential_offload = False

        self._device = "cpu"
        if torch.cuda.is_available():
            try:
                dummy = torch.zeros(1, device='cuda')
                _ = dummy + dummy
                torch.cuda.synchronize()
                self._device = "cuda"
            except Exception as e:
                logger.warning(f"CUDA is available but not usable (e.g., PyTorch compatibility issue), falling back to CPU: {e}")
        
        self._generation_lock = threading.Lock()

        self._compile_failed = False
        self._compile_unet_orig = None
        self._compile_vae_orig = None

        self.service_available = diffusion_available

        logger.info(f"OfflineImageGenerator initialized - Device: {self._device}, Models dir: {self.models_dir}")

    def _get_model_path(self, model_id: str) -> Path:
        model_name = model_id.replace("/", "--")
        return self.models_dir / model_name

    def _is_model_downloaded(self, model_id: str) -> bool:
        model_path = self._get_model_path(model_id)
        return model_path.exists() and any(model_path.iterdir())

    def _resolve_model_ref(self, model_ref: str) -> str:
        """Catalog key (e.g. krea2-turbo) → HF repo id; pass through HF ids and auto."""
        if not model_ref or model_ref in ("auto", ""):
            return model_ref
        return self.available_models.get(model_ref, model_ref)

    def _krea2_variant(self, model_ref: str) -> str:
        """turbo = CFG-distilled 8-step; raw = base checkpoint ~52 steps / CFG 3.5."""
        key = (model_ref or "").lower()
        mid = self._resolve_model_ref(model_ref).lower()
        if "raw" in key or "krea-2-raw" in mid.replace("_", "-"):
            return "raw"
        return "turbo"

    def _skip_negative_prompt(self, family: str, model_ref: str, guidance_scale: float) -> bool:
        """Turbo/Z-Image-low-CFG skip negatives; Krea Raw uses full CFG negatives."""
        if family == "krea2":
            return self._krea2_variant(model_ref) == "turbo"
        if family == "zimage":
            return guidance_scale <= 1.0
        return False

    def _model_family(self, model_id: str) -> str:
        """Map a catalog key or HF model id to a pipeline family.

        Drives pipeline class, scheduler, VRAM strategy, and generation params.
        """
        key = (model_id or "").lower()
        mid = self._resolve_model_ref(model_id).lower()
        if key.startswith("krea2") or (
            "krea" in mid
            and (
                "krea-2" in mid.replace("_", "-")
                or "krea2" in mid.replace("-", "").replace("/", "")
            )
        ):
            return "krea2"
        if "z-image" in mid or "zimage" in mid or key.startswith("zimage"):
            return "zimage"
        if "xl" in mid or "sdxl" in mid:
            return "sdxl"
        return "sd"

    def _build_img2img_pipeline(self, family: str):
        """Share weights from the loaded txt2img pipeline for img2img edits."""
        if family == 'krea2':
            raise RuntimeError(
                "Krea 2 does not support img2img editing yet — use kontext or sd-xl"
            )
        if family == 'zimage':
            if ZImageImg2ImgPipeline is None:
                raise RuntimeError(
                    "Z-Image img2img is unavailable (upgrade diffusers >= 0.38)"
                )
            return ZImageImg2ImgPipeline(
                transformer=self._pipeline.transformer,
                vae=self._pipeline.vae,
                text_encoder=self._pipeline.text_encoder,
                tokenizer=self._pipeline.tokenizer,
                scheduler=self._pipeline.scheduler,
            )
        if family == 'sdxl':
            return StableDiffusionXLImg2ImgPipeline(
                vae=self._pipeline.vae,
                text_encoder=self._pipeline.text_encoder,
                text_encoder_2=self._pipeline.text_encoder_2,
                tokenizer=self._pipeline.tokenizer,
                tokenizer_2=self._pipeline.tokenizer_2,
                unet=self._pipeline.unet,
                scheduler=self._pipeline.scheduler,
            )
        if family == 'sd':
            return StableDiffusionImg2ImgPipeline(
                vae=self._pipeline.vae,
                text_encoder=self._pipeline.text_encoder,
                tokenizer=self._pipeline.tokenizer,
                unet=self._pipeline.unet,
                scheduler=self._pipeline.scheduler,
                safety_checker=None,
                feature_extractor=None,
                requires_safety_checker=False,
            )
        raise RuntimeError(f"Model family '{family}' does not support img2img editing")

    # Measured pipeline footprints per family (bf16 weights + denoise activations),
    # not aspirations. The 2026-06-10 OOM postmortem: a flat 3500MB estimate let the
    # admission check pass while Z-Image actually allocated 9.9GB — straight into a
    # wall of resident Ollama models (gemma 4.95GB + qwen3-embedding 4.32GB).
    # zimage: WITH enable_model_cpu_offload. krea2 model-offload peak ~14GB on 16GB
    # (2026-07-11); sequential offload is used on consumer cards and peaks lower.
    _FAMILY_VRAM_MB = {"krea2": 14000, "zimage": 11000, "sdxl": 8000, "sd": 4000}
    _KREA2_SEQUENTIAL_VRAM_MB = 10000  # layer-by-layer offload on ≤18GB cards
    # CPU-RAM footprint with enable_model_cpu_offload (weights + PyTorch arena).
    # Observed: ~47 GB RSS on 60 GB box during Z-Image batch; gate before load.
    _FAMILY_RAM_GB = {"krea2": 24.0, "zimage": 24.0, "sdxl": 10.0, "sd": 6.0}
    # auto-router worst-case: zimage leads on consumer cards; krea2 when absent
    _OFFLOAD_TURBO_VRAM_MB = 11000
    # Prefer sequential offload for krea2 on consumer cards (module offload OOMs).
    _SEQUENTIAL_OFFLOAD_VRAM_GB = 18.0

    def _will_use_sequential_for_krea2(self) -> bool:
        """True when krea2 loads with sequential CPU offload (≤18GB CUDA cards)."""
        if self._force_sequential_offload:
            return True
        total = self._cuda_total_vram_gb()
        return total > 0 and total <= self._SEQUENTIAL_OFFLOAD_VRAM_GB

    def _vram_estimate_mb(self, model_id: str) -> int:
        if model_id in (None, "", "auto"):
            # Auto leads with zimage on consumer GPUs; worst-case is still ~11GB.
            # On roomy GPUs auto may pick krea2 — use the sequential/model peak.
            if self._prefer_krea2_for_auto():
                return self._FAMILY_VRAM_MB["krea2"]
            return self._OFFLOAD_TURBO_VRAM_MB
        family = self._model_family(model_id)
        if family == "krea2" and self._will_use_sequential_for_krea2():
            return self._KREA2_SEQUENTIAL_VRAM_MB
        return self._FAMILY_VRAM_MB.get(family, 4000)

    def _ram_estimate_gb(self, model_id: str) -> float:
        if model_id in (None, "", "auto"):
            return self._FAMILY_RAM_GB["zimage"]
        return self._FAMILY_RAM_GB.get(self._model_family(model_id), 6.0)

    def _ensure_vram_for_pipeline(self, model_id: str) -> None:
        """Make room on the card BEFORE the pipeline load, not after it OOMs.

        Two layers, both best-effort (never raises — a wrong guess here should
        degrade to the old behavior, not block generation):
          1. If free VRAM minus a safety margin can't fit this family's real
             footprint, evict resident Ollama models via the canonical
             gpu_resource_policy reclaim. This is cross-process — it works even
             though nothing registers ollama:* slots in the orchestrator registry,
             which is why registry-based eviction alone couldn't save us.
          2. Register the slot with the orchestrator using the real estimate so
             its registry eviction + budget math operate on truth, not 3500.
        """
        if self._pipeline is not None and self._current_model == model_id:
            return  # already resident — its VRAM is already spent
        estimate_mb = self._vram_estimate_mb(model_id)
        try:
            if self._device == "cuda":
                free_b, total_b = torch.cuda.mem_get_info()
                free_mb, total_mb = free_b // (1024 * 1024), total_b // (1024 * 1024)
                margin_mb = max(1024, int(total_mb * 0.10))
                if free_mb - margin_mb < estimate_mb:
                    from backend.services.gpu_resource_policy import evict_ollama_models
                    logger.info(
                        f"VRAM admission: {free_mb}MB free won't fit {estimate_mb}MB "
                        f"(+{margin_mb}MB margin) for {model_id} — evicting Ollama models"
                    )
                    evict_ollama_models()
            from backend.services.gpu_memory_orchestrator import get_orchestrator
            get_orchestrator().request_model("sd:pipeline", vram_estimate_mb=estimate_mb, priority=85)
        except Exception as e:
            logger.warning(f"VRAM admission check failed (non-critical, proceeding): {e}")

    def _has_text_intent(self, prompt: str) -> bool:
        """True if the prompt asks for on-image text — bypass enhancement to keep
        spelling intact (HULK -> HUK otherwise). Shared detector lives in
        prompt_enhancer.has_text_intent so image + video stay in sync.
        """
        from backend.utils.prompt_enhancer import has_text_intent
        return has_text_intent(prompt)

    def _cuda_total_vram_gb(self) -> float:
        """Total device VRAM in GB, or 0 if CUDA is unavailable."""
        try:
            if self._device == "cuda" and torch.cuda.is_available():
                return torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _is_cuda_oom(exc: BaseException) -> bool:
        if isinstance(exc, torch.cuda.OutOfMemoryError):
            return True
        msg = (str(exc) or "").lower()
        return "out of memory" in msg and ("cuda" in msg or "cublas" in msg or "cudnn" in msg)

    def _prefer_krea2_for_auto(self) -> bool:
        """Krea2 is aesthetic-first but ~14GB peak; only auto-lead on roomy GPUs."""
        return self._cuda_total_vram_gb() >= 20.0

    def _auto_select_model(self, prompt: str, style: str = "realistic") -> str:
        """Pick the best DOWNLOADED model for this prompt (chat auto-router).

        Intent-ordered preferences, best first; always falls through to a model
        that actually exists on disk. Z-Image-Turbo leads on ≤16–18GB cards —
        strongest prompt adherence per VRAM. Krea2 leads only on ≥20GB GPUs.
        """
        detection = self.detect_content_type(prompt)
        p = prompt.lower()
        lead = "krea2-turbo" if self._prefer_krea2_for_auto() else "zimage-turbo"
        second = "zimage-turbo" if lead == "krea2-turbo" else "krea2-turbo"

        if detection.get("has_face") and detection.get("has_person"):
            prefs = [lead, second, "realistic-vision", "epic-realism", "sd-xl"]
        elif detection.get("has_person"):
            prefs = [lead, second, "sd-xl", "realistic-vision", "epic-realism"]
        elif any(w in p for w in ("anime", "manga", "cartoon", "illustration", "comic")):
            prefs = [lead, second, "sd-xl"]
        elif detection.get("recommended_preset") in ("landscape", "product_photo"):
            prefs = [lead, second, "sd-xl", "epic-realism"]
        else:  # general / complex
            prefs = [lead, second, "sd-xl", "realistic-vision", "sd-1.5"]

        for key in prefs:
            model_id = self.available_models.get(key)
            if model_id and self._is_model_downloaded(model_id):
                logger.info(f"Auto-router selected '{key}' for prompt: {prompt[:60]}...")
                return key

        # Nothing preferred is downloaded — fall back to any downloaded model.
        for key, model_id in self.available_models.items():
            if self._is_model_downloaded(model_id):
                return key
        return "sd-1.5"

    def _oom_fallback_catalog_key(self, failed_key: str) -> Optional[str]:
        """Next model to try after a CUDA OOM on failed_key (catalog key or HF id)."""
        failed_family = self._model_family(failed_key)
        # Prefer lighter DiT, then SDXL, then classic SD — only if present on disk.
        candidates = []
        if failed_family == "krea2":
            candidates = ["krea2-turbo", "zimage-turbo", "sd-xl", "realistic-vision", "sd-1.5"]
        elif failed_family == "zimage":
            candidates = ["sd-xl", "realistic-vision", "sd-1.5"]
        else:
            candidates = ["sd-xl", "realistic-vision", "sd-1.5"]
        failed_resolved = self._resolve_model_ref(failed_key)
        for key in candidates:
            mid = self.available_models.get(key)
            if not mid or mid == failed_resolved or key == failed_key:
                continue
            if self._is_model_downloaded(mid):
                return key
        return None

    def _apply_family_sampling(self, request: ImageGenerationRequest, family: str) -> None:
        """Force family-appropriate steps/guidance after model switch or fallback."""
        if family == "krea2":
            if self._krea2_variant(request.model or "") == "raw":
                request.num_inference_steps = 52
                request.guidance_scale = 3.5
            else:
                request.num_inference_steps = 8
                request.guidance_scale = 0.0
        elif family == "zimage":
            request.num_inference_steps = 8
            request.guidance_scale = 1.0
        elif family == "sdxl":
            if request.guidance_scale > 9.0:
                request.guidance_scale = 7.5
            elif request.guidance_scale < 4.0:
                request.guidance_scale = 6.0
            if request.num_inference_steps < 20:
                request.num_inference_steps = 25
        else:
            if request.guidance_scale < 4.0:
                request.guidance_scale = 7.5
            if request.num_inference_steps < 15:
                request.num_inference_steps = 20

    def _download_model(self, model_id: str) -> tuple[bool, str | None]:
        if not self.service_available:
            msg = "Diffusion service not available for model download"
            logger.error(msg)
            return False, msg

        try:
            model_path = self._get_model_path(model_id)
            logger.info(f"Downloading model {model_id} to {model_path}")

            family = self._model_family(model_id)

            # Large DiT pipelines (Krea2, Z-Image): snapshot only — do not instantiate
            # during download. from_pretrained() loads weights into RAM and fails on
            # Krea2 with transformers<5.2 (tokenizer vocab + Qwen3-VL rope_parameters).
            if family in ('krea2', 'zimage'):
                if family == 'krea2' and Krea2Pipeline is None:
                    msg = "Krea 2 requested but Krea2Pipeline unavailable (upgrade diffusers >= 0.39)"
                    logger.error(msg)
                    return False, msg
                if family == 'zimage' and ZImagePipeline is None:
                    msg = "Z-Image requested but ZImagePipeline unavailable (upgrade diffusers >= 0.38)"
                    logger.error(msg)
                    return False, msg
                from huggingface_hub import snapshot_download
                logger.info(f"Snapshot-downloading {model_id} (family={family})")
                snapshot_download(
                    repo_id=model_id,
                    local_dir=str(model_path),
                    local_dir_use_symlinks=False,
                )
                if not self._is_model_downloaded(model_id):
                    msg = f"Snapshot download finished but {model_path} is empty"
                    logger.error(msg)
                    return False, msg
                logger.info(f"Model {model_id} downloaded successfully (snapshot)")
                return True, None

            elif family == 'sdxl':
                pipeline_class = StableDiffusionXLPipeline
            else:
                pipeline_class = StableDiffusionPipeline

            # Use bf16 on Ada Lovelace+, fp16 otherwise
            if self._device == "cuda":
                gpu_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            else:
                gpu_dtype = torch.float32

            load_kwargs = {
                "torch_dtype": gpu_dtype,
            }

            # safety_checker kwargs only exist on the classic SD pipeline.
            if family == 'sd':
                load_kwargs["safety_checker"] = None
                load_kwargs["requires_safety_checker"] = False

            logger.info(f"Downloading with {pipeline_class.__name__} (family: {family})")

            pipeline = pipeline_class.from_pretrained(
                model_id,
                **load_kwargs
            )

            pipeline.save_pretrained(model_path)

            del pipeline
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            logger.info(f"Model {model_id} downloaded successfully")
            return True, None

        except Exception as e:
            logger.exception(f"Failed to download model {model_id}: {e}")
            return False, str(e)

    def _load_pipeline(self, model_id: str, *, force_sequential: bool = False) -> bool:
        if not self.service_available:
            return False

        try:
            want_sequential = bool(force_sequential or self._force_sequential_offload)
            if (
                self._pipeline
                and self._current_model == model_id
                and (not want_sequential or self._pipeline_offload_mode == "sequential")
            ):
                return True

            if self._pipeline:
                self._unload_pipeline()

            if not self._is_model_downloaded(model_id):
                logger.info(f"Model {model_id} not found locally, downloading...")
                ok, dl_err = self._download_model(model_id)
                if not ok:
                    if dl_err:
                        logger.error(f"Download failed for {model_id}: {dl_err}")
                    return False

            model_path = self._get_model_path(model_id)

            family = self._model_family(model_id)
            if family == 'krea2':
                if Krea2Pipeline is None:
                    logger.error("Krea 2 requested but Krea2Pipeline unavailable (upgrade diffusers >= 0.39)")
                    return False
                pipeline_class = Krea2Pipeline
            elif family == 'zimage':
                pipeline_class = ZImagePipeline
            elif family == 'sdxl':
                pipeline_class = StableDiffusionXLPipeline
            else:
                pipeline_class = StableDiffusionPipeline
            logger.info(f"Loading model with {pipeline_class.__name__} (family: {family})")

            # Use bf16 on Ada Lovelace+ (SM 8.x), fall back to fp16, then fp32
            if self._device == "cuda":
                if torch.cuda.is_bf16_supported():
                    gpu_dtype = torch.bfloat16
                    logger.info("Using bfloat16 (native Ada Lovelace support)")
                else:
                    gpu_dtype = torch.float16
                    logger.info("Using float16")
            else:
                gpu_dtype = torch.float32

            load_kwargs = {
                "torch_dtype": gpu_dtype,
            }

            if family == 'sd':
                load_kwargs["safety_checker"] = None
                load_kwargs["requires_safety_checker"] = False

            self._pipeline = pipeline_class.from_pretrained(
                model_path,
                **load_kwargs
            )

            # Flow-matching DiTs ship their own scheduler — don't force DPM (SD/SDXL only).
            if family not in ('zimage', 'krea2'):
                self._pipeline.scheduler = DPMSolverMultistepScheduler.from_config(
                    self._pipeline.scheduler.config
                )

            # Z-Image / Krea 2 are flow-matching DiTs too large to sit fully resident
            # alongside other models on a 16 GB card. Krea2 peaks ~14GB with module
            # offload alone (2026-07-11 OOM) — use sequential (layer-by-layer) on
            # consumer cards. Z-Image stays on model offload unless forced sequential.
            offload_mode = "full"
            if family in ('zimage', 'krea2') and self._device == "cuda":
                total_gb = self._cuda_total_vram_gb()
                use_sequential = (
                    want_sequential
                    or (family == "krea2" and total_gb > 0 and total_gb <= self._SEQUENTIAL_OFFLOAD_VRAM_GB)
                )
                if use_sequential:
                    try:
                        self._pipeline.enable_sequential_cpu_offload()
                        offload_mode = "sequential"
                        logger.info(
                            f"{family}: enabled sequential CPU offload "
                            f"(VRAM={total_gb:.1f}GB, 16GB-safe path)"
                        )
                    except Exception as e:
                        logger.warning(
                            f"{family} sequential CPU offload unavailable ({e}); "
                            f"trying model offload"
                        )
                        use_sequential = False
                if offload_mode != "sequential":
                    try:
                        self._pipeline.enable_model_cpu_offload()
                        offload_mode = "model"
                        logger.info(f"{family}: enabled model CPU offload (16 GB VRAM safety)")
                    except Exception as e:
                        logger.warning(f"{family} CPU offload unavailable ({e}); loading fully on GPU")
                        self._pipeline = self._pipeline.to(self._device)
                        offload_mode = "full"
            else:
                self._pipeline = self._pipeline.to(self._device)
                offload_mode = "full"

            self._pipeline_offload_mode = offload_mode
            # Clear one-shot force after a successful sequential (or attempted) load.
            self._force_sequential_offload = False

            # channels_last speeds full-GPU UNet paths, but calling .to(channels_last)
            # on accelerate-offloaded DiT transformers can materialize weights on GPU
            # and defeat offload — skip for sequential/model offload modes.
            if self._device == "cuda" and offload_mode == "full":
                if hasattr(self._pipeline, 'unet'):
                    self._pipeline.unet = self._pipeline.unet.to(memory_format=torch.channels_last)
                    logger.info("Enabled channels_last (NHWC) memory format for UNet")
                elif hasattr(self._pipeline, 'transformer'):
                    self._pipeline.transformer = self._pipeline.transformer.to(
                        memory_format=torch.channels_last
                    )
                    logger.info("Enabled channels_last (NHWC) memory format for transformer")

            if hasattr(self._pipeline, "enable_attention_slicing"):
                self._pipeline.enable_attention_slicing()

            if hasattr(self._pipeline, "enable_xformers_memory_efficient_attention"):
                try:
                    self._pipeline.enable_xformers_memory_efficient_attention()
                    logger.info("Enabled xformers memory efficient attention")
                except Exception as e:
                    logger.warning(f"Failed to enable xformers memory efficient attention: {e}")

            if hasattr(self._pipeline, "enable_vae_slicing"):
                self._pipeline.enable_vae_slicing()
                logger.info("Enabled VAE slicing")

            # VAE tiling only at high resolutions (>1024px) — avoids quality loss at normal sizes
            self._vae_tiling_available = hasattr(self._pipeline, "enable_vae_tiling")
            logger.info(f"VAE tiling available (will activate for resolutions > 1024px)")

            # torch.compile(mode='reduce-overhead') uses CUDA graphs which allocate
            # persistent IPC semaphores. When the pipeline is later moved to CPU and
            # torch.cuda.empty_cache() is called, those semaphores leak — leaving the
            # process in a state where Python's interpreter shutdown fires
            # `resource_tracker: leaked semaphore` warnings and the process eventually
            # aborts. This is a known PyTorch issue. Observed killing the backend on
            # 2026-04-11 (PIDs 3047360, 3065470, 3074584).
            #
            # DISABLED BY DEFAULT. Set GUAARDVARK_ENABLE_TORCH_COMPILE=1 to re-enable
            # if/when PyTorch fixes the underlying CUDA graph cleanup bug.
            if (
                os.environ.get("GUAARDVARK_ENABLE_TORCH_COMPILE") == "1"
                and hasattr(torch, 'compile')
                and self._device == "cuda"
                and not self._compile_failed
                and offload_mode == "full"  # compile + offload hooks do not mix well
            ):
                try:
                    if hasattr(self._pipeline, 'unet'):
                        self._compile_unet_orig = self._pipeline.unet
                        self._pipeline.unet = torch.compile(self._pipeline.unet, mode="reduce-overhead")
                        logger.info("Enabled torch.compile(mode='reduce-overhead') for UNet")

                    if hasattr(self._pipeline, 'vae'):
                        self._compile_vae_orig = self._pipeline.vae
                        self._pipeline.vae = torch.compile(self._pipeline.vae, mode="reduce-overhead")
                        logger.info("Enabled torch.compile(mode='reduce-overhead') for VAE")
                except Exception as e:
                    logger.warning(f"Failed to enable torch.compile: {e}")
                    self._compile_unet_orig = None
                    self._compile_vae_orig = None

            self._current_model = model_id
            logger.info(
                f"Pipeline loaded successfully with model {model_id} "
                f"(offload={offload_mode})"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to load pipeline with model {model_id}: {e}")
            self._pipeline = None
            self._current_model = None
            self._pipeline_offload_mode = None
            return False

    def _detect_subject_count(self, prompt: str) -> Dict[str, Any]:
        prompt_lower = prompt.lower()

        single_indicators = ['a ', 'an ', 'one ', 'single ', 'solo ']
        multiple_indicators = ['two ', 'three ', 'four ', 'multiple ', 'several ', 'many ', 'group of ', 'couple ', 'pair of ']

        has_single = any(indicator in prompt_lower for indicator in single_indicators)
        has_multiple = any(indicator in prompt_lower for indicator in multiple_indicators)

        person_plurals = ['men', 'women', 'people', 'workers', 'builders', 'chefs', 'doctors',
                         'teachers', 'children', 'boys', 'girls', 'employees', 'professionals']
        has_plural_subject = any(plural in prompt_lower for plural in person_plurals)

        person_singulars = ['man', 'woman', 'person', 'child', 'boy', 'girl']
        has_and_conjunction = False
        if ' and ' in prompt_lower:
            words_around_and = []
            for singular in person_singulars:
                if singular in prompt_lower:
                    words_around_and.append(singular)
            if len(words_around_and) > 1 and ' and ' in prompt_lower:
                has_and_conjunction = True

        if has_multiple or has_plural_subject or has_and_conjunction:
            subject_count = "multiple"
        elif has_single:
            subject_count = "single"
        else:
            subject_count = "single"

        return {
            "subject_count": subject_count,
            "is_single_subject": subject_count == "single",
            "is_multiple_subjects": subject_count == "multiple"
        }

    def detect_content_type(self, prompt: str) -> Dict[str, Any]:
        prompt_lower = prompt.lower()

        detection = {
            "has_person": False,
            "has_face": False,
            "has_hands": False,
            "has_action": False,
            "has_interaction": False,
            "has_spatial": False,
            "detected_actions": [],
            "recommended_preset": "general",
            "warnings": [],
            "subject_count_info": {}
        }

        detection["subject_count_info"] = self._detect_subject_count(prompt)

        person_words = ['man', 'woman', 'person', 'people', 'worker', 'builder', 'chef', 'doctor',
                       'teacher', 'child', 'boy', 'girl', 'human', 'employee', 'staff', 'professional',
                       'craftsman', 'mechanic', 'plumber', 'electrician', 'carpenter', 'painter']
        if any(word in prompt_lower for word in person_words):
            detection["has_person"] = True

        face_words = ['portrait', 'face', 'headshot', 'selfie', 'close-up', 'closeup', 'head shot']
        if any(word in prompt_lower for word in face_words):
            detection["has_face"] = True

        hand_words = ['hand', 'holding', 'grabbing', 'gripping', 'carrying', 'lifting', 'pointing',
                     'touching', 'typing', 'writing', 'drawing', 'using']
        if any(word in prompt_lower for word in hand_words):
            detection["has_hands"] = True

        action_map = {
            'building': ['building', 'constructing', 'assembling', 'installing', 'fixing', 'repairing'],
            'working': ['working', 'operating', 'using', 'handling'],
            'cooking': ['cooking', 'baking', 'preparing food', 'chef', 'kitchen'],
            'driving': ['driving', 'steering', 'riding', 'in car', 'behind wheel'],
            'typing': ['typing', 'at computer', 'at keyboard', 'coding', 'programming'],
            'reading': ['reading', 'studying', 'with book', 'looking at'],
            'sports': ['playing', 'running', 'jumping', 'swimming', 'exercising', 'training', 'jogging', 'treadmill', 'workout'],
            'gardening': ['gardening', 'planting', 'watering', 'pruning', 'mowing']
        }

        for action_type, keywords in action_map.items():
            if any(keyword in prompt_lower for keyword in keywords):
                detection["has_action"] = True
                detection["detected_actions"].append(action_type)

        interaction_words = ['with', 'using', 'holding', 'beside', 'operating', 'gripping', 'manipulating']
        if detection["has_person"] and any(word in prompt_lower for word in interaction_words):
            detection["has_interaction"] = True

        spatial_words = ['next to', 'behind', 'in front of', 'beside', 'between', 'under', 'over',
                        'sitting on', 'standing by', 'leaning against', 'near']
        if any(word in prompt_lower for word in spatial_words):
            detection["has_spatial"] = True

        if detection["has_face"] and detection["has_person"]:
            detection["recommended_preset"] = "person_portrait"
        elif detection["has_person"] and 'sports' in detection["detected_actions"]:
            detection["recommended_preset"] = "person_athletic"
        elif detection["has_person"] and detection["has_action"]:
            detection["recommended_preset"] = "person_working"
        elif detection["has_person"]:
            detection["recommended_preset"] = "person_full_body"
        elif any(word in prompt_lower for word in ['landscape', 'scenery', 'nature', 'mountain', 'beach', 'forest', 'sunset', 'sunrise']):
            detection["recommended_preset"] = "landscape"
        elif any(word in prompt_lower for word in ['product', 'item', 'object', 'merchandise', 'bottle', 'package']):
            detection["recommended_preset"] = "product_photo"
        elif any(word in prompt_lower for word in ['infographic', 'diagram', 'chart', 'icon', 'vector', 'flat']):
            detection["recommended_preset"] = "infographic_preset"

        if detection["has_person"] and detection["has_hands"] and detection["has_action"]:
            detection["warnings"].append("Complex scene with person + hands + action may require multiple attempts")
        if len(detection["detected_actions"]) > 1:
            detection["warnings"].append("Multiple actions detected - simpler prompts often yield better results")

        return detection

    def enhance_prompt_for_quality(self, prompt: str, style: str = "realistic",
                                   content_preset: Optional[str] = None,
                                   auto_enhance: bool = True,
                                   enhance_anatomy: bool = True,
                                   enhance_faces: bool = True,
                                   enhance_hands: bool = True) -> Tuple[str, str, Dict[str, Any]]:
        logger.debug(
            "Image prompt enhancement started "
            f"(prompt_len={len(prompt)}, auto_enhance={auto_enhance})"
        )
        detection = self.detect_content_type(prompt)

        preset_name = content_preset or detection["recommended_preset"]
        preset = self.content_presets.get(preset_name, self.content_presets["general"])
        style_config = self.style_configs.get(style, self.style_configs["realistic"])

        enhancements = []
        negative_parts = []

        enhancements.append(style_config.get("positive_suffix", ""))
        enhancements.append(preset.get("positive_suffix", ""))

        negative_parts.append(self.base_negative)
        negative_parts.append(style_config.get("negative_prompt", ""))
        negative_parts.append(preset.get("negative_prompt", ""))

        if auto_enhance:
            if detection["has_person"] and enhance_anatomy:
                enhancements.append("correct human proportions, realistic anatomy, proper body structure")
                negative_parts.append(self.anatomy_negative)

            if detection["has_face"] and enhance_faces:
                enhancements.append("detailed facial features, symmetrical face, natural expression")
                negative_parts.append(self.face_negative)

            if detection["has_hands"] and enhance_hands:
                enhancements.append("correctly drawn hands, proper finger count, natural hand position")
                negative_parts.append(self.hands_negative)

            action_enhancements = {
                'building': ['construction scene', 'realistic work pose', 'focused activity'],
                'working': ['realistic work environment', 'logical positioning', 'professional setting'],
                'cooking': ['kitchen scene', 'realistic cooking pose', 'culinary activity'],
                'driving': ['hands on steering wheel', 'seated in vehicle', 'vehicle interior'],
                'typing': ['fingers on keyboard', 'seated at desk', 'office setting'],
                'reading': ['natural reading pose', 'focused attention'],
                'sports': ['athletic pose', 'dynamic movement', 'active motion'],
                'gardening': ['outdoor setting', 'natural environment', 'gardening activity']
            }

            is_single_subject = detection.get("subject_count_info", {}).get("is_single_subject", True)

            for action in detection["detected_actions"]:
                if action in action_enhancements:
                    enhancements.extend(action_enhancements[action])
                    negative_parts.append(f"floating objects, illogical {action}")

            if detection["has_spatial"]:
                enhancements.append("correct spatial relationships, logical positioning, proper depth, consistent perspective")
                negative_parts.append("wrong perspective, floating objects, incorrect scale, impossible physics")

            if detection["has_interaction"] and not is_single_subject:
                enhancements.append("realistic interaction, natural positioning")
                negative_parts.append("awkward poses, impossible poses")

            enhancements.append("coherent scene, logical composition, consistent lighting, unified style")
            negative_parts.append("inconsistent elements, mixed styles, impossible scene, conflicting perspectives")

        unique_enhancements = []
        seen = set()
        for e in enhancements:
            e_clean = e.strip()
            if e_clean and e_clean.lower() not in seen:
                seen.add(e_clean.lower())
                unique_enhancements.append(e_clean)

        enhanced_prompt = f"{prompt}, {', '.join(unique_enhancements)}"
        logger.debug(
            f"Image prompt enhancement complete (enhanced_prompt_len={len(enhanced_prompt)})"
        )

        unique_negatives = []
        seen_neg = set()
        for n in negative_parts:
            for part in n.split(', '):
                part_clean = part.strip()
                if part_clean and part_clean.lower() not in seen_neg:
                    seen_neg.add(part_clean.lower())
                    unique_negatives.append(part_clean)

        negative_prompt = ", ".join(unique_negatives)

        detection["preset_used"] = preset_name
        detection["style_used"] = style
        detection["enhancements_applied"] = unique_enhancements

        return enhanced_prompt, negative_prompt, detection

    def _enhance_prompt(self, prompt: str, style: str) -> Tuple[str, str]:
        if any(keyword in prompt.lower() for keyword in ['elements:', 'style keywords:', 'negative prompt:']):
            style_config = self.style_configs.get(style, self.style_configs["realistic"])
            return prompt, style_config['negative_prompt']

        enhanced_prompt, negative_prompt, _ = self.enhance_prompt_for_quality(
            prompt=prompt,
            style=style,
            auto_enhance=True
        )

        return enhanced_prompt, negative_prompt

    def _optimize_prompt_for_tokens(self, prompt: str, max_tokens: int = 75) -> str:
        words = prompt.split()
        if len(words) <= max_tokens:
            return prompt
        
        if any(keyword in prompt.lower() for keyword in ['elements:', 'style keywords:', 'negative prompt:']):
            main_desc = prompt.split('\n')[0].strip()
            return main_desc
        
        words = prompt.split()
        if len(words) > max_tokens:
            important_keywords = ['high quality', 'detailed', 'professional', 'clean', 'minimal']
            truncated_words = words[:max_tokens-3]
            
            for keyword in important_keywords:
                if keyword not in ' '.join(truncated_words) and len(truncated_words) < max_tokens:
                    truncated_words.append(keyword)
            
            return ' '.join(truncated_words)
        
        return prompt

    def get_prompt_templates(self) -> Dict[str, Dict[str, Any]]:
        return {
            "infographic": {
                "template": """{subject}, {style}, {color_palette}, {background}, {elements}, {mood}

Elements: {element_list}

Style Keywords: {style_keywords}

Negative Prompt: {negative_prompt}""",
                "example": {
                    "subject": "flat vector illustration, infographic style",
                    "style": "clean geometric forms, minimal shadows",
                    "color_palette": "muted palette of blues and grays with accent red",
                    "background": "legal courtroom background with courthouse columns",
                    "elements": "scales of justice, legal documents, gavel, judge's bench silhouette, professional briefcase",
                    "mood": "serious tone",
                    "element_list": "gavel, legal documents with seal, scale of justice, professional desk, law books",
                    "style_keywords": "legal services, professional, corporate law, business consultation, justice, legal practice",
                    "negative_prompt": "no photorealism, no people faces, no over-saturation, no glitter or cartoon color, no watermarks"
                }
            },
            "realistic": {
                "template": "{subject}, {quality}, {lighting}, {composition}, {mood}",
                "example": {
                    "subject": "A majestic mountain landscape at sunset",
                    "quality": "photorealistic, high quality, detailed, sharp focus",
                    "lighting": "golden hour lighting, dramatic clouds",
                    "composition": "balanced composition, professional photography",
                    "mood": "peaceful mood, serene atmosphere"
                }
            },
            "technical": {
                "template": "{subject}, {style}, {details}, {composition}",
                "example": {
                    "subject": "technical diagram of a system",
                    "style": "clean lines, precise details, professional diagram",
                    "details": "clear labels, minimal style, technical illustration",
                    "composition": "clear composition, balanced layout"
                }
            }
        }

    def get_quality_presets(self) -> Dict[str, Dict[str, Any]]:
        return {
            "fast": {
                "num_inference_steps": 15,
                "guidance_scale": 7.0,
                "description": "Quick generation, good for testing"
            },
            "standard": {
                "num_inference_steps": 20,
                "guidance_scale": 7.5,
                "description": "Balanced quality and speed"
            },
            "high": {
                "num_inference_steps": 30,
                "guidance_scale": 8.0,
                "description": "High quality, slower generation"
            },
            "professional": {
                "num_inference_steps": 25,
                "guidance_scale": 7.5,
                "description": "Professional quality for final output"
            }
        }

    def _notify_vision_pipeline(self, action: str):
        """Best-effort notification to vision pipeline. Fire and forget."""
        try:
            import requests as req
            req.post("http://localhost:8201/gpu/contention",
                     json={"source": "image_gen", "action": action}, timeout=1)
        except Exception:
            pass

    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        start_time = time.time()

        result = ImageGenerationResult(
            success=False,
            prompt_used=request.prompt,
            negative_prompt_used=request.negative_prompt,
            image_size=(request.width, request.height)
        )

        if not self.service_available:
            result.error = "Image generation service not available - missing dependencies"
            return result

        with self._generation_lock:
            self._notify_vision_pipeline("start")
            # Central gpu_session for *all* direct calls (chat tool, batch, API, edits via img2img).
            # Provides GPU lease + evict + GlobalLoadGate RAM/swap admission using our real estimates.
            # Reentrant-safe; chat tool may outer-wrap.
            from backend.services.gpu_resource_policy import gpu_session
            from backend.services.job_operation_gate import GpuBusyError
            from backend.services.job_types import JobKind
            import uuid as _uuid_local
            # Resolve routing before admission so VRAM/RAM estimates match the loaded model.
            if request.model in (None, "", "auto"):
                request.model = self._auto_select_model(request.prompt, request.style)
            if (
                self._has_text_intent(request.prompt)
                and "sd-xl" in self.available_models
                and request.model in (None, "", "auto", "sdxl-turbo")
            ):
                if request.model != "sd-xl":
                    logger.info(f"Text intent: routing {request.model} -> sd-xl for crisper type")
                request.model = "sd-xl"

            model_id = self.available_models.get(request.model, self.default_model)
            logger.info(f"Using model: {request.model} -> {model_id}")
            ram_est = self._ram_estimate_gb(request.model)
            vram_est = self._vram_estimate_mb(request.model)

            try:
                with gpu_session(JobKind.VIDEO_RENDER, f"gen_{_uuid_local.uuid4().hex[:8]}",
                                 on_busy="raise", evict_ollama=True, free_comfyui=True,
                                 vram_estimate_mb=vram_est, ram_estimate_gb=ram_est,
                                 require_fit=True, cross_process=True):
                    pass

                family = self._model_family(model_id)
                is_sdxl = family == 'sdxl'

                # Family-appropriate sampling. Batch UI often validates against the
                # *requested* model (e.g. zimage-turbo → steps=12, guidance=1.0). If
                # we later land on SDXL (auto-router, load fallback, text reroute),
                # those turbo params produce soft/painterly "artwork" instead of photos.
                if family in ('krea2', 'zimage', 'sdxl'):
                    if is_sdxl and request.guidance_scale > 9.0:
                        logger.warning(
                            f"Guidance scale {request.guidance_scale} is too high for SDXL "
                            f"(causes black images). Auto-correcting to 7.5"
                        )
                    elif is_sdxl and request.guidance_scale < 4.0:
                        logger.warning(
                            f"Guidance scale {request.guidance_scale} is too low for SDXL. "
                            f"Auto-correcting to 6.0"
                        )
                    elif is_sdxl and request.num_inference_steps < 20:
                        logger.warning(
                            f"Steps {request.num_inference_steps} too low for SDXL "
                            f"(turbo leftovers). Auto-correcting to 25"
                        )
                    self._apply_family_sampling(request, family)
                elif request.guidance_scale > 20.0:
                    logger.warning(f"Guidance scale {request.guidance_scale} is extremely high. Capping at 15.0")
                    request.guidance_scale = 15.0

                # Clamp dimensions to safe maximums (prevents CUDA OOM).
                # SDXL and Z-Image are native high-res; classic SD tops out at 768.
                max_dim = 1536 if family in ('sdxl', 'zimage', 'krea2') else 768
                if request.width > max_dim or request.height > max_dim:
                    logger.warning(f"Resolution {request.width}x{request.height} exceeds safe max {max_dim}x{max_dim}, clamping")
                    request.width = min(request.width, max_dim)
                    request.height = min(request.height, max_dim)
                # Ensure minimum dimensions
                request.width = max(request.width, 256)
                request.height = max(request.height, 256)
                result.image_size = (request.width, request.height)

                # Make room BEFORE loading — family-aware estimate + Ollama eviction
                # when the card is too full. Runs after ALL model rerouting so the
                # estimate matches the model we actually load.
                self._ensure_vram_for_pipeline(model_id)

                if not self._load_pipeline(model_id):
                    # Requested model failed to load (gated/removed repo, missing
                    # download, OOM). Fall back to the default model instead of
                    # failing the whole request — keeps chat image-gen resilient.
                    if model_id != self.default_model:
                        logger.warning(
                            f"Model {request.model} ({model_id}) failed to load; "
                            f"falling back to default {self.default_model}"
                        )
                        model_id = self.default_model
                        family = self._model_family(model_id)
                        is_sdxl = family == 'sdxl'
                        self._apply_family_sampling(request, family)
                        self._ensure_vram_for_pipeline(model_id)
                        if not self._load_pipeline(model_id):
                            result.error = f"Failed to load fallback model {self.default_model}"
                            return result
                    else:
                        result.error = f"Failed to load model {request.model} ({model_id})"
                        return result

                # Best-effort VRAM hygiene after successful load for this job.
                # Helps the chat LLM reload faster on the next turn.
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.synchronize()
                        torch.cuda.empty_cache()
                except Exception:
                    pass

                text_mode = self._has_text_intent(request.prompt)
                if text_mode:
                    # Crisp text/logos need a larger canvas — at 512 the type renders
                    # as mush. Bump capable models to 1024 when the request is below it
                    # (within the per-model max already clamped above: 1536 for these).
                    if family in ("sdxl", "zimage", "krea2") and request.width < 1024 and request.height < 1024:
                        logger.info(
                            f"Text intent: enlarging canvas {request.width}x{request.height} -> 1024x1024 for legible type"
                        )
                        request.width = 1024
                        request.height = 1024
                        result.image_size = (request.width, request.height)
                    style_config = self.style_configs.get(
                        request.style, self.style_configs.get("realistic", {})
                    )
                    style_negative = style_config.get("negative_prompt", "") or ""
                    enhanced_prompt = request.prompt
                    if request.style == "realistic":
                        light_real = "photorealistic, professional photography, natural lighting, sharp focus"
                        if light_real.lower() not in enhanced_prompt.lower():
                            enhanced_prompt = f"{enhanced_prompt}, {light_real}"
                    detection = self.detect_content_type(request.prompt)
                    logger.info(
                        "Text-rendering intent detected — preserving spelling; "
                        f"still applying style={request.style!r} negatives"
                    )
                elif request.auto_enhance:
                    enhanced_prompt, style_negative, detection = self.enhance_prompt_for_quality(
                        prompt=request.prompt,
                        style=request.style,
                        content_preset=request.content_preset,
                        auto_enhance=True,
                        enhance_anatomy=request.enhance_anatomy,
                        enhance_faces=request.enhance_faces,
                        enhance_hands=request.enhance_hands
                    )
                    logger.info(f"Content detection: {detection.get('recommended_preset')}, enhancements: {len(detection.get('enhancements_applied', []))}")
                else:
                    enhanced_prompt, style_negative = self._enhance_prompt(request.prompt, request.style)
                    detection = {}

                # Don't token-trim in text mode — the quoted characters must survive intact.
                if not text_mode:
                    enhanced_prompt = self._optimize_prompt_for_tokens(enhanced_prompt)

                combined_negative = request.negative_prompt
                if style_negative:
                    combined_negative = f"{combined_negative}, {style_negative}" if combined_negative else style_negative

                generator = None
                if request.seed is not None:
                    generator = torch.Generator(device=self._device).manual_seed(request.seed)
                    result.seed_used = request.seed
                else:
                    seed = torch.randint(0, 2**32, (1,)).item()
                    generator = torch.Generator(device=self._device).manual_seed(seed)
                    result.seed_used = seed

                logger.debug(
                    f"Final image prompt lengths: positive={len(enhanced_prompt)}, "
                    f"negative={len(combined_negative)}"
                )
                logger.info(f"Generating image: {enhanced_prompt[:100]}...")

                # Dynamic VAE tiling: only at high res to preserve quality at normal sizes
                if getattr(self, '_vae_tiling_available', False):
                    if request.width > 1024 or request.height > 1024:
                        self._pipeline.enable_vae_tiling()
                        logger.info(f"VAE tiling enabled ({request.width}x{request.height} > 1024px)")
                    elif hasattr(self._pipeline, 'disable_vae_tiling'):
                        self._pipeline.disable_vae_tiling()

                def _call_pipeline(pos_prompt: str, neg_prompt: Optional[str]):
                    """Single forward; raises on OOM / compile failure for recovery."""
                    if family in ('zimage', 'krea2'):
                        return self._pipeline(
                            prompt=pos_prompt,
                            negative_prompt=neg_prompt,
                            width=request.width,
                            height=request.height,
                            num_inference_steps=request.num_inference_steps,
                            guidance_scale=request.guidance_scale,
                            generator=generator,
                        )
                    if self._device == "cuda":
                        # Match autocast dtype to the LOADED model dtype. The model is
                        # loaded in bf16 on Ada+ (see _load_pipeline), but autocast's
                        # CUDA default is fp16 — which overflows SDXL's VAE to NaN and
                        # yields a pure-black image. bf16 has fp32-range exponents, so
                        # this keeps SDXL/SD output correct.
                        _ac_dtype = (
                            torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                        )
                        with torch.autocast("cuda", dtype=_ac_dtype):
                            return self._pipeline(
                                prompt=pos_prompt,
                                negative_prompt=neg_prompt,
                                width=request.width,
                                height=request.height,
                                num_inference_steps=request.num_inference_steps,
                                guidance_scale=request.guidance_scale,
                                generator=generator,
                            )
                    return self._pipeline(
                        prompt=pos_prompt,
                        negative_prompt=neg_prompt,
                        width=request.width,
                        height=request.height,
                        num_inference_steps=request.num_inference_steps,
                        guidance_scale=request.guidance_scale,
                        generator=generator,
                    )

                if family in ('zimage', 'krea2'):
                    neg = (
                        None
                        if self._skip_negative_prompt(
                            family, request.model or "", request.guidance_scale
                        )
                        else combined_negative
                    )
                else:
                    neg = combined_negative

                try:
                    output = _call_pipeline(enhanced_prompt, neg)
                except (AssertionError, RuntimeError, torch.cuda.OutOfMemoryError) as infer_err:
                    # torch.compile recovery (SD/SDXL full-GPU path)
                    is_compile_failure = (
                        (isinstance(infer_err, AssertionError) and not str(infer_err))
                        or any(kw in str(infer_err).lower() for kw in
                               ('triton', 'dynamo', 'inductor', 'cuda graph', 'torch.compile'))
                    )
                    has_compiled_modules = (
                        self._compile_unet_orig is not None or self._compile_vae_orig is not None
                    )
                    if (
                        is_compile_failure
                        and has_compiled_modules
                        and not self._compile_failed
                        and not self._is_cuda_oom(infer_err)
                    ):
                        logger.warning(
                            f"torch.compile first-pass failure "
                            f"({type(infer_err).__name__}: {infer_err or 'no message'}) "
                            f"— stripping compiled wrappers and retrying in eager mode"
                        )
                        if self._compile_unet_orig is not None:
                            self._pipeline.unet = self._compile_unet_orig
                        if self._compile_vae_orig is not None:
                            self._pipeline.vae = self._compile_vae_orig
                        self._compile_failed = True
                        output = _call_pipeline(enhanced_prompt, neg)
                    elif self._is_cuda_oom(infer_err):
                        # 2026-07-11: krea2 model-offload still peaked ~14.3GB on 16GB.
                        # Recover in-process: sequential offload same model, then
                        # lighter catalog fallback. Avoid recursive generate_image
                        # (generation lock is non-reentrant).
                        failed_label = request.model
                        logger.error(
                            f"CUDA OOM during inference with {failed_label} "
                            f"(offload={self._pipeline_offload_mode}): {infer_err}"
                        )
                        try:
                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()
                        except Exception:
                            pass
                        self._unload_pipeline()

                        output = None
                        # Attempt 1: same model with sequential offload if not already.
                        if family in ('krea2', 'zimage'):
                            logger.warning(
                                f"{family} OOM → retrying with sequential CPU offload"
                            )
                            self._force_sequential_offload = True
                            self._ensure_vram_for_pipeline(model_id)
                            if self._load_pipeline(model_id, force_sequential=True):
                                try:
                                    # Rebuild generator after OOM (device state may be dirty)
                                    if request.seed is not None:
                                        generator = torch.Generator(device=self._device).manual_seed(
                                            request.seed
                                        )
                                    else:
                                        seed = result.seed_used or torch.randint(0, 2**32, (1,)).item()
                                        generator = torch.Generator(device=self._device).manual_seed(seed)
                                        result.seed_used = seed
                                    output = _call_pipeline(enhanced_prompt, neg)
                                    logger.info(
                                        f"{family} sequential offload retry succeeded after OOM"
                                    )
                                except Exception as seq_err:
                                    if self._is_cuda_oom(seq_err):
                                        logger.warning(
                                            f"Sequential offload still OOM for {failed_label}: {seq_err}"
                                        )
                                        self._unload_pipeline()
                                        try:
                                            if torch.cuda.is_available():
                                                torch.cuda.empty_cache()
                                        except Exception:
                                            pass
                                    else:
                                        raise

                        # Attempt 2: lighter model fallback
                        if output is None:
                            fb_key = self._oom_fallback_catalog_key(failed_label)
                            if not fb_key:
                                raise infer_err
                            logger.warning(
                                f"{failed_label} OOM → falling back to '{fb_key}'"
                            )
                            request.model = fb_key
                            model_id = self.available_models.get(fb_key, self.default_model)
                            family = self._model_family(model_id)
                            is_sdxl = family == 'sdxl'
                            self._apply_family_sampling(request, family)
                            if family in ('zimage', 'krea2'):
                                neg = (
                                    None
                                    if self._skip_negative_prompt(
                                        family, request.model or "", request.guidance_scale
                                    )
                                    else combined_negative
                                )
                            else:
                                neg = combined_negative
                            max_dim = 1536 if family in ('sdxl', 'zimage', 'krea2') else 768
                            request.width = min(max(request.width, 256), max_dim)
                            request.height = min(max(request.height, 256), max_dim)
                            result.image_size = (request.width, request.height)
                            self._ensure_vram_for_pipeline(model_id)
                            if not self._load_pipeline(model_id):
                                raise RuntimeError(
                                    f"OOM fallback model '{fb_key}' failed to load"
                                ) from infer_err
                            if request.seed is not None:
                                generator = torch.Generator(device=self._device).manual_seed(
                                    request.seed
                                )
                            else:
                                seed = result.seed_used or torch.randint(0, 2**32, (1,)).item()
                                generator = torch.Generator(device=self._device).manual_seed(seed)
                                result.seed_used = seed
                            output = _call_pipeline(enhanced_prompt, neg)
                            logger.info(f"OOM fallback to '{fb_key}' succeeded")
                    else:
                        raise


                image = output.images[0]
                if image is None:
                    result.error = "Pipeline returned no image"
                    result.generation_time = time.time() - start_time
                    return result

                image_id = str(uuid.uuid4())
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"generated_{timestamp}_{image_id}.png"
                image_path = self.cache_dir / filename

                image.save(image_path, "PNG")

                face_restoration_metadata = None
                if request.restore_faces:
                    try:
                        face_service = get_face_restoration_service()
                        service_available = face_service.service_available
                    except Exception as e:
                        logger.warning(f"Could not check face restoration availability: {e}")
                        service_available = False

                    if service_available:
                        should_restore = detection.get("has_person") or detection.get("has_face") if detection else False

                        if should_restore:
                            logger.info("Applying GFPGAN face restoration...")
                            try:
                                success, restored_pil, restore_meta = face_service.restore_face_from_pil(
                                    image=image,
                                    weight=request.face_restoration_weight
                                )

                                if success and restored_pil:
                                    image = restored_pil
                                    image.save(image_path, "PNG")
                                    face_restoration_metadata = restore_meta
                                    logger.info(f"Face restoration applied: {restore_meta.get('faces_detected', 0)} faces enhanced")
                                else:
                                    logger.warning(f"Face restoration failed: {restore_meta.get('error', 'Unknown error') if restore_meta else 'No metadata'}")
                            except Exception as e:
                                logger.error(f"Face restoration error: {e}")
                        else:
                            logger.debug("Skipping face restoration - no faces detected in prompt")
                    else:
                        logger.debug("Face restoration requested but service not available")

                # Optional: knock out the background → transparent RGBA PNG (icons,
                # clip-art, logos). Post-process pass; diffusion itself outputs opaque RGB.
                if getattr(request, "remove_background", False):
                    try:
                        from rembg import remove as _rembg_remove
                        image = _rembg_remove(image)  # returns an RGBA PIL image
                        image.save(image_path, "PNG")  # PNG preserves the alpha channel
                        logger.info("Transparent background applied (rembg)")
                    except Exception as e:
                        logger.error(f"Background removal failed (rembg): {e}")

                result.success = True
                result.image_path = str(image_path)
                result.prompt_used = enhanced_prompt

                # Extra hygiene: release what we can so the chat LLM can reload promptly.
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                except Exception:
                    pass
                result.negative_prompt_used = combined_negative
                result.model_used = self._current_model
                result.generation_time = time.time() - start_time
                result.metadata = {
                    "steps": request.num_inference_steps,
                    "guidance_scale": request.guidance_scale,
                    "style": request.style,
                    "device": self._device,
                    "auto_enhance": request.auto_enhance,
                    "content_preset": detection.get("preset_used") if detection else None,
                    "content_detection": {
                        "has_person": detection.get("has_person"),
                        "has_face": detection.get("has_face"),
                        "has_hands": detection.get("has_hands"),
                        "has_action": detection.get("has_action"),
                        "detected_actions": detection.get("detected_actions", [])
                    } if detection else None,
                    "face_restoration": face_restoration_metadata
                }

                logger.info(f"Image generated successfully in {result.generation_time:.2f}s: {image_path}")

            except Exception as e:
                logger.error(f"Image generation failed: {type(e).__name__}: {e}", exc_info=True)
                error_msg = str(e) or f"{type(e).__name__} (no message)"
                result.error = f"Generation failed: {error_msg}"
                result.generation_time = time.time() - start_time
            finally:
                self._notify_vision_pipeline("stop")
                if not getattr(request, "keep_pipeline_loaded", False):
                    # Immediately free VRAM — don't wait for the 300s idle timer.
                    # The LLM needs the GPU back for the next chat turn.
                    self._unload_pipeline()
                    try:
                        from backend.services.gpu_memory_orchestrator import get_orchestrator
                        get_orchestrator().release_model("sd:pipeline")
                    except Exception:
                        pass

        return result

    def _unload_pipeline(self):
        """Fully unload the pipeline and return RAM/VRAM to the pool.
        Aggressive host RAM release for heavy offloaded models (Z-Image etc).
        Called after every chat gen (keep=False) and at batch end.
        """
        if self._pipeline is None:
            return

        try:
            import psutil
            proc = psutil.Process()
            rss_before = proc.memory_info().rss / (1024**3)
        except Exception:
            rss_before = 0.0

        pipeline = self._pipeline
        self._pipeline = None
        self._img2img_pipeline = None
        self._img2img_family = None
        self._current_model = None
        self._pipeline_offload_mode = None
        self._compile_unet_orig = None
        self._compile_vae_orig = None

        # Explicitly break references to submodules (weights stay in CPU tensors until all refs gone)
        for attr in ("unet", "vae", "text_encoder", "text_encoder_2", "tokenizer", "tokenizer_2",
                     "scheduler", "transformer", "safety_checker", "feature_extractor"):
            try:
                if hasattr(pipeline, attr):
                    obj = getattr(pipeline, attr)
                    setattr(pipeline, attr, None)
                    del obj
            except Exception:
                pass

        try:
            # Accelerate CPU-offload hooks retain large CPU weight copies until removed.
            if hasattr(pipeline, "remove_all_hooks"):
                pipeline.remove_all_hooks()
            free_hooks = getattr(pipeline, "maybe_free_model_hooks", None)
            if callable(free_hooks):
                free_hooks()
        except Exception as e:
            logger.warning(f"Pipeline hook teardown failed (continuing unload): {e}")

        try:
            pipeline.to("cpu")
        except Exception as e:
            logger.debug(f"pipeline.to(cpu) skipped during unload: {e}")

        try:
            del pipeline
        except Exception:
            pass

        import gc
        gc.collect()
        gc.collect()  # second pass often helps release more
        if torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
            except Exception:
                pass

        # More aggressive: return memory to OS (glibc)
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass

        try:
            import psutil
            proc = psutil.Process()
            rss_after = proc.memory_info().rss / (1024**3)
            if rss_before > 0:
                logger.info(f"SD pipeline unloaded; RSS {rss_before:.1f}GB -> {rss_after:.1f}GB (delta {rss_before-rss_after:+.1f}GB); hooks/gc/CUDA cleared + malloc_trim")
            else:
                logger.info("SD pipeline unloaded; hooks cleared, gc run, CUDA cache cleared")
        except Exception:
            logger.info("SD pipeline unloaded; hooks cleared, gc run, CUDA cache cleared")

    def generate_image_from_image(
        self, prompt: str, init_image, strength: float = 0.20,
        negative_prompt: str = "", width: int = 512, height: int = 512,
        num_inference_steps: int = 20, guidance_scale: float = 7.5,
        seed: int = None, model: str = "sd-1.5",
        keep_pipeline_loaded: bool = False
    ) -> ImageGenerationResult:
        """Generate an image using img2img — takes an existing PIL Image and
        produces a variation guided by the prompt and strength parameter.

        Args:
            prompt: Text prompt for the output image.
            init_image: PIL.Image input frame.
            strength: How much to change (0.0=identical, 1.0=ignore input).
            Other args mirror generate_image().

        Returns:
            ImageGenerationResult with the new image path.
        """
        result = ImageGenerationResult(success=False)
        start_time = time.time()

        if not self.service_available:
            result.error = "Image generation service not available"
            return result

        with self._generation_lock:
            self._notify_vision_pipeline("start")
            try:
                if model in (None, "", "auto"):
                    model = self._auto_select_model(prompt, "realistic")

                model_id = self.available_models.get(model, model)
                family = self._model_family(model_id)

                if family == 'krea2':
                    result.error = (
                        f"Model {model} does not support img2img editing yet — "
                        "use kontext or sd-xl for edits"
                    )
                    return result

                if family == 'zimage':
                    num_inference_steps = 8
                    guidance_scale = 1.0
                elif family == 'sdxl':
                    if guidance_scale > 9.0:
                        guidance_scale = 7.5
                    elif guidance_scale < 4.0:
                        guidance_scale = 6.0

                max_dim = 1536 if family in ('sdxl', 'zimage', 'krea2') else 768
                width = min(max(int(width), 256), max_dim)
                height = min(max(int(height), 256), max_dim)

                # Ensure the base txt2img pipeline is loaded (downloads model if needed)
                if not self._load_pipeline(model_id):
                    result.error = f"Failed to load model {model} ({model_id})"
                    return result

                if (
                    self._img2img_pipeline is None
                    or self._current_model != model_id
                    or self._img2img_family != family
                ):
                    logger.info(
                        "Building img2img pipeline for %s (family=%s)",
                        model_id, family,
                    )
                    self._img2img_pipeline = self._build_img2img_pipeline(family)
                    self._img2img_family = family
                    logger.info("img2img pipeline ready (%s)", family)

                # Resize init_image to target dimensions
                if init_image.size != (width, height):
                    init_image = init_image.resize((width, height), Image.LANCZOS)

                # Convert to RGB if needed
                if init_image.mode != "RGB":
                    init_image = init_image.convert("RGB")

                generator = None
                if seed is not None:
                    generator = torch.Generator(device=self._device).manual_seed(seed)
                    result.seed_used = seed
                else:
                    seed = torch.randint(0, 2**32, (1,)).item()
                    generator = torch.Generator(device=self._device).manual_seed(seed)
                    result.seed_used = seed

                combined_negative = negative_prompt or "blurry, low quality, distorted"

                logger.info(
                    "img2img (%s): strength=%s, steps=%s, prompt=%r",
                    family, strength, num_inference_steps, prompt[:80],
                )

                call_kwargs = dict(
                    prompt=prompt,
                    image=init_image,
                    strength=strength,
                    width=width,
                    height=height,
                    num_inference_steps=num_inference_steps,
                    guidance_scale=guidance_scale,
                    generator=generator,
                )

                if family == 'zimage':
                    # Z-Image is bf16 flow-matching — no autocast; CFG distilled out.
                    call_kwargs["negative_prompt"] = None
                    output = self._img2img_pipeline(**call_kwargs)
                elif self._device == "cuda":
                    _ac_dtype = (
                        torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                    )
                    with torch.autocast("cuda", dtype=_ac_dtype):
                        output = self._img2img_pipeline(
                            **call_kwargs,
                            negative_prompt=combined_negative,
                        )
                else:
                    output = self._img2img_pipeline(
                        **call_kwargs,
                        negative_prompt=combined_negative,
                    )

                image = output.images[0]
                if image is None:
                    result.error = "img2img pipeline returned no image"
                    result.generation_time = time.time() - start_time
                    return result

                image_id = str(uuid.uuid4())
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"img2img_{timestamp}_{image_id}.png"
                image_path = self.cache_dir / filename
                image.save(image_path, "PNG")

                result.success = True
                result.image_path = str(image_path)
                result.prompt_used = prompt
                result.negative_prompt_used = combined_negative
                result.model_used = self._current_model
                result.generation_time = time.time() - start_time

                logger.info(f"img2img generated in {result.generation_time:.2f}s: {image_path}")

            except Exception as e:
                logger.error(f"img2img failed: {type(e).__name__}: {e}", exc_info=True)
                error_msg = str(e) or f"{type(e).__name__} (no message)"
                result.error = f"img2img failed: {error_msg}"
                result.generation_time = time.time() - start_time
            finally:
                self._notify_vision_pipeline("stop")
                if not keep_pipeline_loaded:
                    self._unload_pipeline()

        return result

    def get_available_models(self) -> Dict[str, Any]:
        """Visible image models for menus/API. Excludes hidden fallbacks (sd-1.5)
        and carries UI metadata (label/description/recommended/order) so the
        frontend dropdowns can be driven entirely from this single source.
        """
        models = {}

        for model_key, model_id in self.available_models.items():
            if model_key in self.hidden_models:
                continue
            meta = self.model_meta.get(model_key, {})
            models[model_key] = {
                "id": model_id,
                "name": model_key,
                "label": meta.get("label", model_key),
                "description": meta.get("description", ""),
                "recommended": meta.get("recommended", False),
                "order": meta.get("order", 99),
                "downloaded": self._is_model_downloaded(model_id),
                "current": model_id == self._current_model,
                "size_estimate": (
                    "28-36GB" if "krea" in model_id.lower()
                    else "16GB" if "z-image" in model_id.lower() or "zimage" in model_key
                    else "12-15GB" if "xl" in model_id.lower()
                    else "4-7GB"
                )
            }

        return models

    def get_service_status(self) -> Dict[str, Any]:
        optimizations = {}
        
        if self._pipeline:
            optimizations = {
                "attention_slicing": hasattr(self._pipeline, "enable_attention_slicing"),
                "xformers_available": hasattr(self._pipeline, "enable_xformers_memory_efficient_attention"),
                "vae_slicing": hasattr(self._pipeline, "enable_vae_slicing"),
                "vae_tiling": hasattr(self._pipeline, "enable_vae_tiling"),
                "torch_compile_available": hasattr(torch, 'compile'),
                "cpu_offloading_disabled": True
            }
        
        return {
            "service_available": self.service_available,
            "device": self._device,
            "cuda_available": torch.cuda.is_available() if diffusion_available else False,
            "current_model": self._current_model,
            "models_dir": str(self.models_dir),
            "cache_dir": str(self.cache_dir),
            "available_models": self.get_available_models(),
            "available_styles": list(self.style_configs.keys()),
            "optimizations": optimizations,
            "pytorch_version": torch.__version__ if diffusion_available else "N/A",
            "prompt_templates": self.get_prompt_templates(),
            "quality_presets": self.get_quality_presets()
        }

    def clear_cache(self) -> Dict[str, Any]:
        try:
            import shutil
            if self.cache_dir.exists():
                shutil.rmtree(self.cache_dir)
                self.cache_dir.mkdir(parents=True, exist_ok=True)

            return {"success": True, "message": "Cache cleared successfully"}

        except Exception as e:
            return {"success": False, "error": str(e)}


_generator_instance = None

def get_image_generator() -> OfflineImageGenerator:
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = OfflineImageGenerator()
    return _generator_instance


def generate_image(prompt: str, style: str = "realistic", width: int = 512, height: int = 512,
                  steps: int = 20, guidance: float = 7.5, seed: Optional[int] = None) -> ImageGenerationResult:
    request = ImageGenerationRequest(
        prompt=prompt,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=guidance,
        style=style,
        seed=seed
    )

    generator = get_image_generator()
    return generator.generate_image(request)


def get_generator_status() -> Dict[str, Any]:
    generator = get_image_generator()
    return generator.get_service_status()
