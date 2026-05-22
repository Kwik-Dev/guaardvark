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
    def __init__(self, comfy_url: str | None = None, lora_strength: float = 0.25):
        self.comfy_url = (comfy_url or _COMFY_URL).rstrip("/")
        self.lora_strength = lora_strength

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
    ) -> dict:
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
    def generate_image(
        self, *, prompt: str, loras: list[str] | None = None,
        output_path: str, width: int = 1024, height: int = 1024,
        negative_prompt: str | None = None, seed: int = 42,
        steps: int = 30, cfg: float = 7.0,
    ) -> str:
        if not self._available():
            raise RuntimeError(
                f"ComfyUI not reachable at {self.comfy_url} — cannot generate storyboard image"
            )

        # ComfyUI resolves LoRAs by basename within its loras search paths;
        # data/training/loras is registered via extra_model_paths.yaml.
        lora_names = [os.path.basename(p) for p in (loras or []) if p]

        workflow = self._build_workflow(
            prompt=prompt,
            negative=negative_prompt or DEFAULT_NEGATIVE,
            lora_names=lora_names,
            width=width, height=height, seed=seed, steps=steps, cfg=cfg,
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
