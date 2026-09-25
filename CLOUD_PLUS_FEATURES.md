# What `cloud-plus` adds over upstream `main`

This is the itemized delta of the fork mainline `cloud-plus` against the upstream
mirror `main` — everything the fork has added or changed that upstream `main` does
not have. It is generated from the commit range `upstream/main..cloud-plus`
(141 non-merge commits, 311 files, ~17.5k insertions), tip `73b5c4df`.

For the *why* behind the fork — that upstream removed the cloud chat providers and
this fork keeps them — see the callout at the top of `README.md`. For the
up-to-date issue tracker and the bug log, see `FEATURES.md` and `KNOWN_BUGS.md`.

Branch model: `main` is a pure upstream mirror; `cloud-plus` is the fork mainline
that carries this work. This file is the answer to "what does cloud-plus give me
that main does not?"

Last regenerated: the `cloud-plus` tip at the time of writing. Re-run
`git log --no-merges --oneline upstream/main..cloud-plus` after the next upstream
merge to refresh.

---

## 1. Cloud LLM providers, re-introduced

Upstream deleted the cloud chat providers and the master cloud switch (`001d960c`,
merged as `4fe7406a`). `cloud-plus` brings that stack back and adds an
OpenAI-compatible layer with multi-provider escalation.

- **OpenAI-compatible chat provider** — `backend/services/openai_provider.py`,
  `backend/services/mistral_provider.py`, `backend/services/llm_provider.py`, and
  the API surface `backend/api/llm_provider_api.py`.
- **Smart multi-provider escalation** — an OpenAI-compatible endpoint as the primary
  chat brain, with a fallback/escalation path across providers; the
  OpenAI-compatible LLM guard and the Character Generator / consensus routing are
  gated on the master cloud switch (`GUAARDVARK_...` cloud consent).
- **The master cloud switch gates `get_default_llm`** too — when off, no local
  Ollama model is loaded while an OpenAI-compatible endpoint is active.
- **UTF-8 forced in cloud LLM streaming** — fixes mojibake on streamed replies
  (`f1cbbb7d`).
- **`.env` opt-in variables documented** — Z-Image/ComfyUI and OpenAI opt-ins,
  the OpenAI base URL is required (no implicit `api.openai.com` default), and
  vision uses `OLLAMA_BASE_URL` only (`4f265e5d`, `887c4317`, `45c7e4d0`).
- **Music-prompt rewriter respects cloud consent**, with a local fallback
  (`6c529b1b`, `46404488`).
- The Discord voice backend is opt-in (Guaardvark default; pi-omni router fallback),
  `9f4036bd`, pinned by `plugins/discord/tests/test_voice_backend.py`.

## 2. ComfyUI and the GPU (macOS / MPS)

This is the largest block of net-new capability — ComfyUI image/video generation
on Apple Silicon, with a live-serve probe instead of a bundled-directory check.

- **MPS GPU support, auto-start, and status check for ComfyUI** (`04808b4b`).
- **Engine detection from live `/object_info`** instead of the bundled dir
  (`7f5ba1f7`) — the ComfyUI plugin health probe now proves the process on `:8188`
  is ours; a stranger's ComfyUI on the port no longer reads as "running".
- **Z-Image generation on Apple Silicon via ComfyUI** (`3df1fae8`), with LoRA
  applied model-only in the ComfyUI Z-Image graph (`cbe5b353`) and trained LoRAs
  auto-linked into ComfyUI when Z-Image is routed through it (`253d953b`).
- **`/imagemodel comfyui` chat image backend**, accepted regardless of download
  status (`02271e34`, `724f097d`).
- **VRAM reserve declared per model** in the registry (MiniMax H3 5.0 GB,
  Wan 2.2 14B 1.0 GB); first render of the next family after a restart relaunches
  ComfyUI once with the right reserve; `GUAARDVARK_COMFYUI_RESERVE_VRAM` still wins.
  A busy ComfyUI no longer orphans renders (sustained down-detection, `82e52363`).
- **ComfyUI catalog row reports as installed** — the M4 fix (`f8db59dd`,
  `dc40b8fb`, `6808005f`, `03a99e3d`, `def39de2`): the generic `comfyui` /
  `comfy:comfyui` sentinel ids route to `_comfyui_assets_present()` (the live
  `/object_info` probe) instead of the diffusers `model_index.json` check that
  always read them as not installed. Regression:
  `backend/tests/services/test_comfyui_assets_present.py`.
- **Shared ComfyUI model home** via `GUAARDVARK_COMFYUI_DIR`
  (`~/ComfyUI-Shared`) + an `extra_model_paths` bridge, documented (`559c1e0d`).

## 3. Film Crew production

A full set of live-UI and workflow upgrades on top of the Film Crew pipeline.

- **Live RenderProgress during the rendering stage** — `frontend/src/components/filmcrew/RenderProgress.jsx`
  (`12698548`).
- **Resumable rendering + per-shot clip persistence** (`8ea5e57f`); pause/resume a
  render, documented (`d3b2f77e`).
- **Storyboard generation progress indicator** — `StoryboardProgress.jsx`
  (`576c6436`); subject LoRAs refreshed and missing ones dropped during storyboard
  gen (`ec1cdf92`).
- **Script templates loaded from `docs/film-crew-scripts`** (`e2cee1ae`), with a
  Fountain-to-Film-Crew conversion skill and sample (`d3b6b060`).
- **Film Crew agents routed through the OpenAI-compatible provider**, and the
  director service LLM calls routed the same way, gated on cloud consent
  (`ca6dec13`, `ffc0b2aa`, `8552f1ef`).
- **Configurable I2V speed/quality env vars** (fast default, `a71c7bdd`) and an
  **I2V model dropdown in the music-video approval panel** (`c69f285f`).
- **Collapsible alert** in `ProductionDetail` and `CreateProductionDialog`
  (`9f2cf464`, `f9817a4e`).
- Final `final.mp4` plays in the browser and surfaces on the Production page
  (`eb5d8786`); System Map / Audio Studio light-mode contrast fixed (`dff5e3fd`).

## 4. Video editor and FFmpeg

- **FFmpeg still-to-video** generation with camera-only motion
  (`backend/services/ffmpeg_still_video_generator.py`, `scripts/ffmpeg_stills.py`,
  `7c236d3a`), documented in `docs/FFMPEG_STILL_VIDEO.md`.
- **Configurable focus point** for FFmpeg Ken Burns zoom/pan (`aa91f13e`);
  **pan directions including random**, AI model config hidden in FFmpeg mode
  (`e386f4d3`).
- **Framing modes** — letterbox / zoom-to-fill / match-image, with min+max size
  (`c6bfd76f`).
- **Caption export/import + a code-editor editing surface** for captions, plus
  FFmpeg fit-framing and transparency (`cc1907de`).
- **Drag-to-reorder in the video editor Bin panel**, with reliable drop, up/down
  arrow reorder buttons in the bin header, and ref-tracked drag source so a
  re-render doesn't cancel a reorder (`c0a7710e`, `cbf5940b`, `804f3624`, `67504a3a`,
  `ced774f0`).
- FFmpeg batches made visible in the video library with correct counts via
  `batch_metadata.json`; results included in metadata so clips play (`cbe81bff`,
  `674917fa`).

## 5. Audio Foundry, voice, and music

- **MPS support + remote-capable Audio Foundry** (`ac375fff`).
- **STT routed through an external whisper.cpp server** (`45cced4d`).
- **Cloud music-prompt rewriter + music generation progress** (`46404488`).
- Audio guide (setup, models, MPS/CUDA, remote, whisper) added to docs
  (`03dbbbf2`).

## 6. Cast / LoRA

- **Per-run shot count (16/32) for character generation** (`ca5e48d6`), with angle
  distribution documented per shot count (`16adceae`).
- Cast LoRA resolved from the user message in `generate_image` (`6160f54b`);
  cast generation on its own Celery queue (`d6772852`).
- **Identity sync routed through qwen3.8 vision + a cloud consensus model**
  (`e4465509`), with a local fallback (`a4e21c34`).
- **RunPod remote LoRA trainer** as an alternative plugin — SDK deps, pod runner,
  S3/R2 input staging and output bucket+prefix, in-service smoke mode
  (`429f8cf9`, `a26043d9`, `fe77c00d`, `85b57a1e`, `d65270f1`),
  `plugins/runpod_lora_trainer/`.

## 7. UI / model management

- **Model-management UI + a cloud model shown in the status bar** (`175461de`),
  `frontend/src/components/settings/ModelManagementSection.jsx`.
- **Collapsible alerts** — a common `CollapsibleAlert.jsx` /
  `CollapsibleAlertSnackbar.jsx` (click the icon to fold/hide, click again to
  expand), used in the model-management section and dialogs (`579dd83a`,
  `77e96273`, `cd2bce5f`).
- **MUI `Collapse`/`Grow` ref fixes** in `CollapsibleAlert` and `PluginsPage`
  (`de4d1690`); the frontend build was repaired for the missing
  `CollapsibleAlertSnackbar` / `CollapsibleAlertTitle` (`ca5a849a`).

## 8. Ops, MCP, and launch

- **Optional MCP server startup and cleanup in the `start`/`stop` scripts**
  (`825f86a1`).
- A `.codex-ready/` project-type catalog (cli-python, frontend-react,
  gpu-plugins-ml, monorepo, python-api), plus agent project-type configs and
  agent/knowledge docs (`acc0502a`, `429f8cf9`).
- **Portable checkouts** — no absolute home paths in tracked files (`8ee52d76`).
- A **Fork Branch Workflow** diagram (sequence + branch-graph mermaid) and a
  **generation system block diagram**, linked from `ARCHITECTURE` and `AUDIO`
  (`1640ee5f`, `41f86704`).
- **CI runs on `cloud-plus`**, the branch that carries the fork's work
  (`a8e1a9b6`); the load-gate tests stop reading the ambient swap cap
  (`40d2cac5`).
- The `data/clients` dir, JP guide screenshots, and `*.mp4` are gitignored;
  runtime RAG indices and transient UI state are ignored too (`c96f7929`,
  `e8cd0327`, `ff6bbd32`).

## 9. Docs

- **`FEATURES.md` + `KNOWN_BUGS.md`** created from the `ISSUES.md` split
  (`a95c1608`), with accumulated PR handouts/notes moved into `archive/`
  (`91701774`).
- The repo model documented: `main` is the upstream mirror, `cloud-plus` the fork
  mainline (`650c2ebe`).
- Mac / RTX modifications summary (`MACOS.md`) with the provider routing spec,
  memory strategy, and two-track branch management (`f6725cec`, `4f265e5d`).
- How-to guides for video editor, system map, and audio studio
  (`700e9916`); Music Video guide with render-time estimates
  (`89aead3c`); screenshot feature guide (`fd2d67fe`).

---

## 10. `.env` variables

Guaardvark reads its configuration from the repo-root `.env` file. The variables
below are the ones the fork's work touches (grouped by concern). Paths resolve
through `backend/config.py`; secrets and `DATABASE_URL` come from `.env`.
Platform-specific setup — the RTX-on-Mac memory strategy and the two Mac-critical
variables below — lives in `docs/MACOS.md`.

A committed sample lives at **`.env.cloud`**:

```sh
cp .env.cloud .env      # then fill in the values you need
```

It carries the cloud-plus defaults — the cloud chat endpoint and its consent pair,
the memory gates that make a 48 GB Mac viable, the ComfyUI opt-ins. It is
placeholder-only by construction: `.env` is gitignored and `.env.cloud` is not, so
the sample is reviewed by `scripts/check_portable.sh` like any other tracked file.
Copy it rather than editing it, and never put a real credential in it.

### Core / runtime
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_ROOT` | Repo root anchor; all storage/log/backup paths derive from it. |
| `GUAARDVARK_MODE` | Runtime mode: `default` or `test`. |
| `DATABASE_URL` | Postgres connection string (default `postgresql://guaardvark:guaardvark@localhost:5432/guaardvark`). |
| `REDIS_URL` | Redis connection for Celery / sockets. |
| `SECRET_KEY` | Flask session/secret key. |
| `FLASK_PORT` / `VITE_PORT` | Backend (default 5055) and frontend (default 5173) ports. |

### LLM / cloud routing (the memory strategy)
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_DEFAULT_LLM` | Default chat model. |
| `GUAARDVARK_OPENAI_API_KEY` / `GUAARDVARK_OPENAI_BASE_URL` / `GUAARDVARK_OPENAI_MODEL` | OpenAI-compatible provider — used to point the chat brain at **ollama-cloud `deepseek-v4`** so no large LLM is held in local memory. The endpoint is always explicit: **`GUAARDVARK_OPENAI_BASE_URL` is required** (there is no implicit `api.openai.com` default). The key is optional (local vLLM / Ollama need none), and a bare `OPENAI_API_KEY` (exported for another tool) is deliberately ignored. Configuring these is only the **capability** — the operator must also turn on the master cloud switch and select the provider in Settings, or every `get_default_llm` / `get_llm_for_startup` / `get_llm_instance` call stays local. The endpoint + model in use are logged at INFO. |
| `GUAARDVARK_MISTRAL_API_KEY` / `GUAARDVARK_MISTRAL_MODEL` / `GUAARDVARK_MISTRAL_BASE_URL` | Optional Mistral provider (multi-provider escalation). |
| `GUAARDVARK_CLOUD_MODELS_ENABLED` / `GUAARDVARK_LLM_PROVIDER` | Pin the consent and the provider from the environment (`true`/`false`; `ollama`/`openai`/`mistral`), for a deployment that IS cloud by default. Unset, the Settings page toggles decide and a fresh install stays fully local. Set, they win over the stored DB rows — so the box stays cloud across a clone or a DB reset — and `provider_state()` reports the toggle as env-forced so the UI shows it as pinned instead of as a switch the gate would ignore. An unparseable value falls back to the DB rather than reading as local. |
| `OLLAMA_BASE_URL` | Ollama endpoint used by **vision analysis** (`VisionAnalyzer`), embeddings, and the local-chat fallback. Shared — changing it moves all three. |
| `OLLAMA_API_KEY` | Bearer token for a remote/cloud Ollama endpoint. Read by the `ollama` client (chat, consensus, local branches) — **not** by `VisionAnalyzer`'s raw HTTP calls. |
| `GUAARDVARK_EMBEDDING_MODEL` | Embedding model for RAG. |
| `GUAARDVARK_CLAUDE_API_ENABLED` / `GUAARDVARK_CLAUDE_MODEL` / `GUAARDVARK_CLAUDE_MAX_TOKENS` / `GUAARDVARK_CLAUDE_TOKEN_BUDGET` / `GUAARDVARK_CLAUDE_ESCALATION_MODE` | Optional "Uncle Claude" guardian / escalation. |
| `GUAARDVARK_ESCALATION_PROVIDER` / `_BASE_URL` / `_API_KEY` / `_MODEL` | Escalation provider for `smart`/`always` modes: `auto`, `anthropic`, or **any OpenAI-compatible endpoint**. `_BASE_URL` is what selects the OpenAI-compatible path; legacy `ANTHROPIC_API_KEY` + `GUAARDVARK_CLAUDE_MODEL` still resolve to Anthropic. |

### ComfyUI / image generation
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_COMFYUI_DIR` | ComfyUI **shared model home** (e.g. `~/ComfyUI-Shared`). Lets Guaardvark reuse models already in ComfyUI instead of its own `data/models/stable_diffusion`. |
| `GUAARDVARK_COMFYUI_URL` | ComfyUI endpoint (default `http://127.0.0.1:8188`). |
| `GUAARDVARK_COMFYUI_VENV` | ComfyUI virtualenv path. |
| `GUAARDVARK_COMFYUI_IDLE_TIMEOUT` | Idle timeout before ComfyUI is freed. Only wired into the video router, which stops the whole server — image renders never schedule it. |
| `GUAARDVARK_COMFYUI_MODEL_FREE_DELAY_S` | Idle seconds before ComfyUI's resident models are unloaded via `POST /free`, leaving the server up (default `120`, `0` disables). Armed by every ComfyUI stills render and re-armed by each one, so a batch unloads exactly once — after its last image. Skips a non-empty queue, because `/free` evicts for every client on that server. This is the reclaim that reaches a Comfy Desktop install, which never gets the plugin's `--disable-smart-memory --cache-none`, and the only one that returns memory on unified (Apple Silicon) memory, where there is no separate VRAM pool to offload into. |
| `GUAARDVARK_COMFYUI_ENGINE_CACHE_TTL` | Seconds to cache the live `/object_info` engine list (default `5`, `0` disables). Keeps per-model listings and a down ComfyUI from probing once per row. |
| `GUAARDVARK_COMFYUI_LORAS_DIR` | Path to the running ComfyUI's `models/loras` (checked first); where trained Cast LoRAs are symlinked so `LoraLoaderModelOnly` can resolve them by basename. |
| `COMFYUI_OUTPUT_DIR` | ComfyUI output directory. |
| `GUAARDVARK_ZIMAGE_USE_COMFYUI` | Opt-in flag (`1`/`true`/`yes`/`on`) that routes Z-Image through ComfyUI instead of the CUDA-only offline Diffusers path. Required on Apple Silicon. Off by default. |
| `GUAARDVARK_ZIMAGE_UNET` / `_CLIP` / `_CLIP_TYPE` / `_VAE` | Z-Image ComfyUI graph assets (defaults `z_image_turbo_bf16.safetensors`, `qwen_3_4b.safetensors`, `lumina2`, `ae.safetensors`). Must exist in the reachable ComfyUI. |
| `GUAARDVARK_ZIMAGE_SAMPLER` / `_SCHEDULER` | Z-Image sampler/scheduler (defaults `res_multistep` / `simple`). |
| `GUAARDVARK_ZIMAGE_SHIFT` | `ModelSamplingAuraFlow` flow-matching shift (default `3`, from the working Z-Image Turbo workflow). |
| `GUAARDVARK_ZIMAGE_CFG` | CFG used when the requested guidance is below 1.0 (default `1.0`); ComfyUI's KSampler needs a real cfg, unlike the offline CFG-free path. |

### RunPod / LoRA training (offloaded to cloud GPU)
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_RUNPOD_API_KEY` | RunPod API key for the remote LoRA trainer. |
| `GUAARDVARK_RUNPOD_ENDPOINT_ID` | RunPod serverless endpoint id. |
| `GUAARDVARK_RUNPOD_MAX_JOB_SECONDS` | Hard ceiling for a training job. |
| `GUAARDVARK_RUNPOD_POLL_INTERVAL` | Poll interval while waiting on a job. |
| `GUAARDVARK_RUNPOD_OUTPUT_BUCKET` | S3/R2 bucket for training artifacts. |

### Whisper / audio (STT + TTS)
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_USE_WHISPER_SERVER` | Opt-in flag (`1`) to route STT through an external `whisper.cpp` server instead of the bundled build. |
| `GUAARDVARK_START_VOICE_STACK` | Opt-in flag (`1`) that lets the Discord plugin's start/stop scripts bring up / shut down the external pi-omni voice stack (whisper + Kokoro + router on `8081`). Off by default; only used when `voice.backend: "pi-omni"`. |
| `GUAARDVARK_WHISPER_SERVER_BIN` | Path to the `whisper-server` binary (defaults to `command -v whisper-server`). |
| `GUAARDVARK_WHISPER_SERVER_MODEL` | Path to the whisper model (e.g. `ggml-base.bin`). |
| `GUAARDVARK_WHISPER_SERVER_PORT` | Server port (default `5800`). |
| `GUAARDVARK_WHISPER_SERVER_URL` | Server URL the backend posts to (default `http://127.0.0.1:5800`). |
| `WHISPER_DIR` / `WHISPER_BUILD_DIR` / `WHISPER_CLI` | Whisper install/build paths and CLI (bundled build). |

### Video
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_VIDEO_BACKEND` | Video backend — `ffmpeg` for the lightweight pic-to-video path (vs an AI model). |

### GPU / memory management
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_GPU_IDLE_TIMEOUT` | Idle timeout before a GPU model is evicted. |
| `GUAARDVARK_GPU_EVICTION_GRACE` | Grace period before eviction. |
| `GUAARDVARK_GPU_QUALITY_TIER` | Quality tier (e.g. `balanced`). |
| `GUAARDVARK_SWAP_HARD_MAX_GB` | Raise the swap hard-block threshold (default `8` GB) — macOS holds swap "sticky" and a fixed cap can block legitimate work on a healthy Mac. |
| `GUAARDVARK_CHAT_KEEP_ALIVE_CPU` / `GUAARDVARK_CHAT_KEEP_ALIVE_GPU` | Keep-alive for the chat model on CPU vs GPU. |
| `GUAARDVARK_EMBED_KEEP_ALIVE_CPU` / `GUAARDVARK_EMBED_KEEP_ALIVE_GPU` | Keep-alive for the embedding model. |

### MCP
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_MCP_ENABLED` | Enable the MCP server. |
| `GUAARDVARK_MCP_SERVERS` | MCP server config. |
| `GUAARDVARK_MCP_TIMEOUT` | MCP timeout. |

### Ollama tuning (when a local model is used)
| Variable | Purpose |
|----------|---------|
| `OLLAMA_KEEP_ALIVE` | How long a model stays loaded. |
| `OLLAMA_MAX_LOADED_MODELS` | Max models resident at once (memory control). |
| `OLLAMA_NUM_CTX` | Context window. |
| `OLLAMA_NUM_PARALLEL` | Parallel requests. |
| `OLLAMA_KV_CACHE_TYPE` | KV cache quantization (memory control). |
| `OLLAMA_FLASH_ATTENTION` | Flash attention on/off. |

### Storage directories (all derived from `GUAARDVARK_ROOT`)
`GUAARDVARK_STORAGE_DIR`, `GUAARDVARK_OUTPUT_DIR`, `GUAARDVARK_UPLOAD_DIR`, `GUAARDVARK_CACHE_DIR`, `GUAARDVARK_LOG_DIR`, `GUAARDVARK_BACKUP_DIR`, `GUAARDVARK_CONTEXT_DIR` — override where data, outputs, uploads, cache, logs, backups, and context live.

---

## How to refresh

```sh
git fetch upstream
git log --no-merges --oneline upstream/main..cloud-plus      # the full list
git diff --stat upstream/main..cloud-plus                    # the scope
```

After the next upstream merge this list grows where upstream and the fork both
touch the same divergence (chiefly the cloud-provider files upstream deleted).
