"""Pod-side training entrypoint for the runpod_lora_trainer plugin.

Wraps the local trainer daemon logic (plugins/lora_trainer/scripts/) into a
single callable ``run_training()`` that the pod's ``handler.py`` invokes. The
pod image bundles a copy of the scripts (see Dockerfile) so the pod runs the
exact same training code as a local run — no forked logic.

The local scripts are daemon-style modules exposing ``_do_load(cmd)`` and
``_do_train(cmd)`` over a JSON-lines protocol. Here we call those same functions
directly with the same ``cmd`` dicts, so behavior is identical to the local
trainer.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the bundled scripts dir is importable. The Dockerfile copies
# plugins/lora_trainer/scripts/ into ./scripts/ inside the image.
_SCRIPTS = Path(__file__).resolve().parent / "scripts"
if _SCRIPTS.is_dir():
    sys.path.insert(0, str(_SCRIPTS))


def _default_steps(backend: str, n_refs: int) -> int:
    """Mirror RealLoraTrainer's default step budget when the client omits steps."""
    if backend == "zimage":
        return min(1200, max(400, n_refs * 80))
    return min(1500, max(400, n_refs * 100))


def run_training(
    *,
    subject_id: int,
    subject_name: str,
    trigger_word: str | None = None,
    ref_image_paths: list[str],
    output_path: str,
    backend: str = "zimage",
    base_model_id: str | None = None,
    resolution: int = 512,
    rank: int = 16,
    alpha: int = 16,
    learning_rate: float = 1.0e-4,
    steps: int | None = None,
    image_prompts: list[str] | None = None,
) -> dict:
    """Train a LoRA and write a ``.safetensors`` to ``output_path``.

    Mirrors the local trainer's load→train protocol. Raises ``RuntimeError`` on
    failure; returns the trainer result dict on success.
    """
    if backend == "zimage":
        import run_zimage_trainer as trainer
        model_id = base_model_id or "Tongyi-MAI/Z-Image-Turbo"
    elif backend == "sdxl":
        import run_trainer as trainer
        model_id = base_model_id or "stabilityai/stable-diffusion-xl-base-1.0"
    else:
        raise RuntimeError(f"unsupported backend: {backend}")

    # 1) Load the pipeline (same op the local daemon runs).
    load = trainer._do_load({"model_id": model_id})
    if not load.get("ok"):
        raise RuntimeError(load.get("error", "load failed"))

    # 2) Train (same op the local daemon runs).
    token = (trigger_word or "").strip() or subject_name
    if steps is None:
        steps = _default_steps(backend, len(ref_image_paths))
    params = {
        "subject_id": subject_id,
        "subject_name": subject_name,
        "ref_image_paths": [str(Path(p).resolve()) for p in ref_image_paths],
        "output_path": str(Path(output_path).resolve()),
        "rank": int(rank),
        "alpha": int(alpha),
        "steps": int(steps),
        "learning_rate": float(learning_rate),
        "resolution": int(resolution),
        "instance_prompt": f"a photo of {token}",
        "image_prompts": list(image_prompts or []),
    }
    train = trainer._do_train({"params": params})
    if not train.get("ok"):
        raise RuntimeError(train.get("error", "train failed"))
    return train
