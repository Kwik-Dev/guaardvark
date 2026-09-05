# Git Workflow — Actual Batch Plan

Document: `docs/git-workflow-sequence.md` describes the fork workflow:
- keep `main` in the fork synced with upstream `main`
- use `dev` as the fork-internal integration branch
- group related `feat/*` branches into a batch, merge the batch into `dev`, run one integration test, then delete the feature branches unless follow-up work is expected
- create a clean upstream-facing branch only when the change set is ready, and delete it after the upstream PR is merged or closed

## Important note

> The user will clean up documentation commits in the `dev` branch **after** all `feat/*` / `fix/*` code branches are merged into `dev`, but **before** creating a clean upstream-facing PR branch. No docs branches are merged into `dev` as part of the batch plan.

## Current repository snapshot

- `origin/dev` == `main` == `e1a6212`.
- Local `main` / `origin/main` are **369 commits** behind `upstream/main` (`59fccfae`).
- Workspace is on `gitbutler/workspace` with **42 applied virtual branches**, all based on `e1a6212`.
- Untracked `docs/git-workflow-sequence.md` / `.html` are not part of this plan.

## Prerequisite steps

1. Sync fork `main` with `upstream/main`.
2. Create/update a local `dev` branch from synced `main` (and push to `origin/dev`).

## Batches to merge into `dev`

| # | Batch name | Branches to merge | Rationale |
|---|------------|-------------------|-----------|
| 1 | Film Crew + Audio/Video | `feat/audio-mps-whisper-filmcrew`<br>`feat/audio-music-polish-progress`<br>`feat/music-video-i2v-model`<br>`feat/video-captions-ffmpeg`<br>`feat/ffmpeg-still-video`<br>`fix/video-editor-registration`<br>`fix/mps-video-unload`<br>`feat/video-audio-systemmap-ui`<br>`feat/filmcrew-i2v-speed-config` | Audio/MPS/whisper + Film Crew rendering + video editor/FFmpeg. |
| 2 | MCP startup | `feat/mcp-start` | Start/stop script changes; merge right after Batch 1 because both touch `start.sh`/`stop.sh`. |
| 3 | LoRA training | `feat/lora-trainer-mps`<br>`feat/runpod-lora-trainer`<br>`feat/runpod-lora-trainer-setup`<br>`feat/lora-training-fixes`<br>`fix/lora-trainer-timeout` | Local MPS, remote RunPod, and timeout fixes. |
| 4 | Image / ComfyUI | `feat/zimage-comfyui-mps` (contains `feat/zimage-comfyui`)<br>`feat/imagemodel-comfyui` | ComfyUI-backed image generation. Place after LoRA because `zimage-comfyui-mps` also edits `backend/tasks/lora_trainer_tasks.py`. |
| 5 | Cast / character generation | `feat/cast-shot-count`<br>`fix/chat-cast-image-routing`<br>`fix/chat-cast-lora-resolution` | Cast reference-sheet shot counts, chat cast routing, and chat LoRA resolution. |
| 6 | LLM / Vision / OpenAI routing | `fix/vision-sync` (contains `feat/llm-providers`)<br>`chore/llm-providers-followup`<br>`feat/filmcrew-openai-compatible`<br>`voice-openai-routing` | Broad cross-cutting LLM/cloud/OpenAI changes — best merged last so it sits on top. |
| 7 | UI / collapsible alerts | `feat/collapsible-alerts`<br>`fix/collapsible-alert-ref`<br>`fix/infographic-import`<br>`chore/production-detail-collapsible`<br>`chore/filmcrew-collapsible` | MUI Collapse/Grow ref handling + collapsible alert UI. |
| 8 | Config / chore | `chore/agent-config-and-docs`<br>`chore/gitignore-ignore-client-media` | Repo-level agent config, AGENT/KNOWLEDGE docs, and gitignore rules. |

## Docs branches (NOT merged into `dev`; cleaned up separately)

These are doc-only and ride along on the code branches or are standalone. They are **not** part of the batch merge plan:

- `chore/misc-docs`
- `docs/video-audio-systemmap-guides`
- `docs/generation-diagram`
- `docs/filmcrew-fountain-skill`
- `docs/guaardvark-cli-skill`
- `docs/guides`
- `docs/issues-tracker`
- `docs/issues-feature-requests`
- `docs/git-workflow-actual-plan` (this file)

## Merge order

1. Batch 1 — Film Crew + Audio/Video
2. Batch 2 — MCP startup
3. Batch 3 — LoRA
4. Batch 4 — Image / ComfyUI
5. Batch 5 — Cast / character generation
6. Batch 6 — LLM / Vision / OpenAI routing
7. Batch 7 — UI / collapsible alerts
8. Batch 8 — Config / chore

## Known conflict / overlap hotspots

| File | Branches touching it | Batches |
|------|----------------------|---------|
| `backend/tasks/lora_trainer_tasks.py` | `feat/lora-trainer-mps`, `feat/runpod-lora-trainer`, `feat/runpod-lora-trainer-setup`, `feat/lora-training-fixes`, `fix/lora-trainer-timeout`, `feat/zimage-comfyui-mps` | 3 + 4 |
| `backend/config.py` | `fix/vision-sync`, `feat/runpod-lora-trainer` | 6 + 3 |
| `backend/tasks/production_swarm_tasks.py` | `feat/audio-mps-whisper-filmcrew`, `feat/filmcrew-openai-compatible` | 1 + 6 |
| `start.sh` / `stop.sh` | `feat/audio-mps-whisper-filmcrew`, `feat/mcp-start` | 1 + 2 |
| `backend/services/comfyui_image_generator.py` | `feat/zimage-comfyui-mps`, `feat/imagemodel-comfyui` | 4 (within batch) |
| `backend/utils/llm_service.py` | `fix/vision-sync`, `voice-openai-routing` | 6 (within batch) |
| `plugins/lora_trainer/real_trainer.py` | `feat/lora-trainer-mps`, `fix/lora-trainer-timeout` | 3 (within batch) |
| `backend/services/agent_brain.py` | `fix/chat-cast-image-routing`, `fix/vision-sync`, `feat/filmcrew-openai-compatible` | 5 + 6 |
| `backend/tools/image_tools.py` | `fix/chat-cast-lora-resolution`, `feat/zimage-comfyui-mps`, `feat/imagemodel-comfyui` | 4 + 5 |
| `backend/api/cast_library_api.py` | `feat/cast-shot-count`, `fix/chat-cast-image-routing` | 5 (within batch) |
| `frontend/src/pages/CastMemberPage.jsx` | `feat/cast-shot-count`, `fix/chat-cast-image-routing` | 5 (within batch) |
| `frontend/src/components/common/CollapsibleAlert.jsx` | `feat/collapsible-alerts`, `fix/collapsible-alert-ref`, `chore/production-detail-collapsible`, `chore/filmcrew-collapsible` | 7 (within batch) |

## Per-batch smoke tests

After each merge into `dev`, run a focused check:

- **Batch 1**: Film Crew render + audio pipeline + whisper STT + video editor/FFmpeg.
- **Batch 2**: `./start.sh` / `./stop.sh` with MCP enabled.
- **Batch 3**: LoRA training locally (MPS) and via RunPod.
- **Batch 4**: ComfyUI image generation (`Z-Image`, `/imagemodel comfyui`).
- **Batch 5**: Cast reference-sheet generation (16/32 shots) + chat `[trigger]` LoRA resolution.
- **Batch 6**: OpenAI-compatible provider chat, model-management UI, film crew director, Discord voice.
- **Batch 7**: Collapsible alerts in Snackbar / ProductionDetail / CreateProductionDialog.
- **Batch 8**: `./start.sh` with agent config present; gitignore rules respected.

Project test entry points:
- `python3 run_tests.py`
- `cd frontend && npm run lint && npm run test`

## Post-merge cleanup

1. **Docs cleanup**: After all `feat/*` / `fix/*` code batches are in `dev`, remove or relocate the doc-only commits that rode along on code branches or are standalone docs branches (see "Docs branches" above).
2. Delete merged feature/fix branches unless immediate follow-up work is expected.
3. Run the full integration test suite on `dev`.
4. Create a **clean upstream-facing branch** from `dev`.
5. Open PR to `upstream/main` from that clean branch.
6. Delete the upstream-facing branch after the PR is merged or closed.
