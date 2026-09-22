# Guaardvark — Modifications for Mac Summary

**Goal:** Make Guaardvark's main features run on an **RTX machine** (GPU-heavy work) on top of a **MacBook Pro M5 48GB** (the always-on host), working around the Mac's **limited unified memory (48GB)**.

**Memory strategy (the core constraint):**
- **LLM → ollama-cloud `deepseek-v4`** — the chat brain runs in the cloud, so the local Mac does **not** hold a large LLM in memory.
- **Image generation → Z-Image Turbo via ComfyUI** — the local memory budget is reserved for image generation (the heaviest local workload).
- **LoRA training → RunPod plugin** — training is offloaded to a cloud GPU (RunPod), not run on the Mac.
- **Video → simple pic-to-video via FFmpeg** — lightweight, no heavy video model in memory.
- **Captions → generated from the customer profile** — text-based, cheap.
- **Audio → whisper STT + TTS** — speech-to-text runs through an external `whisper.cpp` server (offloaded, MPS-friendly) and text-to-speech is handled locally/lightweight, so audio never competes with image generation for the limited memory budget.

Everything below is the set of modifications (across 42 GitButler virtual branches) that implement this.

---

## 1. LLM / Cloud routing (save local memory)

The chat brain is moved to the cloud so the Mac doesn't hold a big local model.

- **`feat/llm-providers`** — OpenAI-compatible chat provider + smart multi-provider escalation; force UTF-8 decoding in cloud LLM streaming (fixes mojibake); model-management UI + cloud model in status bar; surface `.env` cloud models in the Music Video Director dropdown.
- **`voice-openai-routing`** — guard: don't load a local Ollama model when an OpenAI-compatible endpoint is active.
- **`pr/discord-voice`** — Discord voice gets an **opt-in** backend. `voice.backend: "guaardvark"` (default) keeps Guaardvark's own Audio Foundry for STT/TTS; `"pi-omni"` routes through an OpenAI-compatible voice router (`voice.router_url`, whisper.cpp + Kokoro) and **falls back to Guaardvark's voice API** when the router is unreachable. `voice.router_tts_voice` selects the Kokoro voice; `voice.join_greeting` is empty (silent) by default. The external stack is started only when `GUAARDVARK_START_VOICE_STACK=1`. Split out of #214 so the LLM-provider PR stays provider-only.
- **`pr/g5-vision-llm-routing`** — the consolidated follow-up PR: `fix/vision-sync` + `chore/llm-providers-followup` + `feat/filmcrew-openai-compatible`, stacked on #214.
- **`feat/filmcrew-openai-compatible`** — route Film Crew agents and the director_service LLM calls through the OpenAI-compatible provider (so film crew doesn't need a local LLM).
- **`fix/vision-sync`** — never treat text-only `gemma4:26b-mlx` as vision-capable; prefer `qwen3.8`; route cast identity sync through `qwen3.8` vision + cloud consensus model.

> **Local image analysis model:** Guaardvark's local vision analysis (`VisionAnalyzer`) runs through Ollama. It **prefers a Qwen model** (e.g. `qwen3.8`) for image analysis, because `gemma4:26b-mlx` is text-only despite the name (it returns `400 "does not support image input"`). Gemma4 remains only as a **fallback** (priority: Qwen → any vision model already in VRAM → configured gemma4 → `gemma4:e4b`/`moondream`/`llava`). Gemma4:12b is still used as the default **text** LLM for planning (e.g. character-sheet generation), not for image analysis.

> **Provider routing spec (current):** which env var drives which path —
> - **Vision (image → text)** — `backend/utils/vision_analyzer.py` reads **only `OLLAMA_BASE_URL`** (`POST /api/chat` with the base64 image). It never reads `GUAARDVARK_OPENAI_*`, so vision **cannot** be routed to the cloud today and images never leave the machine. The vision model is auto-detected (Qwen first); `GUAARDVARK_DECISION_MODEL` only overrides its text/decision model.
> - **Chat** — `unified_chat_engine` → `llm_provider` (master `cloud_models_enabled` + active provider, set in Settings).
> - **Text / planning / agents** — `get_default_llm()`: **cloud** when `GUAARDVARK_OPENAI_BASE_URL` + `GUAARDVARK_OPENAI_MODEL` are set, otherwise Ollama. `character_generator_service._default_llm()` is stricter: it follows the **master cloud switch / active provider** (`llm_provider.get_active_provider() == OPENAI`), so a configured base URL alone does **not** route it — the operator has to have the provider selected (and cloud models enabled).
> - **Cast identity consensus** (text-only merge, `character_bible_from_refs._default_consensus_llm` from `fix/vision-sync`) — goes through `openai_provider` (**cloud**, same `GUAARDVARK_OPENAI_*`); it is **not** independently switchable today.
> - **Embeddings / RAG** — `OLLAMA_BASE_URL`.
>
> Consequences: `OLLAMA_BASE_URL` is **shared** by vision, embeddings and the local-chat fallback, so pointing it at a remote/cloud Ollama moves all three. `OLLAMA_API_KEY` is honoured by the `ollama` client (chat / consensus / local branches) but **not** by `VisionAnalyzer`, which posts raw HTTP without an auth header — a cloud `OLLAMA_BASE_URL` therefore 401s for vision. Use the `GUAARDVARK_OPENAI_*` route for cloud (key handled), and keep `OLLAMA_BASE_URL` for the local Qwen vision endpoint.
- **`chore/llm-providers-followup`** — ModelManagementSection collapsible alert + ISSUES note.

## 2. Image generation — Z-Image Turbo via ComfyUI (the local memory budget)

The Mac's memory is reserved for image generation, routed through ComfyUI (works on Apple Silicon MPS and on the RTX box).

> **Model storage:** By default Guaardvark stores its models in its own location (e.g. `data/models/stable_diffusion`). With these changes it can now also **use models that live in ComfyUI's shared model home** (`~/ComfyUI-Shared` via `GUAARDVARK_COMFYUI_DIR` + the `extra_model_paths` bridge), so a model downloaded once for ComfyUI is reused instead of being duplicated into Guaardvark's own directory — saving disk and avoiding re-downloads.

- **`feat/zimage-comfyui`** — route Z-Image image generation through ComfyUI; don't free ComfyUI resident models when rendering through ComfyUI.
- **`feat/zimage-comfyui-mps`** — enable Z-Image generation on Apple Silicon via ComfyUI; apply Z-Image character LoRAs model-only in the ComfyUI Z-Image graph; auto-link trained LoRAs into ComfyUI when Z-Image is routed through ComfyUI.
- **`feat/imagemodel-comfyui`** — add `/imagemodel comfyui` chat image backend; accept it regardless of download status; detect ComfyUI engines from live `/object_info` instead of a bundled dir.
- **`pr/m4-comfyui-image` (#197)** — the consolidated route above, off by default: `GUAARDVARK_ZIMAGE_USE_COMFYUI=1` opts Z-Image (chat, batch and Cast stills) into the ComfyUI graph (UNETLoader + Lumina2 CLIP + `ModelSamplingAuraFlow`, CFG-free with `ConditioningZeroOut`, LoRA chain model-only via `LoraLoaderModelOnly`); `/imagemodel comfyui` picks whichever engine ComfyUI has installed, but **skips the Z-Image engine unless `GUAARDVARK_ZIMAGE_USE_COMFYUI=1`** (falling back to FLUX when present), so the selector cannot silently turn the opt-in on; the ComfyUI loras dir is overridable; Z-Image's `min_steps` floor also applies to the `comfyui` selector. Only the Z-Image route skips freeing ComfyUI's resident models — the FLUX/SDXL Comfy paths keep their historic eviction behaviour.
- **`fix/chat-cast-lora-resolution`** — resolve a cast LoRA from the user message in `generate_image` (so `[starship_captain]` / "Starship Captain" loads the trained LoRA even when the LLM strips the trigger).

> **Z-Image → ComfyUI is opt-in.** The offline Diffusers Z-Image path is CUDA-only, so on Apple Silicon the ComfyUI route needs `GUAARDVARK_ZIMAGE_USE_COMFYUI=1`. With the flag off nothing changes for a user who has not set it. When on, the same flag routes Cast stills through ComfyUI (so the GPU session does **not** evict ComfyUI's resident models), links freshly trained LoRAs into ComfyUI's `models/loras`, and is checked in one place (`character_still_pipeline._zimage_via_comfyui_enabled`).

## 3. LoRA training — RunPod plugin (offload to cloud GPU)

Training is offloaded to RunPod so the Mac's memory/GPU isn't consumed by training.

- **`feat/runpod-lora-trainer`** — add RunPod remote LoRA trainer as an alternative plugin; pod Dockerfile on a modern CUDA 12 base + bundled trainer scripts; S3/R2 input staging + output bucket/prefix + in-service smoke mode; pass `job_id` through the remote trainer + progress ETA.
- **`feat/runpod-lora-trainer-setup`** — RunPod remote LoRA trainer setup (SDK deps, pod runner, issue doc); map the internal zimage model id to the HF repo in the pod runner.
- **`feat/lora-training-fixes`** — keep facial-hair terms in the cast description, `job_id` passthrough, pod path guard.
- **`feat/lora-trainer-mps`** — Apple Silicon (MPS) support for the local LoRA trainer (fallback path).
- **`fix/lora-trainer-timeout`** — raise daemon/task timeouts to fit slow MPS training; fail loudly + reconcile timeouts so training can't loop silently.

## 4. Video — simple pic-to-video via FFmpeg (lightweight)

Video generation is kept lightweight with FFmpeg (no heavy video model in memory).

- **`feat/ffmpeg-still-video`** — FFmpeg still-to-video generation with camera-only motion patterns; configurable focus point for Ken Burns zoom/pan; pan directions incl. random; hide AI model config in FFmpeg mode; bin drag-to-reorder + up/down arrow reorder; FFmpeg batches visible in the video library with correct counts.
- **`feat/video-captions-ffmpeg`** — caption export/import + code-editor editing; FFmpeg fit-framing & transparency; FFmpeg framing modes (letterbox / zoom-to-fill / match-image) with min+max size.
- **`fix/video-editor-registration`** — point video-editor registration `backend_url` at the running backend (5055).
- **`fix/mps-video-unload`** — resolve `_mps_available` NameError (offline video import) + Files repo-root browse crash.
- **`feat/music-video-i2v-model`** — I2V model dropdown in the music video approval panel.
- **`feat/audio-music-polish-progress`** — cloud music-prompt rewriter + music generation progress.

## 5. Captions from customer profile

- **`feat/video-captions-ffmpeg`** — caption generation/editing tied to the customer profile (caption export/import, code-editor editing, FFmpeg fit-framing & transparency).
- **`chore/misc-docs`** — video caption + FFmpeg guides, caption SRT sample, message.md.

## 6. Film Crew + Audio pipeline (MPS-friendly, memory-aware)

- **`feat/audio-mps-whisper-filmcrew`** — MPS support + remote-capable AudioFoundry; route STT through an external whisper.cpp server; ComfyUI MPS GPU support + auto-start + status check; Film Crew resumable rendering + per-shot clip persistence; show RenderProgress during rendering. (Whisper STT is offloaded to an external server and TTS is handled locally/lightweight, so audio stays out of the image-generation memory budget.)
- **`feat/filmcrew-i2v-speed-config`** — configurable I2V speed/quality env vars (fast default); sustained ComfyUI down-detection so a busy ComfyUI doesn't orphan renders.
- **`feat/cast-shot-count`** — per-run shot count (16/32) for character generation.
- **`fix/chat-cast-image-routing`** — text-analysis routing + cast generation celery queue.

## 7. UI / polish

- **`feat/collapsible-alerts`** + **`fix/collapsible-alert-ref`** — collapsible alerts; MUI Collapse/Grow ref handling in CollapsibleAlert and PluginsPage.
- **`chore/production-detail-collapsible`** / **`chore/filmcrew-collapsible`** — collapsible alert in ProductionDetail / CreateProductionDialog.
- **`fix/infographic-import`** — InfographicGenerator stray import statement.
- **`feat/video-audio-systemmap-ui`** — System Map / Audio Studio contrast in light mode; play `final.mp4` in browser on the Production page.
- **`feat/mcp-start`** — optional MCP server startup and cleanup in start/stop scripts.

## 8. Config / docs / housekeeping

- **`chore/agent-config-and-docs`** — agent project-type configs, AGENT/KNOWLEDGE docs, RunPod issues.
- **`chore/gitignore-ignore-client-media`** — ignore `data/clients`, JP guide screenshots, `*.mp4`.
- **Docs branches** (not merged into `dev`; cleaned up separately): `docs/video-audio-systemmap-guides`, `docs/generation-diagram`, `docs/filmcrew-fountain-skill`, `docs/guaardvark-cli-skill`, `docs/guides`, `docs/issues-tracker`, `docs/issues-feature-requests`, `docs/git-workflow-actual-plan`.

---

## How the memory strategy maps to the code

| Workload | Where it runs | Memory impact on the Mac |
|----------|---------------|--------------------------|
| Chat / LLM | ollama-cloud `deepseek-v4` | ~none (cloud) |
| Image gen | Z-Image Turbo via ComfyUI | the main local memory budget |
| LoRA training | RunPod (cloud GPU) | ~none (offloaded) |
| Video (pic→video) | FFmpeg | low |
| Captions | from customer profile (text) | ~none |

## Branch management

The 42 applied branches are organized into two tracks — a **macOS track** (Apple Silicon support) and a **general track** (platform-agnostic improvements) — plus a docs-only group, documented in `docs/git-workflow-actual_plan.md` (merge order, conflict hotspots, and per-PR smoke tests are documented there). `main` is synced to `upstream/main` (`e4331689`); the `dev` integration branch is created from it and receives the track PRs.

---

## `.env` variables

Guaardvark reads its configuration from the repo-root `.env` file. The variables below are the ones that matter for this RTX-on-Mac setup (grouped by concern). Paths resolve through `backend/config.py`; secrets and `DATABASE_URL` come from `.env`.

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
| `GUAARDVARK_OPENAI_API_KEY` / `GUAARDVARK_OPENAI_BASE_URL` / `GUAARDVARK_OPENAI_MODEL` | OpenAI-compatible provider — used to point the chat brain at **ollama-cloud `deepseek-v4`** so no large LLM is held in local memory. Opt-in only, and the endpoint is always explicit: **`GUAARDVARK_OPENAI_BASE_URL` is required** (there is no implicit `api.openai.com` default). The key is optional (local vLLM / Ollama need none), and a bare `OPENAI_API_KEY` (exported for another tool) is deliberately ignored. The endpoint + model in use are logged at INFO. |
| `GUAARDVARK_MISTRAL_API_KEY` / `GUAARDVARK_MISTRAL_MODEL` / `GUAARDVARK_MISTRAL_BASE_URL` | Optional Mistral provider (multi-provider escalation). |
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
| `GUAARDVARK_COMFYUI_IDLE_TIMEOUT` | Idle timeout before ComfyUI is freed. |
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
