"""Remote LoRA trainer driver for the runpod_lora_trainer plugin.

Trains character/environment/prop LoRAs on a remote RunPod serverless endpoint
instead of the local GPU. It is an ALTERNATIVE to the local `lora_trainer`
plugin (manifest `excludes: ["lora_trainer"]`); when this plugin is enabled the
local trainer is disabled and `lora_trainer_tasks` routes here.

Public API matches RealLoraTrainer.train_subject_lora so the Celery wiring can
swap backends without touching the caller. The result dict shape is identical
(`status`, `lora_path`, `lora_version`, ...) and the sidecar is written via
backend.services.media_model_registry.write_lora_sidecar with mock=False, so the
verified-real-training promotion path in lora_trainer_tasks works unchanged.

Flow (remote is a push/poll/ingest job, not a piped daemon):
  1. stage: upload reference images + .txt captions to a manifest
  2. dispatch: endpoint.run({...}) with dataset + hyperparams
  3. poll: job.status() until COMPLETED/FAILED, mapping to unified progress
  4. ingest: download the trained .safetensors from the returned URL into
     output_dir and write the local schema-v2 sidecar
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _resolve_backend(base_model_id: str | None) -> str:
    """Map a base_model_id to the RunPod pod's train backend.

    Mirrors RealLoraTrainer._resolve_backend so the same model choices work
    remotely. The pod image decides which runner script it invokes."""
    mid = (base_model_id or "zimage-turbo").strip().lower()
    if mid in ("zimage-turbo", "zimage", "z-image-turbo", "tongyi-mai/z-image-turbo"):
        return "zimage"
    if mid in ("sdxl-legacy", "sdxl", "stable-diffusion-xl-base-1.0"):
        return "sdxl"
    if mid in ("flux-dev", "flux"):
        return "flux"
    return "zimage"


def _safe_name(subject_name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in (subject_name or "")) or "subject"


def _next_version(target_dir: Path, safe_name: str) -> int:
    v = 1
    while (target_dir / f"{safe_name}_v{v}.safetensors").exists():
        v += 1
    return v


class RemoteLoraTrainer:
    """Remote (RunPod) LoRA trainer driver.

    Real SDK dispatch requires a configured endpoint + API key. A TEST-ONLY
    mock-remote path (GUAARDVARK_RUNPOD_DRY_RUN=1 under pytest) simulates the
    full dispatch→poll→ingest wiring with a locally-copied stub, so the Celery
    flow and sidecar promotion are testable without a paid RunPod job.
    """

    def __init__(self) -> None:
        self._dry_run = False

    # ── availability ────────────────────────────────────────────────────────
    def is_available(self) -> bool:
        """True if this plugin can dispatch a real remote job, OR we're in the
        test-only dry-run mode. No local GPU probe needed."""
        if self._dry_run:
            return True
        from backend import config
        return bool(config.RUNPOD_ENDPOINT_ID and config.RUNPOD_API_KEY)

    # ── public API (matches RealLoraTrainer.train_subject_lora) ─────────────
    def train_subject_lora(
        self,
        *,
        subject_id: int,
        subject_name: str,
        ref_image_paths: list[str],
        output_dir: str,
        trigger_word: str | None = None,
        resolution: int = 512,
        image_prompts: list[str] | None = None,
        rank: int = 16,
        alpha: int = 16,
        learning_rate: float = 1.0e-4,
        steps: int | None = None,
        base_model_id: str | None = None,
        job_id: str | None = None,
        **_: Any,
    ) -> dict:
        if not ref_image_paths:
            return {"status": "failed", "error": "no reference images provided"}

        from backend import config
        if not self.is_available():
            return {
                "status": "failed",
                "error": (
                    "RunPod trainer unavailable: set GUAARDVARK_RUNPOD_ENDPOINT_ID "
                    "and GUAARDVARK_RUNPOD_API_KEY in .env and deploy the pod image "
                    "from plugins/runpod_lora_trainer/pod/. (Or enable dry-run for "
                    "testing.)"
                ),
            }

        backend = _resolve_backend(base_model_id)
        if backend == "flux":
            return {
                "status": "failed",
                "error": (
                    "FLUX remote training is not implemented yet. Use "
                    "base_model_id=zimage-turbo or sdxl-legacy."
                ),
            }

        token = (trigger_word or "").strip() or subject_name
        if steps is None:
            steps = min(1200, max(400, len(ref_image_paths) * 80))
        resolution = max(512, (int(resolution) // 64) * 64)

        target_dir = Path(output_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_name(subject_name)
        version = _next_version(target_dir, safe_name)
        output_path = target_dir / f"{safe_name}_v{version}.safetensors"

        prompts = list(image_prompts or [])
        if not prompts:
            prompts = [f"a photo of {token}"] * len(ref_image_paths)
        while len(prompts) < len(ref_image_paths):
            prompts.append(prompts[-1])

        try:
            if self._dry_run:
                result = self._dispatch_dry_run(output_path)
            else:
                result = self._dispatch_real(
                    subject_id=subject_id,
                    subject_name=subject_name,
                    token=token,
                    ref_image_paths=ref_image_paths,
                    output_path=output_path,
                    backend=backend,
                    prompts=prompts,
                    resolution=resolution,
                    rank=rank,
                    alpha=alpha,
                    learning_rate=learning_rate,
                    steps=steps,
                    base_model_id=base_model_id,
                    max_seconds=config.RUNPOD_MAX_JOB_SECONDS,
                    poll_interval=config.RUNPOD_POLL_INTERVAL,
                    job_id=job_id,
                )
        except Exception as e:
            logger.exception("runpod_lora_trainer: dispatch failed for subject %s", subject_id)
            return {"status": "failed", "error": str(e)}

        if not result.get("ok"):
            return {"status": "failed", "error": result.get("error", "remote train failed")}

        # Ingested artifact → write the local schema-v2 sidecar (mock=False so the
        # verified-real promotion path accepts it). Same contract as real_trainer.
        try:
            from backend.services.media_model_registry import write_lora_sidecar
            registry_base = "zimage-turbo" if backend == "zimage" else "sdxl-legacy"
            write_lora_sidecar(
                output_path,
                subject_id=subject_id,
                subject_name=subject_name,
                trigger_word=token,
                base_model_id=registry_base,
                ref_count=len(ref_image_paths),
                steps=steps,
                mock=False,
                extra={
                    "train_backend": "remote_runpod",
                    "trainer": "runpod_lora_trainer",
                    "resolution": resolution,
                    "rank": rank,
                    "alpha": alpha,
                    "remote_job_id": result.get("remote_job_id"),
                },
            )
        except Exception as e:
            logger.warning("runpod_lora_trainer: write_lora_sidecar failed: %s", e)

        return {
            "status": "ok",
            "lora_path": str(output_path),
            "lora_version": version,
            "train_backend": "remote_runpod",
        }

    # ── dispatch paths ──────────────────────────────────────────────────────
    def _dispatch_dry_run(self, output_path: Path) -> dict:
        """TEST-ONLY mock-remote: fabricate a valid-ish stub artifact locally so
        the Celery + sidecar + promotion wiring is exercised without RunPod."""
        logger.info("runpod_lora_trainer: DRY-RUN (mock remote) → %s", output_path)
        # A real LoRA is large; the mock writes a >100-byte stub so the
        # size check in the caller passes, and a sidecar marks mock=false for
        # promotion testing. This must never run outside pytest.
        import os
        if not bool(os.environ.get("PYTEST_CURRENT_TEST")):
            raise RuntimeError(
                "runpod_lora_trainer dry-run is test-only and refused outside pytest"
            )
        output_path.write_bytes(b"DRYRUN-LORA-STUB-PAYLOAD-0123456789" * 16)
        return {"ok": True, "remote_job_id": "dryrun", "status": "COMPLETED"}

    def _dispatch_real(
        self,
        *,
        subject_id: int,
        subject_name: str,
        token: str,
        ref_image_paths: list[str],
        output_path: Path,
        backend: str,
        prompts: list[str],
        resolution: int,
        rank: int,
        alpha: int,
        learning_rate: float,
        steps: int,
        base_model_id: str | None,
        max_seconds: int,
        poll_interval: int,
        job_id: str | None = None,
    ) -> dict:
        """Real RunPod SDK dispatch: stage → run → poll → download artifact."""
        from backend import config

        # 1) Stage inputs → manifest (S3 bucket if configured, else presigned URLs
        #    produced by the pod's upload_file_to_bucket are used for the result).
        manifest = self._stage_inputs(ref_image_paths, prompts)

        # 2) Dispatch via the runpod SDK (lazy import so the plugin works without
        #    the SDK installed until a remote job is actually requested).
        try:
            import runpod
        except ImportError as e:
            raise RuntimeError(
                "runpod SDK is not installed. Install it to use the RunPod trainer "
                f"(pip install runpod): {e}"
            ) from e

        runpod.api_key = config.RUNPOD_API_KEY
        endpoint = runpod.Endpoint(config.RUNPOD_ENDPOINT_ID)
        payload = {
            "input": {
                "dataset_manifest": manifest,
                "subject_id": subject_id,
                "subject_name": subject_name,
                "trigger_word": token,
                "backend": backend,
                "base_model_id": base_model_id,
                "resolution": resolution,
                "rank": int(rank),
                "alpha": int(alpha),
                "learning_rate": float(learning_rate),
                "steps": int(steps),
                "image_prompts": prompts[: len(ref_image_paths)],
            }
        }
        job = endpoint.run(payload)
        job_id = getattr(job, "id", None) or getattr(job, "job_id", None) or "unknown"
        logger.info("runpod_lora_trainer: dispatched job %s for subject %s", job_id, subject_id)

        # 3) Poll with a hard ceiling. Map remote states to progress percentages
        #    (the caller surfaces these via unified_progress). The RunPod job is a
        #    black box (no intermediate progress), so we estimate progress from
        #    elapsed time vs the max_seconds ceiling and surface an ETA.
        deadline = time.time() + max_seconds
        started = time.time()
        while time.time() < deadline:
            status = self._job_status(job)
            if status == "COMPLETED":
                artifact = self._job_output(job)  # dict or raw value with lora URL
                url = self._extract_lora_url(artifact)
                if not url:
                    raise RuntimeError(f"RunPod job {job_id} completed but returned no LoRA URL: {artifact}")
                # 4) Download the trained .safetensors into the local output path.
                self._download_artifact(url, output_path)
                return {"ok": True, "remote_job_id": job_id, "status": "COMPLETED", "artifact_url": url}
            if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                err = self._job_output(job) or {"error": "unknown"}
                raise RuntimeError(f"RunPod job {job_id} {status}: {err}")
            # Estimate progress from elapsed time. Reserve the last 10% for the
            # artifact download; cap at 90% so the UI never shows "done" early.
            elapsed = time.time() - started
            frac = min(0.9, 0.25 + 0.65 * (elapsed / max_seconds))
            pct = int(round(frac * 100))
            remaining = max(0, int(deadline - time.time()))
            if job_id:
                try:
                    from backend.utils.unified_progress_system import get_unified_progress
                    get_unified_progress().update_process(
                        job_id, pct,
                        f"Training Z-Image Turbo LoRA on RunPod — ~{remaining // 60}m {remaining % 60}s left",
                    )
                except Exception:
                    pass
            time.sleep(poll_interval)

        raise RuntimeError(f"RunPod job {job_id} exceeded {max_seconds}s ceiling")

    # The methods below are thin wrappers so the real SDK surface is contained
    # here and can be faked in tests without importing runpod.
    def _s3_client(self):
        """Build a boto3 S3 client from the configured R2/S3 creds."""
        from backend import config
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError("boto3 is required to stage inputs to S3/R2") from e
        return boto3.client(
            "s3",
            aws_access_key_id=config.RUNPOD_S3_ACCESS_KEY or None,
            aws_secret_access_key=config.RUNPOD_S3_SECRET_KEY or None,
            endpoint_url=config.RUNPOD_S3_ENDPOINT_URL or None,
        )

    def _stage_inputs(self, ref_image_paths: list[str], prompts: list[str]) -> dict:
        """Upload images (+ captions) and return a manifest.

        When S3/R2 creds are configured, upload each reference image to the
        configured bucket and emit ``s3://bucket/key`` URLs in the manifest so
        the pod can download them. Without S3 creds, fall back to local paths
        (the pod reads them from a shared network volume).
        """
        from backend import config
        manifest = []
        if config.RUNPOD_S3_ENDPOINT_URL and config.RUNPOD_S3_ACCESS_KEY:
            bucket = config.RUNPOD_OUTPUT_BUCKET or "guaardvark-loras"
            prefix = config.RUNPOD_S3_PREFIX or ""
            base = f"lora-inputs/{uuid.uuid4().hex[:8]}"
            if prefix:
                base = f"{prefix}/{base}"
            client = self._s3_client()
            for p in ref_image_paths:
                src = Path(p).resolve()
                key = f"{base}/{src.name}"
                client.upload_file(str(src), bucket, key)
                manifest.append({"image_path": f"s3://{bucket}/{key}"})
            logger.info(
                "runpod_lora_trainer: staged %d images to s3://%s/%s",
                len(ref_image_paths), bucket, base,
            )
        else:
            # Network-volume fallback: local paths the pod reads directly.
            for p in ref_image_paths:
                manifest.append({"image_path": str(Path(p).resolve())})
        return {"images": manifest, "captions": prompts}

    def _download_artifact(self, url: str, output_path: Path) -> None:
        """Fetch the trained LoRA from the job's returned URL into output_path.

        Supports s3:// (via boto3 against the configured RunPod S3 gateway) and
        http(s) presigned URLs. File:// is a test convenience."""
        if url.startswith("s3://"):
            self._download_s3(url, output_path)
            return
        if url.startswith("file://"):
            src = Path(url[len("file://"):])
            output_path.write_bytes(src.read_bytes())
            return
        import urllib.request
        logger.info("runpod_lora_trainer: downloading artifact → %s", output_path)
        with urllib.request.urlopen(url, timeout=600) as resp, open(output_path, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)

    def _download_s3(self, url: str, output_path: Path) -> None:
        from backend import config
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError("boto3 is required to pull the LoRA from RunPod S3") from e
        # s3://VOLUME_ID/path/to/lora.safetensors
        rest = url[len("s3://"):]
        bucket, _, key = rest.partition("/")
        client = boto3.client(
            "s3",
            aws_access_key_id=config.RUNPOD_S3_ACCESS_KEY or None,
            aws_secret_access_key=config.RUNPOD_S3_SECRET_KEY or None,
            endpoint_url=config.RUNPOD_S3_ENDPOINT_URL or None,
        )
        logger.info("runpod_lora_trainer: downloading s3://%s/%s → %s", bucket, key, output_path)
        client.download_file(bucket, key, str(output_path))

    def _job_status(self, job: Any) -> str:
        return str(getattr(job.status(), "value", job.status())).upper()

    def _job_output(self, job: Any) -> Any:
        return job.output()

    def _extract_lora_url(self, artifact: Any) -> Optional[str]:
        if isinstance(artifact, dict):
            for key in ("lora_url", "artifact_url", "url", "download_url"):
                val = artifact.get(key)
                if isinstance(val, str) and val.startswith(("http", "s3://", "file://")):
                    return val
        if isinstance(artifact, str) and artifact.startswith(("http", "s3://", "file://")):
            return artifact
        return None


_TRAINER = RemoteLoraTrainer()
