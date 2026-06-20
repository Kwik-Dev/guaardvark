"""
Video model registry — SINGLE SOURCE OF TRUTH for video-model file layout.

Issue #36 root cause #2: the model filenames lived in THREE independently
hand-edited maps that had to agree byte-for-byte:
  1. the download destination (`files[].dst`),
  2. the install/"is it ready?" check (`check_files`), and
  3. the ComfyUI generation loader (`WAN22_MODELS` in comfyui_video_generator.py).

When any one drifted (e.g. a HuggingFace repo reshuffle), the download wrote a
file the generator never loaded → a silent blank render or a model that shows
"not installed" forever. This module collapses all three into one map:

  - `VIDEO_MODEL_REGISTRY` is the only place filenames are written.
  - `check_files` for entries that use `files` is DERIVED from `files[].dst`
    (you edit `files`, never check_files) — see `_normalize_registry()`.
  - The ComfyUI loader map is DERIVED via `wan_comfyui_map()` — the generator
    no longer keeps its own copy.

Both the batch-video API (download/install) and comfyui_video_generator
(generation) import from here, so the two can no longer disagree.
"""

import logging

logger = logging.getLogger(__name__)


VIDEO_MODEL_REGISTRY = {
    "cogvideox-5b": {
        "name": "CogVideoX 5B",
        "description": "Text-to-video, 6s clips. Best quality, needs ~16GB VRAM.",
        "hf_repo": "THUDM/CogVideoX-5b",
        "local_subdir": "CogVideo/CogVideoX-5b",
        # Snapshot download → explicit check paths (subpaths inside the snapshot).
        "check_files": ["transformer/diffusion_pytorch_model-00001-of-00002.safetensors", "vae/diffusion_pytorch_model.safetensors"],
        "size_gb": 11.3,
        "vram_mb": 16000,
        "type": "cogvideox",
    },
    "cogvideox-5b-i2v": {
        "name": "CogVideoX 1.5 5B I2V (BF16)",
        "description": "Image-to-video, 6s clips. Full precision, best quality. Needs ~16GB VRAM.",
        "hf_repo": "Kijai/CogVideoX-comfy",
        "hf_filename": "CogVideoX_1_5_5b_I2V_bf16.safetensors",
        "local_subdir": "checkpoints",
        "check_files": ["CogVideoX_1_5_5b_I2V_bf16.safetensors"],
        # ComfyUI's CogVideoX workflow loads the T5 encoder via CLIPLoader.
        "requires": ["t5-encoder"],
        "size_gb": 10.4,
        "vram_mb": 16000,
        "type": "cogvideox",
    },
    # Wan GGUFs live in HighNoise/ and LowNoise/ subfolders in the repo, but
    # ComfyUI's UnetLoaderGGUF loads them flat from models/unet/. The `files`
    # spec below maps each repo path (`src`) to the exact on-disk name ComfyUI
    # expects (`dst`), so we pull ONLY the two Q5_K_M experts — not all 13
    # quants — and they land where both the loader and the install-check look.
    # check_files is DERIVED from files[].dst (do not add it by hand).
    "wan22-14b": {
        "name": "Wan 2.2 14B MoE (GGUF Q5_K)",
        "description": "State-of-the-art video gen. Two-expert MoE architecture, best quality on 16GB GPU. Requires both HighNoise + LowNoise experts.",
        "hf_repo": "QuantStack/Wan2.2-T2V-A14B-GGUF",
        "local_subdir": "unet",
        "files": [
            {"src": "HighNoise/Wan2.2-T2V-A14B-HighNoise-Q5_K_M.gguf", "dst": "Wan2.2-T2V-A14B-HighNoise-Q5_K_M.gguf"},
            {"src": "LowNoise/Wan2.2-T2V-A14B-LowNoise-Q5_K_M.gguf", "dst": "Wan2.2-T2V-A14B-LowNoise-Q5_K_M.gguf"},
        ],
        # A WAN unet is useless without its VAE + text encoder — installing this
        # model pulls them too, so one click yields a render-ready setup.
        "requires": ["wan-vae", "wan-umt5"],
        "size_gb": 21.0,
        "vram_mb": 11000,
        "type": "wan",
    },
    "wan22-14b-i2v": {
        "name": "Wan 2.2 14B I2V MoE (GGUF Q5_K)",
        "description": "Top-tier image-to-video. Same MoE architecture as Wan 2.2 T2V — start frame conditions an 81-frame clip. Beats CogVideoX I2V on motion + cinematic feel.",
        "hf_repo": "QuantStack/Wan2.2-I2V-A14B-GGUF",
        "local_subdir": "unet",
        # I2V experts are loaded from a nested unet/Wan2.2-I2V/<HighNoise|LowNoise>/
        # path, so dst keeps that nesting (the ComfyUI loader map derives from it).
        "files": [
            {"src": "HighNoise/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf", "dst": "Wan2.2-I2V/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf"},
            {"src": "LowNoise/Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf", "dst": "Wan2.2-I2V/LowNoise/Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf"},
        ],
        "requires": ["wan-vae", "wan-umt5"],
        "size_gb": 21.0,
        "vram_mb": 11000,
        "type": "wan",
    },
    "wan-vae": {
        "name": "Wan 2.1/2.2 VAE",
        "description": "Required by all Wan video models. Shared between versions.",
        "hf_repo": "QuantStack/Wan2.2-T2V-A14B-GGUF",
        "local_subdir": "vae",
        # Repo name is Wan2.1_VAE.safetensors; ComfyUI's VAELoader expects the
        # lowercase wan_2.1_vae.safetensors — download maps one to the other.
        "files": [
            {"src": "VAE/Wan2.1_VAE.safetensors", "dst": "wan_2.1_vae.safetensors"},
        ],
        "size_gb": 0.25,
        "vram_mb": 0,
        "type": "vae",
    },
    "wan-umt5": {
        "name": "UMT5-XXL Text Encoder (FP8)",
        "description": "Required by Wan 2.1/2.2 models for text encoding.",
        "hf_repo": "Osrivers/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "local_subdir": "text_encoders",
        "files": [
            {"src": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "dst": "umt5_xxl_fp8_e4m3fn_scaled.safetensors"},
        ],
        "size_gb": 6.3,
        "vram_mb": 0,
        "type": "encoder",
    },
    "t5-encoder": {
        "name": "T5-XXL Text Encoder (FP8)",
        "description": "Required by CogVideoX models for text encoding.",
        "hf_repo": "comfyanonymous/flux_text_encoders",
        "local_subdir": "clip",
        # CogVideoX workflow's CLIPLoader loads clip/t5/google_t5-v1_1-xxl_
        # encoderonly-fp8_e4m3fn.safetensors — the flux t5xxl fp8 IS that
        # encoder, just under a different name, so we rename on download.
        "files": [
            {"src": "t5xxl_fp8_e4m3fn.safetensors", "dst": "t5/google_t5-v1_1-xxl_encoderonly-fp8_e4m3fn.safetensors"},
        ],
        "size_gb": 4.6,
        "vram_mb": 0,
        "type": "encoder",
    },
    "realesrgan-x2": {
        "name": "Real-ESRGAN 2x Upscaler",
        "description": "Upscales video frames 2x. Applied as post-processing after generation.",
        "hf_repo": "ai-forever/Real-ESRGAN",
        "hf_filename": "RealESRGAN_x2.pth",
        "local_subdir": "upscale_models",
        "check_files": ["RealESRGAN_x2.pth"],
        "size_gb": 0.07,
        "vram_mb": 0,
        "type": "upscaler",
    },
}


def _normalize_registry() -> None:
    """Derive `check_files` from `files[].dst` for every entry that uses `files`.

    This is what makes `files` the single source of truth: the install/ready
    check and the ComfyUI loader both read paths that are guaranteed identical to
    the download destination, so they cannot drift (issue #36).
    """
    for mid, entry in VIDEO_MODEL_REGISTRY.items():
        files = entry.get("files")
        if files:
            entry["check_files"] = [f["dst"] for f in files]
        elif "check_files" not in entry and "hf_filename" in entry:
            entry["check_files"] = [entry["hf_filename"]]


def wan_comfyui_map() -> dict:
    """Build the ComfyUI Wan loader map from the registry (never raises).

    Returns {model_id: {type, unet_high, unet_low, clip, vae}} derived from the
    same `files[].dst` the downloader writes — so the loader always points at the
    bytes that were actually fetched. Replaces the hand-maintained WAN22_MODELS
    copy in comfyui_video_generator.py.
    """
    out = {}
    try:
        for mid, entry in VIDEO_MODEL_REGISTRY.items():
            if entry.get("type") != "wan":
                continue
            dsts = [f["dst"] for f in entry.get("files", [])]
            high = next((d for d in dsts if "HighNoise" in d), None)
            low = next((d for d in dsts if "LowNoise" in d), None)
            vae = clip = None
            for dep in entry.get("requires", []):
                dep_entry = VIDEO_MODEL_REGISTRY.get(dep, {})
                dep_files = dep_entry.get("files", [])
                dep_dst = dep_files[0]["dst"] if dep_files else (dep_entry.get("check_files") or [None])[0]
                if dep_entry.get("type") == "vae":
                    vae = dep_dst
                elif dep_entry.get("type") == "encoder":
                    clip = dep_dst
            out[mid] = {
                "type": "i2v" if "i2v" in mid else "t2v",
                "unet_high": high,
                "unet_low": low,
                "clip": clip,
                "vae": vae,
            }
    except Exception as e:  # never break generation import over a registry quirk
        logger.error("wan_comfyui_map() build failed: %s", e, exc_info=True)
    return out


def verify_registry() -> list:
    """Sanity-check the registry is internally complete. Returns a list of
    human-readable problems (empty = healthy). Never raises."""
    problems = []
    try:
        for mid, entry in VIDEO_MODEL_REGISTRY.items():
            if not entry.get("check_files"):
                problems.append(f"{mid}: no check_files (and no files/hf_filename to derive from)")
            for dep in entry.get("requires", []):
                if dep not in VIDEO_MODEL_REGISTRY:
                    problems.append(f"{mid}: requires unknown model '{dep}'")
            if entry.get("type") == "wan":
                m = wan_comfyui_map().get(mid, {})
                for k in ("unet_high", "unet_low", "clip", "vae"):
                    if not m.get(k):
                        problems.append(f"{mid}: ComfyUI map missing '{k}' (companion/file not resolvable)")
    except Exception as e:
        problems.append(f"verify_registry crashed: {e}")
    return problems


_normalize_registry()

# Loud-but-non-fatal startup check: drift/typos surface in logs instead of as a
# mysterious blank render later.
_problems = verify_registry()
if _problems:
    logger.error("Video model registry has %d consistency problem(s): %s",
                 len(_problems), "; ".join(_problems))
