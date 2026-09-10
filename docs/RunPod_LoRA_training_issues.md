```
docker build -f runpod_lora_trainer/pod/Dockerfile      -t docker.io/kwiksher/guaardvark-lora-trainer:latest .

docker push docker.io/<username>/guaardvark-lora-trainer:latest
```


# RunPod LoRA Training — Known Issues

Status: **Partially resolved** — S3/R2 URL input is now implemented; see below.
Date: 2026-09-02 (created) · updated to reflect the S3 input fix.

> Companion to `docs/RunPod_LoRA_training.md`. That file documents the intended
> architecture; this file tracks the gap between the design and the shipped code,
> specifically around how training data gets from a local PC to the pod.

---

## [RESOLVED] Training data can now be pushed from a local PC to the pod via S3/R2

- **Status:** Resolved — S3/R2 URL input implemented.
- **Area:** `plugins/runpod_lora_trainer/remote_trainer.py` (`_stage_inputs`,
  `_s3_client`), `plugins/runpod_lora_trainer/pod/handler.py` (`_resolve_manifest`,
  `_download_input`), `backend/config.py` (`RUNPOD_S3_*`).

### What was fixed

**Client** (`remote_trainer.py`): `_stage_inputs` now **uploads** each reference
image to the configured S3/R2 bucket and emits `s3://bucket/key` URLs in the
manifest (instead of local paths). A new `_s3_client()` helper builds the boto3
client from `RUNPOD_S3_*`. When no S3 creds are set, it falls back to local
paths (network-volume mode).

**Pod** (`handler.py`): `_resolve_manifest` now **downloads** `s3://` and
`http(s)` URLs to a temp dir before training (new `_download_input()` helper),
and keeps local network-volume paths as-is.

### How it works now

- **S3/R2 configured** → client uploads images to `s3://<bucket>/lora-inputs/<id>/…`,
  pod downloads them via boto3 → trains → uploads the LoRA back.
- **No S3 creds** → client ships local paths, pod reads them from a shared
  network volume (unchanged fallback).

### Requirements for the S3 path

- The pod endpoint must be created with the **same** `GUAARDVARK_RUNPOD_S3_*`
  env vars (or `BUCKET_*`) set, so the pod can download the staged inputs.
- `boto3` is required on both sides (client venv + pod image).

---

## [OPEN] Output artifact transport — S3 is optional, not mandatory

- **Status:** Open (informational) — no change needed.
- **Area:** `plugins/runpod_lora_trainer/pod/handler.py` (output),
  `plugins/runpod_lora_trainer/remote_trainer.py` (`_download_artifact`).

### Question: is S3 mandatory to retrieve the trained LoRA?

**No.** The pod's output logic returns the trained `.safetensors` via:

1. **S3** — if bucket creds are set on the pod, `upload_file_to_bucket` uploads
   it and returns a presigned `http(s)` URL.
2. **Presigned URL** — the `http(s)` URL from `upload_file_to_bucket` (client
   streams it with `urllib`).
3. **Network volume** — if no bucket creds, the pod returns `file://<path>` and
   the client reads it from the shared volume.

The client `_download_artifact` accepts all three (`s3://`, `http(s)://`,
`file://`). So S3 is **one option**, not a requirement, for the output.

### Note

The **input** path (S3 URL staging) is now implemented (see above). The output
path was already complete.

---

## Related

- `docs/RunPod_LoRA_training.md` — intended architecture (push/poll/ingest).
- `plugins/runpod_lora_trainer/pod/Dockerfile` — image bundles **code only** (handler,
  runner, `scripts/`); no training data is baked into the image.
