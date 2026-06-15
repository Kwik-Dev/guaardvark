"""Single source of truth for hardware-derived provisioning decisions.

Pure functions over the dict produced by HardwareDetector.detect()["gpu"]
(and ram/arch). Stdlib-only: this module may be imported before heavy deps
(torch, etc.) are installed, by both the backend and the dep_reconciler.
"""
from __future__ import annotations

import hashlib
from typing import Any

# Overridable to match install_pytorch.sh's GUAARDVARK_ROCM_WHL default.
DEFAULT_ROCM_WHL = "rocm6.3"


def _compute_major(gpu: dict[str, Any]) -> int | None:
    cap = gpu.get("compute_cap")
    if not cap:
        return None
    try:
        return int(str(cap).split(".")[0])
    except (ValueError, IndexError):
        return None


def torch_channel(gpu: dict[str, Any], rocm_whl: str = DEFAULT_ROCM_WHL) -> str:
    """Map a GPU descriptor to a PyTorch wheel channel.

    Mirrors the arch table historically embedded in scripts/install_pytorch.sh.
    NVIDIA: Blackwell/Hopper(>=9) -> cu128 ; Ampere/Ada(8) -> cu121 ;
    Volta/Turing/Pascal(6-7) -> cu118 ; older/unknown -> cpu.
    """
    vendor = (gpu or {}).get("vendor", "none")
    if vendor == "amd":
        return rocm_whl
    if vendor != "nvidia":
        return "cpu"
    major = _compute_major(gpu)
    if major is None:
        return "cpu"
    if major >= 9:
        return "cu128"
    if major >= 8:
        return "cu121"
    if major >= 6:
        return "cu118"
    return "cpu"


def ollama_tuning(gpu: dict[str, Any]) -> dict[str, Any]:
    """Derive Ollama server env from VRAM.

    The 16 GB ceiling is the design target (see CLAUDE.md). On a 16 GB card,
    4 parallel slots quadrupled the KV reservation and forced partial CPU
    offload — so NUM_PARALLEL scales with VRAM, not a baked constant.

    FLASH_ATTENTION is suppressed when no GPU is present or VRAM is unreported
    (the degraded path below), since it only helps on a real GPU.
    """
    vendor = (gpu or {}).get("vendor", "none")
    vram = (gpu or {}).get("vram_mb") or 0
    if vendor not in ("nvidia", "amd") or vram <= 0:
        return {
            "NUM_PARALLEL": 1,
            "KV_CACHE_TYPE": "q8_0",
            "FLASH_ATTENTION": 0,
            "MAX_LOADED_MODELS": 1,
            "KEEP_ALIVE": "15m",
            "VULKAN": 0,
        }
    # >= ~19.5 GB ⇒ 24 GB-class cards (RTX 3090/4090): headroom for 2 parallel
    # slots + 2 loaded models. 16 GB-class falls through to single-slot.
    if vram >= 20000:
        num_parallel = 2
        max_loaded = 2
    else:
        num_parallel = 1
        max_loaded = 1
    # Force CUDA on NVIDIA (VULKAN=0) so a CUDA-init failure can't silently
    # fall back to the slower Vulkan backend. AMD legitimately uses Vulkan.
    vulkan = 0 if vendor == "nvidia" else 1
    return {
        "NUM_PARALLEL": num_parallel,
        "KV_CACHE_TYPE": "q8_0",
        "FLASH_ATTENTION": 1,
        "MAX_LOADED_MODELS": max_loaded,
        "KEEP_ALIVE": "15m",
        "VULKAN": vulkan,
    }


def model_tier(ram_gb: float, gpu: dict[str, Any], arch: str) -> dict[str, str]:
    """Pick chat + embed models for the host.

    Mirrors start.sh's bootstrap tiers: <=8 GB RAM or ARM -> 1B chat model;
    otherwise the standard 8B-class chat model. Embed model is constant.

    `gpu` is reserved for a future VRAM-aware quantisation tier; today's
    selection is RAM/arch-driven only.
    """
    arch = (arch or "").lower()
    is_arm = arch in ("aarch64", "arm64")
    if is_arm or (0 < (ram_gb or 0) <= 8):
        return {"chat": "llama3.2:1b", "embed": "nomic-embed-text"}
    return {"chat": "llama3.1:8b", "embed": "nomic-embed-text"}


def policy_fingerprint(hardware: dict[str, Any]) -> str:
    """Stable short hash of the *decisions* (not raw hardware) so an env only
    rebuilds when a decision actually changes. Folded into reconciler hashes.
    """
    gpu = (hardware or {}).get("gpu", {}) or {}
    arch = (hardware or {}).get("arch", "")
    ram_gb = ((hardware or {}).get("ram", {}) or {}).get("total_gb", 0)
    decisions = "|".join([
        f"torch={torch_channel(gpu)}",
        f"ollama_np={ollama_tuning(gpu)['NUM_PARALLEL']}",
        f"tier={model_tier(ram_gb, gpu, arch)['chat']}",
    ])
    return "hwfp:" + hashlib.sha256(decisions.encode("utf-8")).hexdigest()[:16]


def _is_stale_profile(profile: dict[str, Any]) -> bool:
    """A cached hardware.json is stale if it lacks a decision-critical field a
    current detector would provide.

    Today that field is ``gpu.compute_cap`` (added 2026-06-14): an NVIDIA GPU
    cached by an older detector has no compute_cap, which would make
    ``torch_channel`` silently fall back to ``cpu`` on a perfectly good GPU —
    the exact silent-CPU failure this module exists to prevent.
    """
    gpu = (profile or {}).get("gpu", {}) or {}
    if gpu.get("vendor") == "nvidia" and not gpu.get("compute_cap"):
        return True
    # Extend here whenever torch_channel/ollama_tuning/model_tier start
    # depending on a new field a current detector provides but an old cache lacks.
    return False


def _load_hardware() -> dict[str, Any]:
    """Read cached hardware.json if present AND current, else detect live.

    Trusting a stale cache (missing fields a newer detector adds) would corrupt
    every downstream provisioning decision, so a stale profile is rejected in
    favor of a live probe.
    """
    import os
    from backend.services.hardware_detector import HardwareDetector
    det = HardwareDetector()
    path = os.environ.get("GUAARDVARK_HARDWARE_JSON",
                          os.path.expanduser("~/.guaardvark/hardware.json"))
    profile = det.read_profile(path)
    if profile and not _is_stale_profile(profile):
        return profile
    return det.detect()


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m backend.services.hardware_policy <key>`

    Keys: torch_channel | ollama_env | model_tier | fingerprint
    Prints a shell-consumable value to stdout.
    """
    import sys
    args = argv if argv is not None else sys.argv[1:]
    key = args[0] if args else ""
    hw = _load_hardware()
    gpu = hw.get("gpu", {}) or {}
    if key == "torch_channel":
        print(torch_channel(gpu))
    elif key == "fingerprint":
        print(policy_fingerprint(hw))
    elif key == "ollama_env":
        for k, v in ollama_tuning(gpu).items():
            print(f'Environment="OLLAMA_{k}={v}"')
    elif key == "model_tier":
        ram_gb = (hw.get("ram", {}) or {}).get("total_gb", 0)
        t = model_tier(ram_gb, gpu, hw.get("arch", ""))
        print(f"{t['chat']}\t{t['embed']}")
    else:
        print(f"unknown key: {key!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
