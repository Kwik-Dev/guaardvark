# Remote LoRA Training on RunPod — Design & Research

Status: **Design / research only — not yet implemented.**
Date: 2026-08-26

This document captures a design conversation about using a remote GPU training
service (RunPod) from Guaardvark for LoRA training, plus research on the RunPod
Python SDK. It is a planning artifact, not a spec of shipped code.

---

## 1. Is RunPod currently supported? — No

There is **zero remote-GPU / SSH / cloud code** in the codebase. The only RunPod
reference is a research link in `docs/LoRA_memo.md`. LoRA training runs **only
on the local machine**:

- **Executor**: Celery task `lora_trainer.train_lora` → `_train_impl()` in
  `backend/tasks/lora_trainer_tasks.py:24`
- **Driver**: `RealLoraTrainer` (`plugins/lora_trainer/real_trainer.py`) spawns a
  local subprocess daemon (`run_trainer.py` / `run_zimage_trainer.py`) over
  stdin/stdout JSON
- **Selection**: `GUAARDVARK_LORA_BACKEND` env (`mock|real|auto`) at
  `lora_trainer_tasks.py:122`
- **GPU gating**: local-only `gpu_session()` in
  `backend/services/gpu_resource_policy.py:344` (in-process gate + nvidia-smi probe)
- **Artifacts**: written locally to `data/training/loras/` via `_output_dir()`
  (`lora_trainer_tasks.py:19`)

The design is **cleanly extensible**: `train_subject_lora()` (real_trainer.py:273)
is a stable public API that already matches the mock, so a remote trainer is a
drop-in swap.

---

## 2. Design: `RemoteLoraTrainer` (RunPod) as a new backend

The abstraction boundary is the `train_subject_lora()` contract. Add a third
backend value and a new driver implementing the same method.

### 2.1 Backend selection — `lora_trainer_tasks.py:122`

Extend `GUAARDVARK_LORA_BACKEND` with `runpod` (and allow `auto` to prefer remote
when configured):

```
backend == "runpod"  → use RemoteLoraTrainer
```

Lazy import, same as `RealLoraTrainer`.

### 2.2 New driver — `plugins/lora_trainer/remote_trainer.py`

A class exposing the **same signature** as `RealLoraTrainer.train_subject_lora()`
(subject_id, name, ref_image_paths, output_dir, trigger_word, resolution,
image_prompts, rank, alpha, learning_rate, steps, base_model_id) and returning the
same result dict shape (`{"status": "ok", "lora_path": ..., "lora_version": ...}`)
so `_train_impl`'s caller persists it unchanged.

**Key difference from the local daemon:** this is a full push/poll/ingest job,
not a piped subprocess. The local driver blocks on a long-lived subprocess; a
remote job must be dispatched and its artifact **pulled back**.

Proposed flow:

1. **`is_available()`** — True if a RunPod target is configured (endpoint/API key
   present). No local GPU probe.
2. **`train_subject_lora(...)`**:
   - **Stage inputs**: upload the subject's reference images + `.txt` captions
     (already built upstream) to RunPod storage (S3 bucket or network volume).
     Returns a dataset manifest URL.
   - **Dispatch**: `endpoint.run({...})` with a job payload
     `{images_uri, captions_uri, resolution, rank, alpha, lr, steps, base_model,
     trigger_word, webhook_url, output_bucket}`. Returns a `job_id`.
   - **Poll**: poll `job.status()` on an interval (configurable
     `GUAARDVARK_RUNPOD_POLL_INTERVAL`), mapping remote states → the
     unified-progress percentages the local path uses.
   - **Ingest**: when status is `done`, **download** the `.safetensors` + sidecar
     from the returned presigned URL into `output_dir` (local
     `data/training/loras/`), then write the local schema-v2 sidecar via
     `media_model_registry.write_lora_sidecar` so downstream render/inference
     works identically.
3. **`_gpu_env()` / `shutdown()`** — no-ops (no local GPU held). **Bypass
   `gpu_session()`** so no local VRAM is claimed — add a flag in `_train_impl` to
   skip the `gpu_session(JobKind.LORA_TRAIN...)` block for the remote path.

### 2.3 Configuration — `backend/config.py`

Reuse the existing pattern (env overrides like `GUAARDVARK_COMFYUI_URL` at
config.py:72). Add a `GUAARDVARK_REMOTE_TRAINER` config block:

```
RUNPOD_API_KEY / GUAARDVARK_RUNPOD_API_KEY   (never logged — secrets rule)
RUNPOD_TARGET_URL                            (HTTP endpoint or pod IP:port)
RUNPOD_STORAGE_BUCKET / OUTPUT_BUCKET        (upload/download of inputs & LoRA artifact)
GUAARDVARK_LORA_BACKEND=runpod                (opt-in)
GUAARDVARK_RUNPOD_POLL_INTERVAL
```

Keys from repo-root `.env` (matching how the rest of secrets are handled). Do
**not** hardcode a pod identity.

### 2.4 Artifact contract — `run_zimage_trainer.py` / `run_trainer.py`

The remote pod runs an **uploaded copy of the same runner scripts** (they already
produce `.safetensors` + sidecar and print a JSON result). To support RunPod
without forking the training logic:

- The runner scripts get a thin `--remote` mode: read inputs from the manifest
  URI, write results to `output_dir` then **push** the `.safetensors` + sidecar
  to `output_bucket`.
- Ship a `pod_scripts/runpod_entry.sh` + `Dockerfile` under
  `plugins/lora_trainer/` so a user can build/upload the pod image once. This is
  where the real iteration goes; the backend integration only needs the HTTP +
  object-store contract.

### 2.5 Frontend

No required changes — the UI already reads `Subject.training_status` + unified
progress, which the remote path emits the same way. Optional later: a backend
indicator on the training row.

### 2.6 Files touched

| File | Change |
|---|---|
| `backend/tasks/lora_trainer_tasks.py` | select `runpod` backend; skip local `gpu_session` for remote |
| `plugins/lora_trainer/remote_trainer.py` | **new** driver (upload → dispatch → poll → ingest) |
| `backend/config.py` | `GUAARDVARK_RUNPOD_*` + `RUNPOD_TARGET_URL` resolution |
| `plugins/lora_trainer/scripts/run_trainer.py`, `run_zimage_trainer.py` | optional `--remote` mode + push-artifact |
| `plugins/lora_trainer/pod/` (Dockerfile, entry.sh, requirements) | runnable RunPod pod image |
| `docs/GUAARDVARK_GUIDE.md`, `AGENTS.md` | document the backend + env |

### 2.7 Suggested implementation order (each independently mergeable)

1. Config block + `RemoteLoraTrainer` skeleton with a **dry-run/mock-remote**
   (fake poll returning `done` + a local copy of a pre-made safetensor) — proves
   the dispatch→ingest→persist wiring and the backend env selection, under pytest.
2. Real RunPod SDK integration + upload/poll/ingest.
3. Runner scripts `--remote` mode + pod image.
4. Docs.

---

## 3. RunPod Python SDK — research notes

The `runpod` Python package (PyPI, Python 3.10+) is **two libraries in one**:

1. **API/GraphQL client** — manage pods, submit jobs to serverless endpoints,
   from your own code.
2. **Serverless worker SDK** — the code that *runs on* RunPod (a `handler(job)`
   function registered via `runpod.serverless.start(...)`).

### 3.1 Features relevant to Guaardvark LoRA training

| Feature | What it does | Use for us |
|---|---|---|
| `runpod.Endpoint("ID")` | Submit jobs to a deployed serverless GPU endpoint | **Recommended** — dispatch training, poll status |
| `endpoint.run({...})` → `Job` | Async job; `job.status()`, `job.output()` | Long-running training (async is the right mode) |
| `endpoint.run_sync(...)` | Blocks ~90s | Too short for training; skip |
| `runpod.create_pod/get_pods/stop_pod` | Spin up/manage interactive GPU pods | Alternative one-off path |
| `runpod.serverless.start({"handler": fn})` | The worker entrypoint that runs *on* the pod | Our pod image's `CMD` |
| `upload_file_to_bucket(...)` | Push a file to any S3-compatible bucket, returns presigned URL | **Upload trained `.safetensors` back to us** |
| Network-volume S3 API (boto3) | Read/write a volume without a GPU | Stage base model + dataset; avoid re-downloading |

### 3.2 Authentication — what you need

**Two separate credentials:**

1. **RunPod API key** — for the SDK (endpoint jobs, pod management). Get it:
   Console → **Settings → API Keys → Create API Key**. Since Nov 2024 keys have
   scopes (`All` / `Restricted` / `Read Only`); use **Restricted** scoped to just
   your training endpoint. Load via env:
   `runpod.api_key = os.getenv("RUNPOD_API_KEY")`.
2. **S3 API key** (separate) — for the S3-compatible gateway to network volumes.
   Console → **Settings → S3 API Keys**. Gives an access key (`user_...`) +
   secret (`rps_...`), shown once. Used with boto3 + a datacenter endpoint like
   `https://s3api-us-ks-2.runpod.io/`.

### 3.3 Do you need to sign up / pay?

- **Sign up: yes** — free, and you can create an API key immediately.
- **Pay: yes, effectively** — RunPod is **pay-per-second, usage-based** (no
  monthly subscription). New accounts get a small promo credit (~$1), but
  **running your own pod/endpoint requires a positive credit balance** (RunPod
  stops pods when balance can't cover ~10 min of runtime). There's no real free
  GPU tier.

So: **sign up, create a Restricted API key, and add a small credit balance** to
actually run training. The SDK install/config works on a free account, but
dispatching a real job needs credits.

### 3.4 Recommended architecture for Guaardvark

Given the SDK, the cleanest design is a **serverless endpoint** (not a pod):

- **Pod image** (`plugins/lora_trainer/pod/`): a Dockerfile with
  `pytorch/pytorch:2.0.1-cuda11.7` + `runpod`, `diffusers`, `peft`, `accelerate`,
  and a `handler.py` that runs our existing `run_zimage_trainer.py` /
  `run_trainer.py` logic, then `upload_file_to_bucket()` the `.safetensors` back.
- **Client side** (`RemoteLoraTrainer`): `endpoint.run({...})` with the dataset
  manifest + hyperparams, poll `job.status()`, then **download** the artifact from
  the returned presigned URL into `data/training/loras/`.
- **Network volume** for the base model so cold starts don't re-download multi-GB
  checkpoints.

This maps cleanly onto the design above: the SDK replaces the "raw HTTP"
transport option, and `upload_file_to_bucket` / presigned-URL download replaces
the object-store plumbing.

---

## 4. Open decisions (not yet resolved)

1. **Transport**: RunPod **graphQL / `runpod` python SDK** (`runpod.api`) vs raw
   **HTTP endpoints** + an **S3-compatible bucket** for artifacts. SDK is faster
   to stand up; raw HTTP avoids a new dependency and works with self-hosted
   bucket. (Research leans SDK.)
2. **Security posture**: an API key in `.env` is the baseline; consider a
   **cap/budget** (max $ per job) and an **approval gate** (outreach-style
   supervised dispatch) before any paid pod is launched. Given the existing MCP
   default-deny and supervised-outreach ethos, an approval gate is recommended.

---

## 5. Sources

- https://github.com/runpod/runpod-python (README — installation, Endpoint, Pods,
  serverless worker, upload utils)
- https://docs.runpod.io/serverless/sdks (install, config, API key env)
- https://docs.runpod.io/get-started/api-keys (API key creation/scopes)
- https://docs.runpod.io/serverless/workers/overview (workers, worker states)
- https://docs.runpod.io/serverless/workers/create-dockerfile (Dockerfile)
- https://docs.runpod.io/storage/s3-api (S3-compatible API, boto3 example)
- https://docs.runpod.io/pods/pricing (credits, storage pricing, account limits)
- https://github.com/runpod/runpod-python/blob/main/docs/serverless/utils/rp_upload.md
