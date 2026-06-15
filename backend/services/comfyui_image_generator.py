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

# A neutral SDXL negative — keeps anatomy/quality sane without fighting the LoRA.
DEFAULT_NEGATIVE = (
    "lowres, bad anatomy, bad hands, cropped, worst quality, low quality, "
    "jpeg artifacts, watermark, signature, deformed, extra limbs, blurry"
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

    # ── workflow ──────────────────────────────────────────────────────
    def _build_workflow(
        self, *, prompt: str, negative: str, lora_names: list[str],
        width: int, height: int, seed: int, steps: int, cfg: float,
        model: str | None = None,
    ) -> dict:
        effective_model = model or self.model
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
        lora_names = [os.path.basename(p) for p in (loras or []) if p]

        # Run preflight (logs warnings for missing; does not raise).
        self._preflight_loras(loras or [])

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
