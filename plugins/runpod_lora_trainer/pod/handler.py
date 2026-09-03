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
import uuid
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


def _bucket_name() -> str:
    """The S3/R2 bucket the pod uploads the trained LoRA to."""
    return (
        os.environ.get("GUAARDVARK_RUNPOD_OUTPUT_BUCKET")
        or os.environ.get("BUCKET_NAME")
        or "guaardvark-loras"
    )


def _bucket_prefix() -> str:
    """Optional parent folder (prefix) in the bucket for a project/customer."""
    return (
        os.environ.get("GUAARDVARK_RUNPOD_S3_PREFIX", "")
        or os.environ.get("BUCKET_PREFIX", "")
    ).strip().strip("/")


def _download_input(url: str) -> str:
    """Download an s3:// or http(s) URL to a temp file and return the local path.

    s3:// is fetched with boto3 against the configured S3/R2 gateway; http(s)
    URLs are streamed with urllib. Local paths are returned unchanged.
    """
    dest = Path(tempfile.mkdtemp(prefix="guaardvark_input_")) / Path(url.split("/")[-1])
    if url.startswith("s3://"):
        import boto3
        rest = url[len("s3://"):]
        bucket, _, key = rest.partition("/")
        client = boto3.client(
            "s3",
            aws_access_key_id=(
                os.environ.get("GUAARDVARK_RUNPOD_S3_ACCESS_KEY")
                or os.environ.get("BUCKET_ACCESS_KEY_ID")
            ),
            aws_secret_access_key=(
                os.environ.get("GUAARDVARK_RUNPOD_S3_SECRET_KEY")
                or os.environ.get("BUCKET_SECRET_ACCESS_KEY")
            ),
            endpoint_url=(
                os.environ.get("GUAARDVARK_RUNPOD_S3_ENDPOINT_URL")
                or os.environ.get("BUCKET_ENDPOINT_URL")
            ),
        )
        client.download_file(bucket, key, str(dest))
    else:
        import urllib.request
        with urllib.request.urlopen(url, timeout=600) as resp, open(dest, "wb") as f:
            f.write(resp.read())
    return str(dest)


def _resolve_manifest(manifest: dict) -> list[str]:
    """Turn a dataset manifest into a list of local image paths on the pod.

    The client stages inputs either as ``s3://`` / ``http(s)`` URLs (downloaded
    here to a temp dir) or as local network-volume paths (used as-is).
    """
    images = manifest.get("images") or []
    paths = []
    for entry in images:
        if isinstance(entry, dict):
            p = entry.get("image_path")
        else:
            p = entry
        if not p:
            continue
        if p.startswith(("s3://", "http://", "https://")):
            paths.append(_download_input(p))
        else:
            paths.append(p)
    return paths


def _s3_client():
    """Build a boto3 S3 client from the pod's env creds (R2/S3 gateway)."""
    import boto3
    return boto3.client(
        "s3",
        aws_access_key_id=(
            os.environ.get("GUAARDVARK_RUNPOD_S3_ACCESS_KEY")
            or os.environ.get("BUCKET_ACCESS_KEY_ID")
        ),
        aws_secret_access_key=(
            os.environ.get("GUAARDVARK_RUNPOD_S3_SECRET_KEY")
            or os.environ.get("BUCKET_SECRET_ACCESS_KEY")
        ),
        endpoint_url=(
            os.environ.get("GUAARDVARK_RUNPOD_S3_ENDPOINT_URL")
            or os.environ.get("BUCKET_ENDPOINT_URL")
        ),
    )


def _smoke_test() -> dict:
    """In-service smoke test: verify wiring + S3 read/write, no training.

    Triggered when the job input has ``"smoke": true``. Checks that the runner
    scripts import, then writes a tiny object to the S3/R2 bucket and reads it
    back (and deletes it). Returns a per-check result dict.
    """
    results: dict = {}

    # 1. Wiring: runner scripts import + expose the trainer API.
    try:
        import runner  # noqa: F401
        import run_zimage_trainer  # noqa: F401
        import run_trainer  # noqa: F401
        for mod in (run_zimage_trainer, run_trainer):
            assert hasattr(mod, "_do_load") and hasattr(mod, "_do_train")
        results["wiring"] = "ok"
    except Exception as e:  # noqa: BLE001
        results["wiring"] = f"fail: {e}"

    # 2. S3 read/write: put a tiny object, read it back, delete it.
    try:
        if not _bucket_creds():
            results["s3"] = "skip: no bucket creds configured"
        else:
            client = _s3_client()
            bucket = _bucket_name()
            prefix = _bucket_prefix() or ""
            key = f"{prefix}/smoke-test/{uuid.uuid4().hex}.txt" if prefix else f"smoke-test/{uuid.uuid4().hex}.txt"
            body = b"guaardvark smoke test"
            client.put_object(Bucket=bucket, Key=key, Body=body)
            got = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            client.delete_object(Bucket=bucket, Key=key)
            results["s3"] = "ok" if got == body else "fail: read/write mismatch"
    except Exception as e:  # noqa: BLE001
        results["s3"] = f"fail: {e}"

    ok = all(v == "ok" for v in results.values())
    return {"status": "success" if ok else "failed", "smoke": results}


def handler(job) -> dict:
    inp = job["input"]

    # In-service smoke test: verify wiring + S3 read/write, no training.
    if inp.get("smoke"):
        return _smoke_test()

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
            out_file.name, str(out_file), bucket_creds,
            bucket_name=_bucket_name(),
            prefix=_bucket_prefix() or None,
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


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
