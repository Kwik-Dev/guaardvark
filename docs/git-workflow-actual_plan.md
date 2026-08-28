# Git Workflow — Actual Batch Plan

Document: `docs/git-workflow-sequence.md` describes the fork workflow:
- keep `main` in the fork synced with upstream `main`
- use `dev` as the fork-internal integration branch
- group related `feat/*` branches into a batch, merge the batch into `dev`, run one integration test, then delete the feature branches unless follow-up work is expected
- create a clean upstream-facing branch only when the change set is ready, and delete it after the upstream PR is merged or closed

## Important note

> The user will clean up documentation commits in the `dev` branch **after** all `feat/*` branches are merged into `dev`, but **before** creating a clean upstream-facing PR branch. No docs branches are merged into `dev` as part of the batch plan.

## Current repository snapshot

- `origin/dev` == `main` == `e1a6212`.
- Local `main` / `origin/main` are 92 commits behind `upstream/main`.
- Workspace is on `gitbutler/workspace` with 13 applied virtual branches, all based on `e1a6212`.
- Untracked `docs/git-workflow-sequence.md` / `.html` are not part of this plan.

## Prerequisite steps

1. Sync fork `main` with `upstream/main`.
2. Create/update a local `dev` branch from synced `main` (and push to `origin/dev`).

## Batches to merge into `dev`

| # | Batch name | Branches to merge | Rationale |
|---|------------|-------------------|-----------|
| 1 | Film Crew + Audio | `feat/audio-mps-whisper-filmcrew` | Audio/MPS/whisper + Film Crew rendering. |
| 2 | MCP startup | `feat/mcp-start` | Start/stop script changes; merge right after Batch 1 because both touch `start.sh`/`stop.sh`. |
| 3 | LoRA training | `feat/lora-trainer-mps`<br>`feat/runpod-lora-trainer`<br>`fix/lora-trainer-timeout` | Local MPS, remote RunPod, and timeout fixes. |
| 4 | Image / ComfyUI | `feat/zimage-comfyui-mps` (contains `feat/zimage-comfyui`)<br>`feat/imagemodel-comfyui` | ComfyUI-backed image generation. Place after LoRA because `zimage-comfyui-mps` also edits `backend/tasks/lora_trainer_tasks.py`. |
| 5 | LLM / Vision / OpenAI routing | `fix/vision-sync` (contains `feat/llm-providers`)<br>`feat/filmcrew-openai-compatible`<br>`voice-openai-routing` | Broad cross-cutting LLM/cloud/OpenAI changes — best merged last so it sits on top. |

## Merge order

1. Batch 1 — Film Crew + Audio
2. Batch 2 — MCP startup
3. Batch 3 — LoRA
4. Batch 4 — Image / ComfyUI
5. Batch 5 — LLM / Vision / OpenAI routing

## Known conflict / overlap hotspots

| File | Branches touching it | Batches |
|------|----------------------|---------|
| `backend/tasks/lora_trainer_tasks.py` | `feat/lora-trainer-mps`, `feat/runpod-lora-trainer`, `fix/lora-trainer-timeout`, `feat/zimage-comfyui-mps` | 3 + 4 |
| `backend/config.py` | `fix/vision-sync`, `feat/runpod-lora-trainer` | 5 + 3 |
| `backend/tasks/production_swarm_tasks.py` | `feat/audio-mps-whisper-filmcrew`, `feat/filmcrew-openai-compatible` | 1 + 5 |
| `start.sh` / `stop.sh` | `feat/audio-mps-whisper-filmcrew`, `feat/mcp-start` | 1 + 2 |
| `backend/services/comfyui_image_generator.py` | `feat/zimage-comfyui-mps`, `feat/imagemodel-comfyui` | 4 (within batch) |
| `backend/utils/llm_service.py` | `fix/vision-sync`, `voice-openai-routing` | 5 (within batch) |
| `plugins/lora_trainer/real_trainer.py` | `feat/lora-trainer-mps`, `fix/lora-trainer-timeout` | 3 (within batch) |

## Per-batch smoke tests

After each merge into `dev`, run a focused check:

- **Batch 1**: Film Crew render + audio pipeline + whisper STT.
- **Batch 2**: `./start.sh` / `./stop.sh` with MCP enabled.
- **Batch 3**: LoRA training locally (MPS) and via RunPod.
- **Batch 4**: ComfyUI image generation (`Z-Image`, `/imagemodel comfyui`).
- **Batch 5**: OpenAI-compatible provider chat, model-management UI, film crew director, Discord voice.

Project test entry points:
- `python3 run_tests.py`
- `cd frontend && npm run lint && npm run test`

## Post-merge cleanup

1. **Docs cleanup**: After all `feat/*` / `fix/*` batches are in `dev`, remove or relocate the doc-only commits that rode along on code branches:
   - `e3941c52` docs: add audio guide
   - `70287f08` docs: add Film Crew Fountain-to-Film-Crew conversion skill
   - `d06a7e9c` docs: add Guaardvark CLI skill
   - `af94c0e4` docs(issues): record stable-audio torchsde MPS recursion
2. Delete merged feature/fix branches unless immediate follow-up work is expected.
3. Run the full integration test suite on `dev`.
4. Create a **clean upstream-facing branch** from `dev`.
5. Open PR to `upstream/main` from that clean branch.
6. Delete the upstream-facing branch after the PR is merged or closed.
