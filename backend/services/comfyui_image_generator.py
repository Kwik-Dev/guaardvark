"""ComfyUI SDXL image generator — the LoRA-aware ImageGenerator the Storyboard
Artist needs but never had.

This is the missing bridge: the LoRA trainer produces SDXL character LoRAs, but
nothing applied them at generation time (storyboard gen ignored `loras`
entirely). This class builds an SDXL txt2img workflow with a LoraLoader chain so
the trained character actually shows up in the frame — and that consistent frame
is what the SVD I2V step animates, carrying identity into video.

Model loading uses DiffusersLoader against ComfyUI/models/diffusers/sdxl-base-1.0
(a symlink to the diffusers-format SDXL we already have on disk), so no
single-file checkpoint conversion is needed. Trained LoRAs are referenced by
basename because data/training/loras is registered as a ComfyUI loras search
path via extra_model_paths.yaml.
"""
from __future__ import annotations

import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

try:
    from backend.config import COMFYUI_URL
    _COMFY_URL = COMFYUI_URL
except Exception:  # pragma: no cover - config import is environment-specific
    _COMFY_URL = os.environ.get("GUAARDVARK_COMFYUI_URL", "http://127.0.0.1:8188")

# DiffusersLoader reads from ComfyUI/models/diffusers/<this>. Set up as a symlink
# to the diffusers-format SDXL base by the LoRA-consistency wiring.
SDXL_DIFFUSERS_MODEL = os.environ.get("GUAARDVARK_SDXL_DIFFUSERS", "sdxl-base-1.0")

# Flux asset names for the keyframe/storyboard flux branch (align with infographic
# documented downloads for out-of-box success). Users can override via env if they
# use different quants / filenames in their ComfyUI/models/unet/ and clip/ dirs.
# Common issue: exact filename must appear in ComfyUI's object_info list for the
# loader node, or you get "Value not in list" validation errors.
FLUX_UNET = os.environ.get("GUAARDVARK_FLUX_UNET", "flux1-schnell-Q8_0.gguf")
FLUX_T5 = os.environ.get("GUAARDVARK_FLUX_T5", "t5/t5xxl_fp8_e4m3fn.safetensors")
FLUX_CLIP = os.environ.get("GUAARDVARK_FLUX_CLIP", "clip_l.safetensors")
FLUX_VAE = os.environ.get("GUAARDVARK_FLUX_VAE", "ae.safetensors")

# FLUX-dev (full transformer) keyframe path — the identity-lock route for trained
# character LoRAs. Unlike the schnell GGUF branch, this one ACTUALLY chains LoRAs
# (LoraLoaderModelOnly), uses FluxGuidance, and renders at dev step counts. An fp8
# unet keeps the 12B transformer inside a 16 GB card (ComfyUI smart-offloads T5/clip).
# Verified end-to-end on sage_harlow 2026-06-22 (strength 0.9, 28 steps, guidance 3.5).
FLUX_DEV_UNET = os.environ.get("GUAARDVARK_FLUX_DEV_UNET", "flux1-dev.safetensors")
FLUX_DEV_T5 = os.environ.get("GUAARDVARK_FLUX_DEV_T5", "t5xxl_fp16.safetensors")
FLUX_DEV_WEIGHT_DTYPE = os.environ.get("GUAARDVARK_FLUX_DEV_DTYPE", "fp8_e4m3fn")
FLUX_DEV_GUIDANCE = float(os.environ.get("GUAARDVARK_FLUX_DEV_GUIDANCE", "3.5"))

# ── FLUX.1 Kontext [dev] — instruction image editing ───────────────────────────
# The loader filename is single-sourced from the ComfyUI-models registry (SSOT) so
# the download destination and the loader node can never drift (issue #36 class of
# bug). Companions (t5xxl_fp8, clip_l, ae) are the SAME files the FLUX branches
# already use above — no new asset names introduced.
try:
    from backend.services.video_model_registry import (
        VIDEO_MODEL_REGISTRY as _VMR,
        is_model_installed as _is_model_installed,
        comfyui_models_dir as _comfy_models_dir,
    )
    KONTEXT_UNET = _VMR.get("flux-kontext-dev", {}).get("hf_filename", "flux1-kontext-dev-Q6_K.gguf")
except Exception:  # pragma: no cover - registry import is environment-specific
    KONTEXT_UNET = "flux1-kontext-dev-Q6_K.gguf"
    _is_model_installed = None
    _comfy_models_dir = None

# A neutral SDXL negative — keeps anatomy/quality sane without fighting the LoRA.
DEFAULT_NEGATIVE = (
    "lowres, bad anatomy, bad hands, cropped, worst quality, low quality, "
    "jpeg artifacts, watermark, signature, deformed, extra limbs, blurry, "
    # Identity/anatomy-bleed guard (character-LoRA "horse-head" failure mode). Scoped to
    # human-animal HYBRID artifacts so a legitimately-present animal still renders. Kept in
    # sync with backend/utils/prompt_enhancer.IDENTITY_BLEED_NEGATIVE.
    "animal head, horse head, animal ears, animal face, fur on face, snout, muzzle, "
    "human-animal hybrid, anthropomorphic, extra head, two heads, mutated anatomy"
)


class ComfyUIImageGenerator:
    """Implements the storyboard ImageGenerator protocol with real LoRA support.

    generate_image(prompt, loras, output_path, width, height) -> output_path
    """

    # 0.25 is the sweet spot for these rank-16 SDXL character LoRAs — verified on
    # sage_harlow at a fixed seed: 0.25 is sharp + on-model, 0.4 starts to look
    # over-processed, and 0.6 "fries" the image into a blurry mush.
    def __init__(self, comfy_url: str | None = None, lora_strength: float = 0.25, model: str | None = None,
                 flux_unet: str | None = None, flux_t5: str | None = None,
                 flux_clip: str | None = None, flux_vae: str | None = None):
        self.comfy_url = (comfy_url or _COMFY_URL).rstrip("/")
        self.lora_strength = lora_strength
        self.model = model or "sdxl"  # "flux-schnell", "sdxl", "sdxl-lora" etc. (from MV keyframe_model)
        # Allow per-instance override (e.g. from MV settings for different quants)
        self.flux_unet = flux_unet or FLUX_UNET
        self.flux_t5 = flux_t5 or FLUX_T5
        self.flux_clip = flux_clip or FLUX_CLIP
        self.flux_vae = flux_vae or FLUX_VAE

    # ── connectivity ──────────────────────────────────────────────────
    def _available(self) -> bool:
        try:
            return requests.get(self.comfy_url, timeout=3).status_code == 200
        except requests.exceptions.RequestException:
            return False

    def comfyui_installed_engines(self) -> list[str]:
        """Which image engines can the reachable ComfyUI actually run?

        Queries the running server's ``/object_info`` — the authoritative source
        for where the live models are (an external Comfy Desktop install, not the
        bundled plugin dir). Returns engine tags like ``['zimage', 'flux-dev']``.
        """
        try:
            resp = requests.get(f"{self.comfy_url}/object_info", timeout=5)
            resp.raise_for_status()
            info = resp.json()
        except Exception as e:
            logger.warning("ComfyUI object_info probe failed: %s", e)
            return []

        def _choices(node: str, key: str) -> list[str]:
            try:
                lst = info.get(node, {}).get("input", {}).get("required", {}).get(key, [])
                if isinstance(lst, list) and lst and isinstance(lst[0], list):
                    return [str(x) for x in lst[0]]
                if isinstance(lst, list) and lst and isinstance(lst[0], str):
                    return [str(x) for x in lst]
            except Exception:
                pass
            return []

        unet = set(_choices("UNETLoader", "unet_name"))
        unet_gguf = set(_choices("UnetLoaderGGUF", "unet_name"))
        all_unet = unet | unet_gguf
        clip = set(_choices("CLIPLoader", "clip_name"))
        dual = set(_choices("DualCLIPLoader", "clip_name1"))
        vae = set(_choices("VAELoader", "vae_name"))

        engines: list[str] = []
        if ZIMAGE_UNET in all_unet and ZIMAGE_CLIP in clip and ZIMAGE_VAE in vae:
            engines.append("zimage")
        if (FLUX_DEV_UNET in all_unet and FLUX_DEV_T5 in dual
                and FLUX_CLIP in dual and FLUX_VAE in vae):
            engines.append("flux-dev")
        if (FLUX_UNET in all_unet and FLUX_T5 in dual
                and FLUX_CLIP in dual and FLUX_VAE in vae):
            engines.append("flux-schnell")
        return engines

    # ── workflow ──────────────────────────────────────────────────────
    def _build_workflow(
        self, *, prompt: str, negative: str, lora_names: list[str],
        width: int, height: int, seed: int, steps: int, cfg: float,
        model: str | None = None,
    ) -> dict:
        effective_model = model or self.model
        ml = (effective_model or "").lower()

        # ── Capability guard (subject-16 + media model registry) ──────────────
        # Character LoRAs are tied to a base_model_id (sidecar schema v2). Never
        # force every LoRA onto SDXL — only SDXL-format LoRAs use the SDXL chain.
        # FLUX LoRAs use flux-dev; mismatched model tags are corrected to the
        # LoRA's family so identity is applied, not silently dropped.
        if lora_names:
            try:
                from backend.services.media_model_registry import resolve_inference_for_loras
                # Caller may pass basenames only; resolve_inference needs paths when possible.
                # Prefer full paths from kwargs stored on self if present.
                lora_paths = getattr(self, "_last_lora_paths", None) or list(lora_names)
                info = resolve_inference_for_loras(
                    [p if ("/" in p or p.endswith(".safetensors")) else p for p in lora_paths]
                )
                tag = info.get("comfy_model_tag") or "sdxl"
                if info.get("family") == "sdxl" and ("flux" in ml or "zimage" in ml or "z-image" in ml):
                    logger.warning(
                        "LoRAs are SDXL (base=%s) but model=%r — overriding to sdxl so identity applies.",
                        info.get("base_model_id"), effective_model,
                    )
                    effective_model = "sdxl"
                    ml = "sdxl"
                elif info.get("family") == "flux" and "flux" not in ml:
                    logger.warning(
                        "LoRAs are FLUX (base=%s) but model=%r — overriding to %s.",
                        info.get("base_model_id"), effective_model, tag,
                    )
                    effective_model = tag
                    ml = tag
                elif info.get("family") == "zimage":
                    # Z-Image LoRAs are not applied via this Comfy SDXL/FLUX graph yet.
                    logger.warning(
                        "Z-Image LoRAs cannot use Comfy SDXL/FLUX graph (base=%s); "
                        "caller should use offline Z-Image path.",
                        info.get("base_model_id"),
                    )
            except Exception as e:
                # Pre-registry LoRAs / basename-only: fall back to historic SDXL force
                # when flux-schnell would drop LoRAs entirely.
                logger.debug("LoRA base resolve failed (%s); using legacy flux→sdxl guard", e)
                if "flux" in ml and "dev" not in ml:
                    logger.warning(
                        "Keyframe model=%r WITH %d LoRA(s); assuming SDXL legacy LoRAs — "
                        "overriding to sdxl.",
                        effective_model, len(lora_names),
                    )
                    effective_model = "sdxl"
                    ml = "sdxl"

        if "flux" in ml and "dev" in ml:
            # FLUX-dev branch. As of the subject-16 fix this only fires for an
            # explicit flux-dev model with NO LoRAs (plain flux-dev stills) — the
            # capability guard above re-routes every LoRA request to the SDXL
            # branch because this app's character LoRAs are SDXL. The LoraLoaderModelOnly
            # chain below is retained for a FUTURE flux trainer; a flux-format LoRA
            # would need to bypass the guard (e.g. a model tag like "flux-dev-loras")
            # to reach it. Model-only chain: FLUX character LoRAs don't train the
            # text encoder (SimpleTuner "text encoder was not trained"), so clip is
            # left untouched and the trigger word in the prompt does the identity work.
            # Dev UNET/T5 come from the FLUX_DEV_* module constants (override via
            # GUAARDVARK_FLUX_DEV_UNET / _T5) — NOT the per-instance flux_unet/flux_t5,
            # which default to the schnell GGUF and would break this graph.
            model_src = ["unet", 0]
            wf: dict = {
                "unet": {
                    "class_type": "UNETLoader",
                    "inputs": {"unet_name": FLUX_DEV_UNET, "weight_dtype": FLUX_DEV_WEIGHT_DTYPE},
                },
                "clip": {
                    "class_type": "DualCLIPLoader",
                    "inputs": {"clip_name1": FLUX_DEV_T5, "clip_name2": self.flux_clip, "type": "flux"},
                },
                "vae_loader": {
                    "class_type": "VAELoader",
                    "inputs": {"vae_name": self.flux_vae},
                },
            }
            for i, name in enumerate(lora_names):
                nid = f"lora_{i}"
                wf[nid] = {
                    "class_type": "LoraLoaderModelOnly",
                    "inputs": {"model": model_src, "lora_name": name, "strength_model": self.lora_strength},
                }
                model_src = [nid, 0]
            # FluxGuidance owns "guidance" (user slider). KSampler cfg stays 1.0
            # (distilled). Soft floor on steps avoids accidental 1-step garbage;
            # upper bound is operator-owned (quality slider / batch params).
            flux_guidance = float(cfg) if cfg is not None and float(cfg) > 0 else FLUX_DEV_GUIDANCE
            flux_guidance = max(1.0, min(flux_guidance, 10.0))
            flux_steps = int(steps) if steps else 28
            flux_steps = max(4, min(flux_steps, 100))
            wf["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}}
            wf["guid"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": ["pos", 0], "guidance": flux_guidance}}
            # FLUX-dev is CFG-distilled (cfg=1.0) so the negative is inert; an empty
            # encode keeps the KSampler contract valid without fighting the LoRA.
            wf["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["clip", 0]}}
            wf["latent"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
            wf["sampler"] = {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": flux_steps,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": 1.0,
                    "model": model_src,
                    "positive": ["guid", 0],
                    "negative": ["neg", 0],
                    "latent_image": ["latent", 0],
                },
            }
            wf["vae"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}}
            wf["save"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "storyboard-flux-dev", "images": ["vae", 0]}}
            return wf

        if effective_model and "flux" in effective_model.lower():
            # Basic flux branch for storyboard keyframes (P1 wiring per approved plan).
            # Reuses patterns from the working infographic flux workflow.
            # Uses separate VAELoader (ae.safetensors) + correct clip_l (not _sdxl).
            # Hardcoded names must match files present in the running ComfyUI
            # (see GUAARDVARK_FLUX_* envs above). Mismatch => "Value not in list"
            # validation errors from UnetLoaderGGUF / DualCLIPLoader.
            # VAEDecode must not index non-existent output (was ["clip", 2] causing
            # "tuple index out of range").
            wf: dict = {
                "unet": {
                    "class_type": "UnetLoaderGGUF",
                    "inputs": {"unet_name": self.flux_unet},
                },
                "clip": {
                    "class_type": "DualCLIPLoader",
                    "inputs": {
                        "clip_name1": self.flux_t5,
                        "clip_name2": self.flux_clip,
                        "type": "flux",
                    },
                },
                "vae_loader": {
                    "class_type": "VAELoader",
                    "inputs": {"vae_name": self.flux_vae},
                },
                "pos": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": prompt, "clip": ["clip", 0]},
                },
                "neg": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": negative, "clip": ["clip", 0]},
                },
                "latent": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {"width": width, "height": height, "batch_size": 1},
                },
                "sampler": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": seed,
                        "steps": min(steps, 8),  # flux-schnell typically low steps
                        "cfg": 1.0,
                        "sampler_name": "euler",
                        "scheduler": "simple",
                        "denoise": 1.0,
                        "model": ["unet", 0],
                        "positive": ["pos", 0],
                        "negative": ["neg", 0],
                        "latent_image": ["latent", 0],
                    },
                },
                "vae": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]},
                },
                "save": {
                    "class_type": "SaveImage",
                    "inputs": {"filename_prefix": "storyboard-flux", "images": ["vae", 0]},
                },
            }
            return wf

        # Default SDXL path (unchanged for compat; supports LoRA chaining).
        wf: dict = {
            "loader": {
                "class_type": "DiffusersLoader",
                "inputs": {"model_path": SDXL_DIFFUSERS_MODEL},
            },
        }

        # Chain LoraLoaders: each consumes the previous node's MODEL+CLIP.
        model_src = ["loader", 0]
        clip_src = ["loader", 1]
        for i, name in enumerate(lora_names):
            node_id = f"lora_{i}"
            wf[node_id] = {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": name,
                    "strength_model": self.lora_strength,
                    "strength_clip": self.lora_strength,
                    "model": model_src,
                    "clip": clip_src,
                },
            }
            model_src = [node_id, 0]
            clip_src = [node_id, 1]

        wf["pos"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": clip_src},
        }
        wf["neg"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": clip_src},
        }
        wf["latent"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        }
        wf["ksampler"] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed, "steps": steps, "cfg": cfg,
                "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0,
                "model": model_src, "positive": ["pos", 0],
                "negative": ["neg", 0], "latent_image": ["latent", 0],
            },
        }
        wf["vae"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["ksampler", 0], "vae": ["loader", 2]},
        }
        wf["save"] = {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "storyboard", "images": ["vae", 0]},
        }
        return wf

    # ── submission ────────────────────────────────────────────────────
    def _queue(self, workflow: dict) -> Optional[str]:
        resp = requests.post(f"{self.comfy_url}/prompt", json={"prompt": workflow}, timeout=15)
        resp.raise_for_status()
        return resp.json().get("prompt_id")

    def _wait(self, prompt_id: str, timeout: int = 300) -> Optional[dict]:
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = requests.get(f"{self.comfy_url}/history/{prompt_id}", timeout=5)
                resp.raise_for_status()
                hist = resp.json()
                if prompt_id in hist:
                    return hist[prompt_id].get("outputs", {})
            except requests.exceptions.RequestException as e:
                logger.warning("ComfyUI history poll error: %s", e)
            time.sleep(2)
        return None

    def _fetch_first_image(self, outputs: dict, output_path: str) -> Optional[str]:
        for node_output in outputs.values():
            for item in node_output.get("images", []):
                filename = item.get("filename")
                if not filename:
                    continue
                params = {"filename": filename, "type": item.get("type", "output")}
                if item.get("subfolder"):
                    params["subfolder"] = item["subfolder"]
                url = f"{self.comfy_url}/view?{urllib.parse.urlencode(params)}"
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(url, output_path)
                return output_path
        return None

    # ── instruction editing (FLUX.1 Kontext) ──────────────────────────
    def _upload_image_to_comfyui(self, image_path: str) -> Optional[str]:
        """POST a local image to ComfyUI's /upload/image and return its server-side
        name (replicates comfyui_video_generator._upload_image_to_comfyui — that file
        is owned by the video path, so the small uploader is duplicated here)."""
        try:
            with open(image_path, "rb") as fh:
                files = {"image": (os.path.basename(image_path), fh, "image/png")}
                resp = requests.post(f"{self.comfy_url}/upload/image", files=files, timeout=30)
            resp.raise_for_status()
            return resp.json().get("name")
        except Exception as e:
            logger.error("Kontext: failed to upload edit image to ComfyUI: %s", e)
            return None

    def _kontext_installed(self) -> bool:
        """Honest install gate — True only when the Kontext GGUF is actually on disk."""
        if _is_model_installed:
            try:
                return _is_model_installed("flux-kontext-dev")
            except Exception:
                pass
        try:
            base = _comfy_models_dir() if _comfy_models_dir else (
                Path(__file__).resolve().parents[3] / "plugins" / "comfyui" / "ComfyUI" / "models"
            )
            f = base / "unet" / KONTEXT_UNET
            return f.exists() and f.stat().st_size > 0
        except Exception:
            return False

    def _build_kontext_workflow(self, *, src_image_name: str, instruction: str,
                                steps: int, guidance: float, seed: int) -> dict:
        # Native ComfyUI Kontext graph (nodes verified present in this fork:
        # comfy_extras/nodes_flux.py + nodes_edit_model.py). We use CLIPTextEncode +
        # FluxGuidance + ReferenceLatent — NOT CLIPTextEncodeFlux, which bakes its own
        # guidance and would double-apply it alongside FluxGuidance.
        return {
            "unet": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": KONTEXT_UNET}},
            "clip": {"class_type": "DualCLIPLoader",
                     "inputs": {"clip_name1": self.flux_t5, "clip_name2": self.flux_clip, "type": "flux"}},
            "vae_loader": {"class_type": "VAELoader", "inputs": {"vae_name": self.flux_vae}},
            "load": {"class_type": "LoadImage", "inputs": {"image": src_image_name}},
            "scale": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["load", 0]}},
            "encode": {"class_type": "VAEEncode", "inputs": {"pixels": ["scale", 0], "vae": ["vae_loader", 0]}},
            "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": instruction, "clip": ["clip", 0]}},
            "ref": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["pos", 0], "latent": ["encode", 0]}},
            "guid": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["ref", 0], "guidance": guidance}},
            "neg": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}},
            "sampler": {"class_type": "KSampler",
                        "inputs": {"seed": seed, "steps": steps, "cfg": 1.0,
                                   "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                                   "model": ["unet", 0], "positive": ["guid", 0],
                                   "negative": ["neg", 0], "latent_image": ["encode", 0]}},
            "vae": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}},
            "save": {"class_type": "SaveImage", "inputs": {"filename_prefix": "edit-kontext", "images": ["vae", 0]}},
        }

    def edit_image(self, *, image_path: str, instruction: str, output_path: str,
                   steps: int = 28, guidance: float = 2.5, seed: int = 42) -> str:
        """Instruction-guided edit of an existing image via FLUX.1 Kontext [dev].
        Honest failure if ComfyUI is down or the Kontext model isn't installed —
        never returns a fake/unedited image. Default 28 steps (Kontext is under-rendered
        at 20); guidance ~2.5 (do not exceed ~3.5 — over-bakes/identity-drift; cfg stays 1.0).

        Holds the GPU for the whole edit (exclusivity + evict Ollama + free ComfyUI UNDER
        the held lease) so the ~11GB Kontext load can't OOM against a resident chat model
        or a concurrent render — enforced HERE so no caller can bypass it."""
        if not self._available():
            raise RuntimeError(f"ComfyUI not reachable at {self.comfy_url} — cannot edit image")
        if not os.path.exists(image_path):
            raise RuntimeError(f"Source image not found: {image_path}")
        if not self._kontext_installed():
            raise RuntimeError(
                f"FLUX.1 Kontext [dev] model not installed (expected "
                f"ComfyUI/models/unet/{KONTEXT_UNET}). Image editing is unavailable "
                f"until that model finishes downloading."
            )
        from backend.services.gpu_resource_policy import gpu_session
        from backend.services.job_types import JobKind
        import uuid as _uuid
        with gpu_session(JobKind.VIDEO_RENDER, f"chat_edit_{_uuid.uuid4().hex[:8]}",
                         on_busy="raise", evict_ollama=True, free_comfyui=True,
                         vram_estimate_mb=11000, require_fit=True, cross_process=True):
            src_name = self._upload_image_to_comfyui(image_path)
            if not src_name:
                raise RuntimeError("Failed to upload the source image to ComfyUI")
            workflow = self._build_kontext_workflow(
                src_image_name=src_name, instruction=instruction,
                steps=max(int(steps), 1), guidance=guidance, seed=seed,
            )
            prompt_id = self._queue(workflow)
            if not prompt_id:
                raise RuntimeError("ComfyUI did not accept the Kontext edit workflow")
            outputs = self._wait(prompt_id)
            if outputs is None:
                raise RuntimeError(f"ComfyUI image edit timed out (prompt {prompt_id})")
            result = self._fetch_first_image(outputs, output_path)
            if result is None:
                raise RuntimeError(f"ComfyUI produced no edited image for prompt {prompt_id}")
        logger.info("Kontext edit complete: %s", result)
        return result

    # ── public API (ImageGenerator protocol) ──────────────────────────
    def _preflight_loras(self, lora_paths: list[str]) -> None:
        """Best-effort preflight for LoRA paths (media team audit P1-5 / P3-12).
        Checks common locations (data/training/loras + Comfy loras search).
        Logs warning + skips missing ones instead of hard-failing the batch
        (one bad cast LoRA shouldn't nuke an entire storyboard pass).
        Also clamps strength at call sites.
        """
        if not lora_paths:
            return
        search_dirs = []
        try:
            # data/training/loras is the canonical storage for user-trained ones.
            from backend.config import STORAGE_DIR
            search_dirs.append(Path(STORAGE_DIR) / "training" / "loras")
        except Exception:
            pass
        try:
            # Comfy registers extra_model_paths; probe a likely loras/ subdir next to ComfyUI.
            # This is read-only best-effort; the actual LoraLoader inside Comfy will
            # resolve by basename anyway.
            search_dirs.append(Path(__file__).resolve().parents[3] / "plugins" / "comfyui" / "ComfyUI" / "models" / "loras")
        except Exception:
            pass

        for p in lora_paths:
            if not p:
                continue
            pth = Path(p)
            found = pth.exists()
            if not found:
                for d in search_dirs:
                    if (d / pth.name).exists():
                        found = True
                        break
            if not found:
                logger.warning("LoRA preflight: %s not found in training/loras or Comfy loras search; proceeding without it (cast identity may be lost)", p)

    def generate_image(
        self, *, prompt: str, loras: list[str] | None = None,
        output_path: str, width: int = 1024, height: int = 1024,
        negative_prompt: str | None = None, seed: int = 42,
        steps: int = 30, cfg: float = 7.0,
        model: str | None = None,  # e.g. keyframe_model from MV settings ("flux-schnell", "sdxl"...)
    ) -> str:
        if not self._available():
            raise RuntimeError(
                f"ComfyUI not reachable at {self.comfy_url} — cannot generate storyboard image"
            )

        effective_model = model or self.model
        # ComfyUI resolves LoRAs by basename within its loras search paths;
        # data/training/loras is registered via extra_model_paths.yaml.
        lora_paths = [p for p in (loras or []) if p]
        lora_names = [os.path.basename(p) for p in lora_paths]
        # Full paths for media_model_registry sidecar lookup in _build_workflow.
        self._last_lora_paths = lora_paths

        # Run preflight (logs warnings for missing; does not raise).
        self._preflight_loras(lora_paths)

        # Align model tag with LoRA base when possible (SDXL vs FLUX).
        if lora_paths:
            try:
                from backend.services.media_model_registry import resolve_inference_for_loras
                info = resolve_inference_for_loras(lora_paths)
                if info.get("comfy_model_tag"):
                    effective_model = info["comfy_model_tag"]
                if info.get("family") == "zimage":
                    raise RuntimeError(
                        "Z-Image character LoRAs must run on the offline Z-Image path, "
                        "not ComfyUI SDXL/FLUX. (base_model_id=zimage-turbo)"
                    )
            except RuntimeError:
                raise
            except Exception:
                pass

        workflow = self._build_workflow(
            prompt=prompt,
            negative=negative_prompt or DEFAULT_NEGATIVE,
            lora_names=lora_names,
            width=width, height=height, seed=seed, steps=steps, cfg=cfg,
            model=effective_model,
        )

        prompt_id = self._queue(workflow)
        if not prompt_id:
            raise RuntimeError("ComfyUI did not accept the image workflow")

        outputs = self._wait(prompt_id)
        if outputs is None:
            raise RuntimeError(f"ComfyUI image generation timed out (prompt {prompt_id})")

        result = self._fetch_first_image(outputs, output_path)
        if result is None:
            raise RuntimeError(f"ComfyUI produced no image for prompt {prompt_id}")

        logger.info("Storyboard image generated (%d LoRAs): %s", len(lora_names), result)
        return result
