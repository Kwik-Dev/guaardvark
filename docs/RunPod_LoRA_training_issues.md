# RunPod LoRA Training — Known Issues

Status: **Open** — input data path is scaffolding; only the network-volume path works today.
Date: 2026-09-02.

> Companion to `docs/RunPod_LoRA_training.md`. That file documents the intended
> architecture; this file tracks the gap between the design and the shipped code,
> specifically around how training data gets from a local PC to the pod.

---

## [OPEN] Training data cannot be pushed from a local PC to the pod

- **Status:** Open — no fix applied.
- **Area:** `plugins/runpod_lora_trainer/remote_trainer.py` (`_stage_inputs`),
  `plugins/runpod_lora_trainer/pod/handler.py` (`_resolve_manifest`),
  `backend/config.py` (`RUNPOD_S3_*`).

### Symptom / repro

A local Guaardvark run cannot automatically transfer reference images + captions to
the RunPod serverless pod. The client sends only **local filesystem paths** in the
`dataset_manifest`; the pod treats those paths as files already present on the pod.

### Root cause

The input half of the transfer is unimplemented scaffolding:

- **Client** (`remote_trainer.py:296-310` `_stage_inputs`) does **not** upload bytes
  anywhere. It builds `{"image_path": str(Path(p).resolve())}` entries and ships them
  in the job payload. The S3 branch only logs `"S3 staging configured"` and still
  returns local paths — the S3 creds (`RUNPOD_S3_ENDPOINT_URL/ACCESS_KEY/SECRET_KEY`,
  `config.py:89-91`) are dead code for inputs.
- **Pod** (`handler.py:52-60` `_resolve_manifest`) reads `image_path` as a local file
  and **deliberately does not download URLs**:
  > "This deliberately does NOT download arbitrary URLs in this scaffolding — the
  > volume-shared path is the supported path. Extend to fetch URLs when a public-bucket
  > staging mode is added."

So neither of the two documented options (S3 staging, presigned URLs) is actually
wired for inputs. The **output** half is complete (pod uploads the trained
`.safetensors` via `upload_file_to_bucket`; client pulls it back with boto3
`_download_s3`).

### Workarounds that work today

1. **Shared RunPod Network Volume (matches the code).** Mount a Network Volume at the
   same path on both the local machine and the pod; place reference images there
   locally; the pod reads them at runtime. No code change needed.
2. **Manual S3 upload (out of band).** Copy images to a bucket the pod can reach and
   pass `s3://` paths in the manifest — but the pod's `_resolve_manifest` currently
   treats `image_path` as a local file and does not download `s3://` URLs, so this
   still needs a code change.

### Proposed fix (closes the loop end to end)

Implement the input transfer the comments describe:

1. In `_stage_inputs`, actually upload local images to the configured S3 bucket (or
   generate presigned URLs) and emit `s3://` / `https://` URLs in the manifest instead
   of local paths.
2. In the pod's `_resolve_manifest`, download `s3://` / `http(s)` URLs to a temp dir
   before training (extend the "public-bucket staging mode" the comment mentions).
3. Keep the network-volume path as a fallback when no S3 creds are set.

### Related

- `docs/RunPod_LoRA_training.md` — intended architecture (push/poll/ingest).
- `plugins/runpod_lora_trainer/pod/Dockerfile` — image bundles **code only** (handler,
  runner, `scripts/`); no training data is baked into the image.
- `plugins/runpod_lora_trainer/pod/handler.py` — also has a latent bug: `_resolve_manifest`
  references `manifest` / `inp` / `subject_id` / `prompts` that are not passed in
  (stale/truncated scaffolding), so the URL-download mode is not just unimplemented but
  would not run as written.
