"""RunPod serverless worker handler for the runpod_lora_trainer plugin.

This runs INSIDE the RunPod pod. It receives a job payload from the client
(plugins/runpod_lora_trainer/remote_trainer.py), trains the LoRA using the same
runner logic as the local trainer, uploads the resulting .safetensors to an
S3-compatible bucket (or returns a presigned URL), and returns a URL the client
downloads back into data/training/loras/.

The pod image is built from plugins/runpod_lora_trainer/pod/Dockerfile and
deployed as a Serverless endpoint on RunPod. It expects the dataset manifest the
client staged: either local network-volume paths or presigned URLs.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import runpod
from runpod.serverless.utils import upload_file_to_bucket

logger = logging.getLogger(__name__)


def _bucket_creds() -> dict:
    """Build bucket creds from env for upload_file_to_bucket. Falls back to the
    RunPod-provided S3 gateway if the operator set GUAARDVARK_RUNPOD_S3_*."""
    endpoint = (
        os.environ.get("GUAARDVARK_RUNPOD_S3_ENDPOINT_URL")
        or os.environ.get("BUCKET_ENDPOINT_URL")
    )
    access = (
        os.environ.get("GUAARDVARK_RUNPOD_S3_ACCESS_KEY")
        or os.environ.get("BUCKET_ACCESS_KEY_ID")
    )
    secret = (
        os.environ.get("GUAARDVARK_RUNPOD_S3_SECRET_KEY")
        or os.environ.get("BUCKET_SECRET_ACCESS_KEY")
    )
    if endpoint and access and secret:
        return {
            "endpointUrl": endpoint,
            "accessId": access,
            "accessSecret": secret,
        }
    return {}


def _resolve_manifest(manifest: dict) -> list[str]:
    """Turn a dataset manifest into a list of local image paths on the pod.

    The client stages inputs into a network volume (path manifest) or as URLs.
    This deliberately does NOT download arbitrary URLs in this scaffolding —
    the volume-shared path is the supported path. Extend to fetch URLs when a
    public-bucket staging mode is added.
    """
    images = manifest.get("images") or []
    paths = []
    for entry in images:
        if isinstance(entry, dict):
            p = entry.get("image_path")
        else:
            p = entry
        if p:
            paths.append(p)
    return paths


def handler(job) -> dict:
    inp = job["input"]
    subject_id = inp.get("subject_id")
    subject_name = inp.get("subject_name", "subject")
    trigger_word = inp.get("trigger_word", subject_name)
    backend = inp.get("backend", "zimage")
    base_model_id = inp.get("base_model_id")
    resolution = int(inp.get("resolution", 512))
    rank = int(inp.get("rank", 16))
    alpha = int(inp.get("alpha", 16))
    lr = float(inp.get("learning_rate", 1.0e-4))
    steps = int(inp.get("steps", 400))
    prompts = inp.get("image_prompts") or []

    image_paths = _resolve_manifest(inp.get("dataset_manifest") or {})

    # Train into a temp output dir.
    workdir = Path(tempfile.mkdtemp(prefix="guaardvark_lora_"))
    out_file = workdir / f"{subject_name or subject_id}_lora.safetensors"

    # Invoke the shared runner. This mirrors what the local trainer daemon runs;
    # the pod image bundles a copy of the runner script (see Dockerfile).
    try:
        from runner import run_training  # provided in the pod image
    except ImportError:
        raise RuntimeError(
            "Pod runner missing. Ensure the pod image bundles runner.py "
            "(the run_training() entrypoint)."
        )

    run_training(
        subject_id=subject_id,
        subject_name=subject_name,
        trigger_word=trigger_word,
        ref_image_paths=image_paths,
        output_path=str(out_file),
        backend=backend,
        base_model_id=base_model_id,
        resolution=resolution,
        rank=rank,
        alpha=alpha,
        learning_rate=lr,
        steps=steps,
        image_prompts=prompts,
    )

    # Upload the result and return a URL the client downloads.
    bucket_creds = _bucket_creds()
    if bucket_creds:
        url = upload_file_to_bucket(
            out_file.name, str(out_file), bucket_creds
        )
    else:
        # No bucket: fall back to a local path (client would need to fetch via
        # the network volume). Rare — prefer configuring the bucket.
        url = f"file://{out_file}"

    return {
        "status": "success",
        "lora_url": url,
        "remote_job_id": str(job.get("id", "")),
    }


runpod.serverless.start({"handler": handler})
