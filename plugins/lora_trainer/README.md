# LoRA Trainer Plugin

Trains character, environment, and prop LoRAs for the Film Crew.

## Backends

The plugin selects between two backends:

- **real** (production) — runs SDXL LoRA training in an isolated `venv-torch/` via subprocess. ~10-15 min per subject on a 24 GB GPU.
- **mock** (pytest only) — sleeps ~1s, writes a stub safetensors file. **Refused outside pytest** per the NO-MOCKS policy; production must use the real trainer or fail loud.

Selection priority:
  1. `GUAARDVARK_LORA_BACKEND=real|auto|mock` env var (default: `auto`)
  2. `auto`: use **real** if `venv-torch/bin/python` exists and CUDA probe passes; otherwise **fail** with an error (no silent mock fallback)
  3. `mock`: allowed only when `PYTEST_CURRENT_TEST` is set (unit/integration tests)

### Setting up real training

  $ cd plugins/lora_trainer
  $ ./scripts/setup_venv.sh

This creates `venv-torch/`, installs torch+diffusers+peft (~7 GB once + ~5 GB cache for the SDXL base on first run), and verifies CUDA. Once it succeeds, the next training dispatch picks the real backend automatically.

### When real training is unavailable

If `auto` cannot reach the real trainer (missing venv, GPU busy, CUDA probe failed), the Celery task returns `status: failed` with guidance — it does **not** write a fake LoRA. Check `nvidia-smi`, run `setup_venv.sh`, or set `GUAARDVARK_LORA_BACKEND=real` to bypass the availability probe.

### Mock backend (tests only)

Set `GUAARDVARK_LORA_BACKEND=mock` only under pytest. In production that value is rejected with an explicit error.

### Hyperparameters

  - Base model: `stabilityai/stable-diffusion-xl-base-1.0`
  - LoRA rank/alpha: 16/16; target_modules = to_q, to_k, to_v, to_out.0
  - Steps: `min(1500, max(400, num_refs * 100))`
  - LR 1e-4, bf16, batch=1 with gradient_accumulation_steps=2
  - Resolution 1024 (resize all refs)
