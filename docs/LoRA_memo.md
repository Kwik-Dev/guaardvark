Z-Imageで人物の一貫性を保つLoRAをRunPodで作る手順｜60枚・約40分・0.73ドルの実測
https://note.com/locany/n/n6c8c4d0ed696

LTX-2.3とZ-Image TurboによるI2Vワークフロ
https://physical-i.jp/ai/ltx-2-3-i2v-business-verification/


MacBook Airだけで、自作キャラのLoRAを0円で学習させた（Draw Things・4000ステップの全記録）
https://note.com/chbipoo/n/n1631a320139e


----

## Draw Things

『Mac miniでMiniMax-H3』Draw Things対応まで待てなかったのでChatGPTに頼んだら、生成環境ができた
https://note.com/sane_weasel8589/n/n50fafc452948


Draw Thingsとは？無料でMacとiPhoneに使えるローカルAI画像生成の全部 (2026年版)
https://aipicks.jp/mag/draw-things-guide-2026

----

Confirmed — **MPS is fully supported and being used right now.** The log shows exactly how it works:

```
[run_zimage_trainer] loaded on CPU ...        ← initial load (by design)
[run_zimage_trainer] staging VAE on mps for latent cache... → cached 8 latents; VAE off GPU
[run_zimage_trainer] staging text encoder on mps for prompt cache... → cached 8 prompts; TE off GPU
[run_zimage_trainer] staging transformer+LoRA on mps (res=768, rank=16, dtype=torch.bfloat16)...
[run_zimage_trainer] step 1/640 loss=0.58203   ← ACTUALLY TRAINING
```

So:
- **MPS is active** — the heavy modules (VAE, text encoder, transformer+LoRA) are each staged onto the Metal/MPS device for their compute pass, then moved off to free memory (that's the `off GPU`/`off` pattern).
- **"loaded on CPU"** just means the initial model load happened on CPU because the full pipeline can't sit in unified memory at once — it's the same memory-saving strategy the 16 GB CUDA path uses.
- **Training is running:** it's at **step 1 of 640**, loss 0.58203.

So Elara's LoRA training is genuinely in progress on MPS. The monitor will report when it completes (640 steps, then a `Elara_v*.safetensors` will be saved).


----

Here's the complete LoRA training call stack, traced through the code. It's a **Celery task → backend-selection → subprocess daemon (JSON-lines protocol) → PEFT/diffusers training loop** chain.

## Full call stack

```
Frontend "Train" button
└─ POST /api/cast/subjects/<id>/train
   └─ cast_library_api.py  train_subject()
      ├─ ensure_vision_identity(s)          # vision-ground identity (Ollama)
      ├─ ensure_subject_image_captions()    # VLM .txt sidecars
      ├─ s.training_status = "training"; commit
      └─ dispatch_lora_train(subject_id)
         └─ lora_train_dispatch.py  dispatch_lora_train()
            ├─ create unified progress job
            └─ celery.send_task("lora_trainer.train_lora", [subject_id, job_id])
               └─ lora_trainer_tasks.py  train_lora_task()   # Celery (soft 245m / hard 255m)
                  └─ train_subject_lora_for_subject(subject_id, job_id)
                     ├─ idempotency check (training_status == "training")
                     └─ _train_impl(subject_id, job_id)
                        ├─ build train_images (refs ∪ approved samples)
                        ├─ ensure_subject_image_captions()
                        ├─ validate_cast_training()          # pretrain gate
                        ├─ build_training_captions()
                        ├─ settings_for_subject()            # rank/alpha/lr/steps/res
                        ├─ assert_train_ready(base_model_id) # media_model_registry
                        └─ backend selection (GUAARDVARK_LORA_BACKEND: mock|real|auto|runpod)
                           ├─ runpod → RemoteLoraTrainer.train_subject_lora()   # plugins/runpod_lora_trainer
                           ├─ real   → RealLoraTrainer.train_subject_lora()    # plugins/lora_trainer
                           └─ mock   → mock_trainer (pytest-only, NO-MOCKS policy)
```

## Real trainer (local) — `plugins/lora_trainer/real_trainer.py`

```
RealLoraTrainer.train_subject_lora(...)
├─ _resolve_backend(base_model_id) → "zimage" | "sdxl"
├─ _ensure_proc(backend)            # spawns the daemon subprocess
│    ├─ sdxl  → venv-torch/bin/python scripts/run_trainer.py
│    └─ zimage→ backend/venv/bin/python scripts/run_zimage_trainer.py
├─ _send({"op":"load","model_id":...})     # load SDXL/ZImage pipeline
├─ _send({"op":"train","params":{...}})    # rank, alpha, steps, lr, resolution, refs, prompts
├─ write_lora_sidecar(...)                  # media_model_registry
└─ return {status:"ok", lora_path, lora_version, base_model_id}
```

The daemon is a **long-lived subprocess** speaking JSON-lines over stdin/stdout, with a watchdog (`_send` timeout) and `pdeathsig` so it dies with the Celery worker.

## Training subprocess — `scripts/run_trainer.py` (SDXL) / `run_zimage_trainer.py` (Z-Image)

```
_do_load:  StableDiffusionXLPipeline.from_pretrained(...)   # or ZImagePipeline
_do_train:
├─ freeze VAE + text_encoder + text_encoder_2
├─ unet.to(bf16); enable_gradient_checkpointing()
├─ unet = get_peft_model(unet, LoraConfig(r, alpha, target_modules=["to_q","to_k","to_v","to_out.0"]))
├─ pre-encode all ref images → latents; pre-encode captions → prompt embeds
├─ evict VAE + text encoders to CPU (16GB memory plan)
├─ AdamW(only trainable params, lr)
├─ Accelerator(gradient_accumulation_steps=2, mixed_precision="bf16" on CUDA)
└─ training loop (steps):
     add noise → UNet forward → MSE loss → backward → optimizer.step()
└─ save: PEFT state dict → retag to Kohya lora_unet_* → safetensors
```

## Result handling — back in `train_subject_lora_for_subject`

```
if result.status == "ok":
├─ s.lora_path, s.lora_version, s.training_status = "trained"; commit
├─ ensure_lora_in_comfyui()   # link into ComfyUI models/loras for generation
└─ notify progress system
```

## GPU management (the real-trainer path)

```
with gpu_session(JobKind.LORA_TRAIN, f"subject_{id}",
                 evict_ollama=True, free_comfyui=True,
                 vram_estimate_mb=12000, require_fit=True):
    ... train ...
finally:
    _TRAINER.shutdown()   # free the ~7GB SDXL daemon after the job
```

Training claims the GPU **exclusively** (LORA_TRAIN slot), evicts Ollama + ComfyUI to avoid the observed OOM (subject 16: 137MiB free), and shuts the daemon down afterward so it doesn't leak VRAM between jobs.

## Key design points

- **Two backends**: SDXL (`venv-torch`, PEFT UNet) and Z-Image Turbo (`backend/venv`, `ZImagePipeline.save_lora_weights`) — the default product is Z-Image.
- **Remote option**: `runpod_lora_trainer` plugin routes training to RunPod (no local GPU held).
- **NO-MOCKS policy**: the mock trainer is reachable only under pytest; production fails loud if no real accelerator is found.
- **Idempotency + loud failure**: `training_status` guards double-dispatch; unexpected exceptions mark the Subject `failed` instead of leaving it wedged in `training`.

Want me to dig into any specific layer — e.g. the daemon protocol (`_send`/watchdog), the Z-Image trainer's memory staging, or the RunPod remote path?

----

Alibaba Cloud Object Storage Service (OSS) provides S3-compatible object storage with aggressive pay-as-you-go pricing, making it a strong competitor in the Asia-Pacific region and globally. [1, 2, 3] 
## Updated Comparison Table

| Feature | DigitalOcean Spaces[](https://www.digitalocean.com/pricing/spaces-object-storage) | Cloudflare R2[](https://www.cloudflare.com/products/r2/) | Alibaba Cloud OSS[](https://www.alibabacloud.com/en/product/oss/pricing?_p_lc=1) |
|---|---|---|---|
| Base Pricing | $5.00/month (includes 250 GB) | Pay-as-you-go (no base fee) | Pay-as-you-go (no base fee) |
| Storage Cost (Standard) | $0.02 / GB / month | $0.015 / GB / month (Standard) | $0.017 / GB / month (LRS, >5GB) |
| Data Out (Egress) | 1 TB included, then $0.01 / GB | 100% Free (Zero egress fees) | Metered/Variable (Region-dependent rates) |
| API Requests | Unlimited/Included (no per-request fee) | Metered ($4.50/million write, $0.36/million read) | Metered (Low per-request fees) |
| Free Tier | None | 10 GB/month + request allowances | First 5GB (Standard LRS storage) |
| CDN | Built-in (one-click per bucket) | Edge-native (via Cloudflare ecosystem) | Integrated (via Alibaba Cloud CDN/DCDN) |

If you'd like, I can:

* 
* Compare Alibaba Cloud OSS and Cloudflare R2 data transfer fees for heavy media streaming
* Explain Alibaba Cloud OSS region-specific pricing for Tokyo vs. global nodes
* 


[1] [https://www.alibabacloud.com](https://www.alibabacloud.com/help/en/oss/developer-reference/compatibility-with-amazon-s3)
[2] [https://www.quora.com](https://www.quora.com/What-are-the-best-alternatives-to-Amazon-S3-What-are-the-pros-and-cons)
[3] [https://www.hostever.com](https://www.hostever.com/blog/exploring-the-top-10-aws-s3-alternatives)
