# Project Type: GPU Service Plugins + ML/Infra

Guaardvark runs 13+ self-contained GPU service plugins under `plugins/`, each on its
own port, health-checked and arbitrated by the System Resource Orchestrator (VRAM).

## Plugin layout

- Each plugin is a directory under `plugins/` (e.g. `comfyui`, `vision_pipeline`,
  `video_editor`, `audio_foundry`, `upscaling`, `training`, `lora_trainer`,
  `runpod_lora_trainer`, `swarm`, `discord`, `ollama`, `gpu_embedding`).
- Each has a `plugin.json` manifest: `id`, `port`, `vram_estimate_mb`, `endpoints`,
  `config`, and `requirements` (gpu/cuda/system_binaries). Example:
  `plugins/video_editor/plugin.json`.
- Plugins run as separate processes; they are health-checked by the orchestrator.

## Editing & reproducibility

- Treat generated data, trained models, migration outputs, and deployment manifests
  as first-class repository artifacts that need provenance.
- Document what can run locally vs. what needs GPU/staging, and anything that must
  never run without explicit approval.
- Preserve pinned versions and reproducibility notes. `GUAARDVARK_*` env vars override
  defaults; never hardcode paths.
- The `runpod_lora_trainer` plugin has its own `pod/` (Dockerfile, runner).

## Testing

- Use fast local validation per edit (unit tests, dry-run commands, schema checks)
  when possible.
- Keep broader pipeline/training/deployment smoke tests separate and run after a
  coherent work batch.
- Ask the user before treating dummy datasets or synthetic production-like examples
  as canonical fixtures.

## Gotchas

- **Python 3.12 only** for the ML stack (numpy<2.0, mediapipe, gfpgan/basicsr,
  realesrgan lack 3.13/3.14 wheels); `setuptools<81` may be required.
- `backend/services/swarm/` is the **Film Crew** sequential production pipeline, NOT
  the parallel orchestrator. The parallel Swarm Orchestrator lives in `plugins/swarm/`.
