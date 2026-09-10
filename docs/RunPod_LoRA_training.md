  - https://z-image-turbo.com/lora-training
- https://medium.com/diffusion-doodles/how-to-train-a-lora-ostris-ai-toolkit-44216331056e
- https://dev.to/gary_yan_86eb77d35e0070f5/best-practices-for-training-lora-models-with-z-image-complete-2026-guide-4p7h
- https://occulticsiesta.hateblo.jp/entry/2026/03/17/204218
- https://www.reddit.com/r/StableDiffusion/comments/1q8nknf/ltx2_lora_training_docker_imagerunpod/

- https://onlinegamernikki.com/runpod-lora-learning-howto

# Remote LoRA Training on RunPod — Setup & Reference

Status: **Implemented** (was "design only" — see history note below).
Date: 2026-08-26 (doc created) · updated to reflect shipped code.

> **History note:** this file began as a design/research artifact. The RunPod
> trainer has since been **implemented** in `plugins/runpod_lora_trainer/`
> (commit `1548b64 feat(lora): add RunPod remote LoRA trainer as an alternative
> plugin`). The design sections below are retained for context but the **Setup**
> section reflects the actual shipped code.

---

## 1. What it is

An **alternative to the local `lora_trainer` plugin** that trains
character/environment/prop LoRAs on a **remote RunPod serverless GPU endpoint**
instead of the local machine. Enabling it **disables the local trainer** via the
manifest `excludes: ["lora_trainer"]`. It holds **no local GPU** (no VRAM claim,
no local daemon, nothing to evict).

## 2. Architecture

```
Guaardvark (client)                          RunPod (serverless pod)
remote_trainer.py  ── endpoint.run(payload) ─▶  pod/handler.py
   │  dispatch                                  │  run_training()  ← runner.py
   │  poll job.status()                         │  (same code as local trainer)
   └─ download .safetensors ◀── lora_url ───────┘  upload to S3 bucket
```

Flow is a **push/poll/ingest job** (not a piped daemon like the local trainer):

1. **stage** — upload ref images + `.txt` captions into a manifest
2. **dispatch** — `endpoint.run({...})` with dataset + hyperparams
3. **poll** — `job.status()` until `COMPLETED`/`FAILED`, mapped to unified progress
4. **ingest** — download the trained `.safetensors` from the returned URL into
   `output_dir`, write the local schema-v2 sidecar

`RemoteLoraTrainer.train_subject_lora(...)` has the **same public API and result
shape** as `RealLoraTrainer.train_subject_lora`, so the Celery wiring
(`lora_trainer_tasks`) swaps backends without touching the caller.

## 2.1 How LoRA training is processed (flowchart)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Guaardvark (local)                                                         │
│                                                                             │
│  POST /api/cast/subjects/<id>/train                                         │
│    └─ cast_library_api.train_subject()                                      │
│         ├─ ensure_vision_identity()          (Ollama vision grounding)      │
│         ├─ ensure_subject_image_captions()   (VLM .txt sidecars)            │
│         ├─ training_status = "training"                                     │
│         └─ dispatch_lora_train(subject_id)                                  │
│              └─ celery.send_task("lora_trainer.train_lora")                 │
│                   └─ lora_trainer_tasks.train_lora_task()                   │
│                        └─ _train_impl(subject_id)                          │
│                             ├─ build train_images (refs ∪ approved)         │
│                             ├─ validate_cast_training()  (pretrain gate)    │
│                             ├─ settings_for_subject()   (rank/alpha/lr/…)   │
│                             └─ backend selection                            │
│                                  └─ runpod_lora_trainer enabled? ──yes──┐   │
│                                                                         │   │
│  RemoteLoraTrainer.train_subject_lora()  ◀──────────────────────────────┘   │
│    ├─ 1. stage:  build dataset manifest (images + captions)                 │
│    ├─ 2. dispatch: endpoint.run({input: {...}})  ──────────────┐            │
│    ├─ 3. poll: job.status() until COMPLETED/FAILED             │            │
│    └─ 4. ingest: download .safetensors → output_dir            │            │
│         └─ write_lora_sidecar() (schema-v2, mock=False)        │            │
│              └─ training_status = "trained"; ensure_lora_in_comfyui()       │
└─────────────────────────────────────────────────────────────────────────────┘
                                                                              │
                                                                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  RunPod (cloud GPU server)                                                  │
│                                                                             │
│  Serverless endpoint (your pod image)                                       │
│    └─ pod/handler.py  handler(job)                                          │
│         ├─ _resolve_manifest()  → local image paths on the pod             │
│         ├─ run_training(...)    (runner.py → local trainer scripts)        │
│         │    ├─ _do_load()   load ZImagePipeline / SDXLPipeline            │
│         │    └─ _do_train()  PEFT LoRA on the GPU (diffusers/peft)         │
│         │         └─ writes .safetensors to temp dir                       │
│         └─ upload_file_to_bucket() → presigned URL (or file://)            │
│              └─ returns {status, lora_url, remote_job_id} ────────────────┐ │
└─────────────────────────────────────────────────────────────────────────────┘
                                                                              │
                                                                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Guaardvark (local) — ingest                                                │
│                                                                             │
│  _download_artifact(lora_url, output_path)                                  │
│    ├─ s3://   → boto3 against RunPod S3 gateway                            │
│    ├─ http(s) → presigned URL streaming download                           │
│    └─ file:// → test convenience                                           │
│  → LoRA saved to data/training/loras/<name>_v<N>.safetensors               │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Is RunPod a service that runs a training pod container on their GPU server?**
Yes. RunPod is a cloud GPU provider. You build a **container** (the pod image)
from the Dockerfile and deploy it as a **Serverless endpoint**; RunPod runs that
container on **their GPU servers**. Guaardvark never touches a local GPU for this
path — it dispatches a job to the endpoint, RunPod executes the training inside
the container on its GPU, and Guaardvark downloads the resulting `.safetensors`.

## 3. Prerequisites

- **RunPod account** — sign up at runpod.io (free).
- **RunPod credits** — pay-per-second, usage-based. A positive balance is
  required to actually run a job (new accounts get ~$1 promo credit; no real
  free GPU tier).
- **RunPod API key** — Console → **Settings → API Keys → Create API Key**.
  Use a **Restricted** key scoped to your training endpoint. This is the
  `rpa_...` key used by Guaardvark's SDK (`GUAARDVARK_RUNPOD_API_KEY`).
- **Docker** — to build the pod image locally before pushing to a registry.
- **A container registry** — Docker Hub (`docker.io`), GHCR, or any registry
  you can push to. Needed to host the pod image so RunPod can pull it.
- **Cloudflare R2** (optional but recommended) — an S3-compatible bucket for
  staging training inputs and retrieving the trained LoRA artifact.

## 4. Setup

### 4.1 Docker — build & push the pod image

The pod image is built from `plugins/runpod_lora_trainer/pod/Dockerfile` and
deployed as a **Serverless endpoint** on RunPod. The image bundles:
- `handler.py` — the RunPod worker entrypoint
- `runner.py` — a `run_training()` wrapper that reuses the **local trainer
  scripts** (`plugins/lora_trainer/scripts/run_trainer.py` /
  `run_zimage_trainer.py`), so the pod runs the exact same training code as a
  local run

**Base image:** `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` (CUDA 12 —
supports Ada/Ampere GPUs and the Z-Image/diffusers stack).

**1. Log in to your registry** (Docker Hub example):
```bash
docker login
```

**2. Build** from the `plugins/` directory (so both the pod files and the local
scripts are in the Docker build context):
```bash
cd plugins
docker build -f runpod_lora_trainer/pod/Dockerfile \
    -t docker.io/<username>/guaardvark-lora-trainer:latest .
```

**3. Push:**
```bash
docker push docker.io/<username>/guaardvark-lora-trainer:latest
```

**Private image note:** if the repo is **private**, the RunPod endpoint needs
credentials to pull it — see §4.3 (container-registry auth). Use a Docker Hub
**Personal Access Token** (Account Settings → Personal Access Tokens) as the
registry password, not your account password.

### 4.2 Cloudflare R2 — artifact staging (optional but recommended)

R2 is S3-compatible, so it works as the bucket for staging training inputs and
retrieving the trained `.safetensors`. Create a bucket in the Cloudflare
dashboard, then generate an **R2 API token** (R2 → Manage R2 API Tokens) with
**Object Read & Write** permission on that bucket.

You'll need four values:
- **Endpoint URL** — `https://<accountid>.r2.cloudflarestorage.com`
- **Access Key ID** — the R2 token's access key
- **Secret Access Key** — the R2 token's secret
- **Bucket name** — e.g. `guaardvark-loras`

These map to the `GUAARDVARK_RUNPOD_S3_*` env vars in §4.4.

### 4.3 RunPod — API key, registry auth, endpoint

**1. API key** (for Guaardvark's SDK):
Console → **Settings → API Keys → Create API Key** → **Restricted**, scoped to
your training endpoint. This is the `rpa_...` key for `GUAARDVARK_RUNPOD_API_KEY`.

**2. Container-registry auth** (only if the pod image is **private**):
Register the registry credentials in RunPod so the endpoint can pull the image.
Via the RunPod MCP tool `runpod_create-container-registry-auth`:
- **Registry**: `docker.io` (or `index.docker.io/v1/`)
- **Username**: your Docker Hub username
- **Password**: your Docker Hub **Personal Access Token**

**3. Create the Serverless endpoint** (via `runpod_create-endpoint`):
- **imageName**: `docker.io/<username>/guaardvark-lora-trainer:latest`
- **gpuPoolIds**: e.g. `["ADA_24"]` (RTX 4090, 24GB) or `["AMPERE_24"]`
  (RTX 3090, 24GB, cheaper)
- **containerRegistryAuthId**: the auth id from step 2 (if private)
- **workersMin/workersMax**: e.g. `0` / `1` (scale to zero when idle)
- **executionTimeoutMs**: ≥ `GUAARDVARK_RUNPOD_MAX_JOB_SECONDS` (default 10800s)

Copy the returned **endpoint ID** for `GUAARDVARK_RUNPOD_ENDPOINT_ID`.

### 4.4 Configure `.env`

Add to the repo-root `.env`:

```bash
# Route training to RunPod (auto = plugin-state decides; runpod = force)
GUAARDVARK_LORA_BACKEND=auto

# RunPod endpoint + API key (required)
GUAARDVARK_RUNPOD_ENDPOINT_ID=<endpoint id from §4.3>
GUAARDVARK_RUNPOD_API_KEY=rpa_...

# Cloudflare R2 / S3-compatible bucket for artifact staging (optional)
GUAARDVARK_RUNPOD_S3_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com
GUAARDVARK_RUNPOD_S3_ACCESS_KEY=<r2 access key>
GUAARDVARK_RUNPOD_S3_SECRET_KEY=<r2 secret key>
GUAARDVARK_RUNPOD_OUTPUT_BUCKET=runpod-storage
# Optional parent folder (prefix) in the bucket for a project/customer, e.g.
# "kwiksher" → inputs under kwiksher/lora-inputs/…, outputs under kwiksher/…
GUAARDVARK_RUNPOD_S3_PREFIX=kwiksher

# Optional tuning (defaults shown)
GUAARDVARK_RUNPOD_POLL_INTERVAL=15
GUAARDVARK_RUNPOD_MAX_JOB_SECONDS=10800
```

### 4.5 Enable the plugin

In the **Plugins UI**, enable **RunPod LoRA Trainer**. This:
- **disables the local `lora_trainer`** (via the manifest `excludes`),
- routes training to RunPod (plugin state drives backend selection in
  `_train_impl`).

### 4.6 Specifying input data

**Input data is NOT set in `.env`.** The training images come from the **cast
Subject** in the Guaardvark UI:

- **Reference images** — uploaded for the character (`Subject.ref_image_paths`)
- **Approved samples** — generated Character-Generator samples marked approved

These are collected automatically in `_train_impl` (refs ∪ approved samples) and
passed to `RemoteLoraTrainer.train_subject_lora(...)`.

What `.env` controls is **where the input images are staged** for the pod:

| Env var | Controls |
|---|---|
| `GUAARDVARK_RUNPOD_S3_ENDPOINT_URL` / `_ACCESS_KEY` / `_SECRET_KEY` | If set, the client **uploads** each ref image to this S3/R2 bucket and the pod **downloads** it (`s3://` URLs) |
| `GUAARDVARK_RUNPOD_OUTPUT_BUCKET` | The bucket name used for staging inputs + the trained LoRA |
| *(none set)* | Fallback: client ships local paths; pod reads them from a shared **network volume** |

So: upload the character's reference images in the cast UI, and set the S3/R2
vars in `.env` to stage them to the pod. The pod endpoint must be created with
the **same** `GUAARDVARK_RUNPOD_S3_*` (or `BUCKET_*`) env vars so it can download
the staged inputs.

### 4.7 Downloading the trained LoRA

After the pod finishes training, the client downloads the `.safetensors`
automatically. The pod returns a URL in its job output; the client fetches it
into `data/training/loras/<name>_v<N>.safetensors` and writes a sidecar.

```
1. Client dispatches job:  endpoint.run(payload)
2. Client polls:          job.status() until COMPLETED
3. On COMPLETED:          artifact = job.output()  →  {"lora_url": "..."}
4. Client extracts:       url = _extract_lora_url(artifact)
5. Client downloads:      _download_artifact(url, output_path)
```

`_download_artifact` handles three URL schemes:

| Pod returns | How the client downloads | When |
|---|---|---|
| `http(s)://` presigned URL | `urllib` streams the bytes | Pod has S3/R2 bucket creds → `upload_file_to_bucket` returns a presigned URL (**your normal path** with R2) |
| `s3://bucket/key` | `boto3.download_file()` against the `RUNPOD_S3_*` gateway | Pod writes to a RunPod network-volume S3 gateway |
| `file://<path>` | Reads the local file directly | Pod has **no** bucket creds AND the network volume is mounted at the same path on your machine (rare fallback) |

**Note:** the pod runs on **RunPod's cloud GPU servers**, not your machine. The
`file://` path is on a shared **network volume**, not your local disk — it only
works if that volume is mounted locally. With R2 configured, you'll use the
`http(s)` presigned-URL path, which needs no extra setup.

## 5. Config reference (`backend/config.py`)

| Env var | Default | Purpose |
|---|---|---|
| `GUAARDVARK_RUNPOD_ENDPOINT_ID` | — | RunPod serverless endpoint ID |
| `GUAARDVARK_RUNPOD_API_KEY` | — | RunPod API key (`rpa_...`) |
| `GUAARDVARK_RUNPOD_S3_ENDPOINT_URL` / `_ACCESS_KEY` / `_SECRET_KEY` | — | S3-compatible bucket (e.g. Cloudflare R2) for artifact staging |
| `GUAARDVARK_RUNPOD_OUTPUT_BUCKET` | `guaardvark-loras` | Bucket name for the trained LoRA artifact |
| `GUAARDVARK_RUNPOD_S3_PREFIX` | — | Optional parent folder (prefix) in the bucket for a project/customer (e.g. `kwiksher`) |
| `GUAARDVARK_RUNPOD_POLL_INTERVAL` | 15s | Poll cadence |
| `GUAARDVARK_RUNPOD_MAX_JOB_SECONDS` | 10800 (3h) | Hard job ceiling |
| `GUAARDVARK_LORA_BACKEND` | `auto` | `mock`/`real`/`auto`/`runpod` |
| `GUAARDVARK_DOCKERHUB_USERNAME` / `_TOKEN` | — | Docker Hub registry auth for a private pod image (read by setup tooling) |

## 6. Backend selection

In `_train_impl` (`backend/tasks/lora_trainer_tasks.py`):
- `GUAARDVARK_LORA_BACKEND=runpod` → remote
- `auto` → remote if `plugin_manager.is_effectively_enabled("runpod_lora_trainer")`
  (plugin state is the source of truth)

## 7. Testing (dry-run, no RunPod cost)

`GUAARDVARK_RUNPOD_DRY_RUN=1` under pytest simulates the full
dispatch→poll→ingest wiring with a local stub artifact — so the Celery flow and
sidecar promotion are testable without a paid RunPod job. Refused outside pytest.

## 8. Known gaps / limitations

- **Pod dependency versions may need pinning** — the pod `requirements.txt`
  pulls unpinned `diffusers`/`peft`/`transformers`/`accelerate` on top of
  `pytorch:2.5.1-cuda12.4`. If the image build hits a `pip` resolution conflict,
  pin versions to match the local `backend/venv` stack (torch 2.13, diffusers
  0.39, peft 0.20) or a known-good combo.
- **FLUX not implemented** — `_resolve_backend` maps `flux`, but
  `train_subject_lora` returns a "not implemented" failure. Only `zimage-turbo`
  and `sdxl-legacy` work.
- **Lazy SDK imports** — `runpod` and `boto3` are imported only when a real
  remote job is requested, so the plugin works without them installed until
  needed.

## 9. Sources

- https://github.com/runpod/runpod-python
- https://docs.runpod.io/serverless/sdks
- https://docs.runpod.io/get-started/api-keys
- https://docs.runpod.io/serverless/workers/overview
- https://docs.runpod.io/serverless/workers/create-dockerfile
- https://docs.runpod.io/storage/s3-api
- https://docs.runpod.io/pods/pricing
