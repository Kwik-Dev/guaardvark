# Character Generation Guide

End-to-end guide for creating a consistent character (cast member) in Guaardvark:
from reference photos and identity sync, through reference-sheet generation,
LoRA training, and use in the Film Crew pipeline.

## Overview

A character (Subject) goes through this lifecycle:

```
Upload refs → Sync identity from photos → Plan sheet → Generate sheet → Approve
  → Train LoRA → Promote to Training Data → Use in Film Crew (storyboard/video)
```

Each step builds on the previous one, and the trained LoRA is what carries the
character's identity into production.

---

## 1. Prerequisites

### A vision-capable model must be available to Ollama
Character identity sync, angle verification, and storyboard judging all need a
model that accepts image input. The system auto-detects a vision model with this
priority (see `backend/utils/vision_analyzer.py`):

1. A **Qwen** model (e.g. `qwen3.8:27b-mlx`) — preferred, confirmed vision-capable.
2. A known vision-capable Gemma4 tag (`gemma4:e4b` / `e2b` / `12b` / `latest`).
3. Other detected vision models.

> **Important:** bare `gemma4` tags that are text-only (e.g. `gemma4:26b-mlx`) are
> deliberately **not** treated as vision-capable. They return
> `400 "does not support image input"` and silently break identity sync and angle
> verification. If your only `gemma4` is a `-mlx` text build, pull `gemma4:e2b` or
> use `qwen3.8`.

### ComfyUI must be able to find trained LoRAs
The character generation (Z-Image path) renders through ComfyUI. ComfyUI only
scans its own `models/loras/` directory, so trained LoRAs must be linked there.
This is automatic when `GUAARDVARK_ZIMAGE_USE_COMFYUI=1`:
`ensure_lora_in_comfyui()` symlinks each trained LoRA into ComfyUI's
`models/loras/` after training and before generation.

Beyond the LoRA, the ComfyUI guaardvark talks to must also have the **Z-Image
base models registered** — `z_image_turbo_bf16.safetensors` (in `unet`/
`diffusion_models`), `qwen_3_4b.safetensors` (in `clip`/`text_encoders`), and
`ae.safetensors` (in `vae`). If `plugins/comfyui/ComfyUI/models/` only holds
WAN video files, a Z-Image still fails with `unet_name/clip_name/vae_name ...
not in []` node_errors. To reuse a Comfy Desktop install's models `~/ComfyUI-Shared`
via the bundled server, add an `extra_model_paths.yaml` (see
`docs/GUAARDVARK_GUIDE.md` §7) and restart ComfyUI.

---

## 2. Sync identity from photos (Overview)

**Endpoint:** `POST /cast-library/subjects/<id>/bible/from-refs`

- Vision-scans the uploaded reference photos (using the vision model, e.g.
  `qwen3.8`), then a **consensus step** merges the per-photo descriptions into a
  structured bible (class token, marks, bible text).
- The consensus step is text-only and uses the configured OpenAI-compatible cloud
  model (e.g. `deepseek-v4-flash:cloud`).
- Refreshes captions and recomposes sample prompts.
- Requires reference photos; returns `400 no_refs` if none uploaded.

The bible describes the **core identity** (face, hair, skin, build, marks). Keep
clothing out of the bible — clothing is a per-scene prompt variable, not part of
the locked identity.

---

## 3. Plan reference sheet

**Button:** "Plan reference sheet" / "Re-plan sheet"
**Endpoint:** `POST /cast-library/subjects/<id>/plan`

- Runs the Casting Director (`generate_character_sheet`) to produce a fresh shot
  **plan** (angles/poses/framings), defaulting to `n=32` shots.
- **Does not generate images** — it only produces the plan.
- If reference photos exist, it keeps the vision-grounded bible (does not invent
  appearance from text).
- Persists the bible + trigger word.

> **Warning:** "Re-plan sheet" **discards the current sheet**, including approved
> samples. Use "Generate additional" to add shots without losing approved ones.

---

## 4. Generate character sheet

**Button:** "Generate base sheet (no LoRA)" or "Generate with trained LoRA"
**Endpoint:** `POST /cast-library/subjects/<id>/generate`

- Dispatches `character.generate_samples`, which re-runs the sheet plan and
  renders the shots (FLUX or Z-Image via ComfyUI).
- If the subject has a trained LoRA (`lora_path`), it passes `use_trained_lora:
  true`, so identity comes from the **LoRA adapter** (trigger + shot variation,
  no full bible dump).
- Appends the new shots onto the curated set by default.

### Image sizes
The canvas is chosen per shot by `_aspect_for_row`:

| Shot type | Dimensions |
|---|---|
| Full-body (head-to-toe) | **832 × 1216** (portrait) |
| Face / close-up / medium | **1024 × 1024** (square) |

### Angle verification
Each generated sample is vision-checked against its planned angle
(`_verify_angle_relabel_regen`). On mismatch it regenerates **once** with a
strengthened framing prompt, then relabels to the observed angle.

> If wrong-facing shots are not being fixed, the angle classifier may be using a
> text-only gemma4 build. Restart the worker after switching to `qwen3.8`, then
> regenerate.

---

## 5. Approve samples

**Button:** "Approve all generated"
**Endpoint:** `POST /cast-library/subjects/<id>/samples/approve`

Approving marks samples as the curated "keeper" set. Approvals drive three things:

1. **Curated set** — approved samples are kept (new batches stack above them).
2. **LoRA training** — the trainer uses `approved=True, status="done"` samples.
3. **Promotion** — after a successful train, approved samples are promoted into
   durable Training Data.

---

## 6. Train LoRA

**Button:** "Train LoRA"

- Dispatches `lora_trainer.train_lora`, which trains a LoRA adapter on the
  approved reference samples (via the Z-Image trainer on MPS/CUDA).
- Training on Apple Silicon/MPS is slow (~11s/step); a 640-step schedule takes
  ~2h. The daemon train timeout is 3h (`_TRAIN_TIMEOUT_S`).
- On success, produces a `.safetensors` file in `data/training/loras/` and sets
  `training_status='trained'`.
- When `GUAARDVARK_ZIMAGE_USE_COMFYUI=1`, the new LoRA is auto-linked into
  ComfyUI's `models/loras/`.

**Training-set guidance:** the LoRA locks the **person** (face, hair, skin, build).
Varying **clothing** across the training images is fine and helps generalization —
the outfit is controlled by the scene prompt. Consistency in face/identity is what
matters.

---

## 7. Promote to Training Data

After a **verified real** train, `promote_samples_after_train`:

- Marks used approved samples `promoted_to_training=True` (hidden from Generate
  Character, listed under Training Data).
- Appends their paths to `Subject.ref_image_paths` (deduped) — the **durable refs**.

Durable refs survive re-planning and feed:
- Future LoRA train/amend.
- Character generation.
- Identity sync / bible building.
- The post-train smoke test.

---

## 8. Use in Film Crew

Storyboard/video generation (`run_storyboard_artist`) resolves each shot's
characters via `subjects_to_lock()` → their **`lora_path`**, then renders the
storyboard still with `generate_image(prompt=scene_prompt, loras=[...])`. The
identity lock (trigger + class + short marks) is applied inside the render.

- The storyboard uses the **trained LoRA** for identity — approved images are not
  referenced directly; their influence is baked into the LoRA weights.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Identity sync / angle verify fail with "does not support image input" | Vision model is a text-only gemma4 (`gemma4:26b-mlx`) | Use `qwen3.8` (auto-preferred) or pull a vision-capable gemma4 tag; restart worker |
| Generate fails with ComfyUI `lora_name '...' not in []` | Trained LoRA not linked into ComfyUI `models/loras/` | Ensure `GUAARDVARK_ZIMAGE_USE_COMFYUI=1` (auto-links); or symlink manually |
| Generate fails with ComfyUI `unet_name/clip_name/vae_name '...' not in []` | ComfyUI has the LoRA but not the Z-Image base models (`z_image_turbo_bf16`, `qwen_3_4b`, `ae.safetensors`) | Add an `extra_model_paths.yaml` bridging a Comfy-Desktop/`ComfyUI-Shared` tree and restart ComfyUI |
| Wrong-facing shots not fixed | Angle classifier using broken vision model | Restart worker after switching to `qwen3.8`, then regenerate |
| LoRA training killed at ~30 min | Daemon train timeout too low | `_TRAIN_TIMEOUT_S` should be 3h for MPS |

---

## Reference (key code paths)

- `backend/api/cast_library_api.py` — `bible/from-refs`, `plan`, `generate`, `approve`
- `backend/services/cast_identity_manager.py` / `character_bible_from_refs.py` — identity sync + consensus
- `backend/services/character_generator_service.py` — sheet planning
- `backend/tasks/character_generation_tasks.py` — generation + angle verification
- `backend/services/character_angle_verify.py` — angle classify / strengthen prompt
- `backend/tasks/lora_trainer_tasks.py` — LoRA training + promotion
- `backend/services/sample_promotion.py` — promotion to Training Data
- `backend/services/comfyui_image_generator.py` — ComfyUI rendering + LoRA auto-link
- `backend/utils/vision_analyzer.py` / `chat_utils.py` / `servo_knowledge_store.py` — vision model detection
