# Git Workflow — Actual Batch Plan (Two-Track)

Document: `docs/git-workflow-sequence.md` describes the fork workflow:
- keep `main` in the fork synced with upstream `main`
- use `dev` as the fork-internal integration branch
- group related `feat/*` branches into a batch, merge the batch into `dev`, run one integration test, then delete the feature branches unless follow-up work is expected
- create a clean upstream-facing branch only when the change set is ready, and delete it after the upstream PR is merged or closed

## Two-track structure

The 42 branches are split into two tracks:

1. **macOS track** — Apple Silicon support (MPS, ComfyUI, OpenAI routing).
2. **general track** — platform-agnostic improvements (UI, captions, RunPod, cast, etc.).

Each track has its own PR sequence. The macOS track is merged first (as the foundation), then the general track (which builds on it).

## Current repository snapshot

- `origin/main` == `upstream/main` == `e4331689` (synced).
- All 42 virtual branches rebased onto `e4331689` (no conflicts).
- Workspace is on `gitbutler/workspace`.

## Prerequisite steps

1. ✅ Sync fork `main` with `upstream/main` (`e1a6212` → `e4331689`).
2. ✅ Rebase all 42 virtual branches onto `e4331689` (no conflicts).
3. Create `dev` in the fork from `e4331689`.

## macOS track (8 branches, 4 PRs)

| PR | Branches | Rationale |
|----|----------|-----------|
| M1 | `feat/audio-mps-whisper-filmcrew`<br>`fix/mps-video-unload` | MPS audio (AudioFoundry) + whisper.cpp STT + MPS video unload fix. |
| M2 | `feat/lora-trainer-mps` | LoRA training on MPS. |
| M3 | `feat/zimage-comfyui`<br>`feat/zimage-comfyui-mps`<br>`feat/imagemodel-comfyui` | ComfyUI-backed image generation (Z-Image) on Apple Silicon. |
| M4 | `feat/llm-providers`<br>`voice-openai-routing` | OpenAI-compatible routing (cloud fallback for no-CUDA Macs). |

## general track (25 branches, 7 PRs)

| PR | Branches | Rationale |
|----|----------|-----------|
| G1 | `feat/audio-music-polish-progress`<br>`feat/music-video-i2v-model`<br>`feat/video-captions-ffmpeg`<br>`feat/ffmpeg-still-video`<br>`fix/video-editor-registration`<br>`feat/video-audio-systemmap-ui`<br>`feat/filmcrew-i2v-speed-config` | Film Crew + Audio/Video general improvements. |
| G2 | `feat/mcp-start` | Optional MCP server startup/cleanup. |
| G3 | `feat/runpod-lora-trainer`<br>`feat/runpod-lora-trainer-setup`<br>`feat/lora-training-fixes`<br>`fix/lora-trainer-timeout` | Remote RunPod LoRA trainer + timeout fixes. |
| G4 | `feat/cast-shot-count`<br>`fix/chat-cast-image-routing`<br>`fix/chat-cast-lora-resolution` | Cast reference-sheet shot counts + chat routing + LoRA resolution. |
| G5 | `fix/vision-sync`<br>`chore/llm-providers-followup`<br>`feat/filmcrew-openai-compatible` | LLM/Vision/OpenAI general routing. |
| G6 | `feat/collapsible-alerts`<br>`fix/collapsible-alert-ref`<br>`fix/infographic-import`<br>`chore/production-detail-collapsible`<br>`chore/filmcrew-collapsible` | Collapsible alert UI + MUI ref handling. |
| G7 | `chore/agent-config-and-docs`<br>`chore/gitignore-ignore-client-media` | Agent config + docs + gitignore rules. |

## Cross-track dependencies

The two tracks are not fully independent — these files are touched by both:

| File | macOS branch | general branch |
|------|-------------|----------------|
| `start.sh` / `stop.sh` | `feat/audio-mps-whisper-filmcrew` (M1) | `feat/mcp-start` (G2) |
| `backend/tasks/production_swarm_tasks.py` | `feat/audio-mps-whisper-filmcrew` (M1) | `feat/filmcrew-openai-compatible` (G5) |
| `backend/tasks/lora_trainer_tasks.py` | `feat/lora-trainer-mps` (M2), `feat/zimage-comfyui-mps` (M3) | `feat/runpod-lora-trainer` (G3) |
| `backend/tools/image_tools.py` | `feat/zimage-comfyui-mps` (M3), `feat/imagemodel-comfyui` (M3) | `fix/chat-cast-lora-resolution` (G4) |
| `backend/utils/llm_service.py` | `voice-openai-routing` (M4) | `fix/vision-sync` (G5) |
| `plugins/lora_trainer/real_trainer.py` | `feat/lora-trainer-mps` (M2) | `fix/lora-trainer-timeout` (G3) |

## Merge order

1. **macOS track**: M1 → M2 → M3 → M4.
2. **general track**: G1 → G2 → G3 → G4 → G5 → G6 → G7.

The macOS track lands first (foundation); the general track lands on top. Cross-track conflicts are resolved when the general track lands.

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

## Per-PR smoke tests

- **M1**: audio pipeline + whisper STT + MPS video.
- **M2**: LoRA training on MPS.
- **M3**: ComfyUI image generation (`Z-Image`, `/imagemodel comfyui`).
- **M4**: OpenAI-compatible chat + Discord voice.
- **G1**: Film Crew render + video editor/FFmpeg.
- **G2**: `./start.sh` / `./stop.sh` with MCP enabled.
- **G3**: RunPod LoRA training.
- **G4**: Cast reference-sheet generation (16/32 shots) + chat `[trigger]` LoRA resolution.
- **G5**: vision sync + Film Crew director.
- **G6**: collapsible alerts in Snackbar / ProductionDetail / CreateProductionDialog.
- **G7**: `./start.sh` with agent config present; gitignore rules respected.

Project test entry points:
- `python3 run_tests.py`
- `cd frontend && npm run lint && npm run test`

## Post-merge cleanup

1. **Docs cleanup**: after all code PRs are in `dev`, remove or relocate the doc-only commits (see "Docs branches" above).
2. Delete merged feature/fix branches unless immediate follow-up work is expected.
3. Run the full integration test suite on `dev`.
4. Create a **clean upstream-facing branch** from `dev`.
5. Open PR to `upstream/main` from that clean branch.
6. Delete the upstream-facing branch after the PR is merged or closed.
