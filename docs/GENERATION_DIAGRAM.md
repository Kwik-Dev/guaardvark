# Guaardvark Generation System — Block Diagram

How the generation stack boots (`./start.sh`), how requests flow, and where
models + outputs live.

## Mermaid diagram

```mermaid
flowchart LR
    subgraph BOOT["start.sh boot sequence"]
        A1["Postgres :5432"] --> A2["Redis :6379"]
        A2 --> A3["Backend Flask :5000"]
        A3 --> A4["Celery workers"]
        A4 --> A5["Plugin loop<br/>(auto_start + enabled)"]
    end

    subgraph PLUGINS["GPU / service plugins (each plugins/plugin-id/scripts/start.sh)"]
        P1["ComfyUI :8188<br/>image and video gen<br/>FLUX / WAN / CogVideoX"]
        P2["Audio Foundry :8206<br/>voice / music / fx"]
        P3["Ollama (local) :11434<br/>Qwen for vision<br/>(qwen3.8:27b-mlx)<br/>+ local LLM / RAG / agents"]
        P4["Other plugins<br/>swarm :8210, upscaling,<br/>video_editor, vision_pipeline,<br/>gpu_embedding, lora_trainer,<br/>training, discord"]
    end

    subgraph CLOUD["Cloud LLM (optional)"]
        C1["Ollama-cloud<br/>DeepSeek V4<br/>chatbots / text generation"]
    end

    subgraph VOICE["Backend voice (voice_api.py)"]
        V1["whisper-cli (STT)<br/>tools/voice/whisper.cpp/"]
        V2["whisper.cpp server :5800<br/>(optional, GUAARDVARK_USE_WHISPER_SERVER=1)"]
        V3["Piper TTS (subprocess)<br/>tools/voice/piper-models/"]
    end

    subgraph FRONT["Frontend"]
        F1["React / Vite :5173<br/>AudioFoundryPage, ComfyUI UI,<br/>voice chat, DocumentsPage"]
    end

    F1 -->|"HTTP /api + socket.io"| A3
    A3 -->|"AUDIO_FOUNDRY_URL<br/>http://localhost:8206"| P2
    A3 -->|"http://127.0.0.1:8188"| P1
    A3 -->|"http://127.0.0.1:11434"| P3
    A3 -->|"cloud provider (OpenAI-compatible)"| C1
    A3 -->|"STT / TTS"| VOICE

    P2 -->|"VRAM request / evict"| ORCH["GPU Memory Orchestrator<br/>(backend)"]
    P1 -->|"VRAM request / evict"| ORCH
    P3 -->|"VRAM request / evict"| ORCH

    subgraph MODELS["Model locations"]
        M1["ComfyUI models<br/>plugins/comfyui/ComfyUI/models/<br/>(checkpoints, loras, vae, clip,<br/>diffusion_models)"]
        M2["Audio Foundry models<br/>~/.cache/huggingface/hub/<br/>(chatterbox, kokoro,<br/>ace-step, stable-audio)"]
        M3["Ollama models<br/>~/.ollama/models/<br/>(qwen3.8:27b-mlx, gemma4,<br/>embeddings)"]
        M4["whisper ggml models<br/>tools/voice/whisper.cpp/models/"]
        M5["Piper voices<br/>tools/voice/piper-models/"]
    end

    P1 --> M1
    P2 --> M2
    P3 --> M3
    V1 --> M4
    V2 --> M4
    V3 --> M5

    subgraph OUT["Output locations"]
        O1["ComfyUI output<br/>plugins/comfyui/ComfyUI/output/"]
        O2["Audio Foundry output<br/>data/uploads/Audio/<br/>(registered as Documents)"]
        O3["Voice reference clips<br/>data/uploads/voice_references/"]
    end

    P1 --> O1
    P2 --> O2
    A3 --> O3
```

## ASCII fallback

```
                        ┌──────────────────────────────────────────────┐
                        │              ./start.sh boot                 │
                        │  Postgres:5432 → Redis:6379 → Flask:5000     │
                        │  → Celery workers → plugin loop              │
                        └──────────────────────────────────────────────┘
                                          │
        ┌─────────────────────────────────┼──────────────────────────────────┐
        │                                 │                                  │
   ┌────▼─────┐                    ┌──────▼──────┐                    ┌──────▼──────┐
   │  ComfyUI │                    │Audio Foundry│                    │   Ollama    │
   │  :8188   │                    │   :8206     │                    │  :11434     │
   │ img/video│                    │ voice/music │                    │ Qwen vision │
   └────┬─────┘                    │ /fx         │                    │ (qwen3.8:   │
        │                          └──────┬──────┘                    │  27b-mlx)  │
        │                                 │                          └──────┬──────┘
        │                                 │                                 │
   ┌────▼──────────────┐          ┌───────▼──────────────┐          ┌───────▼────────┐
   │ models/           │          │ ~/.cache/huggingface │          │ ~/.ollama/models│
   │ ComfyUI/models/   │          │ /hub/ (chatterbox,   │          │ (qwen3.8:27b-  │
   │ (ckpt, lora, vae) │          │  kokoro, ace-step,   │          │  mlx, gemma4,  │
   └────┬──────────────┘          │  stable-audio)      │          │  embeddings)   │
        │                         └───────┬──────────────┘          └─────────────────┘
        │                                 │
   ┌────▼──────────────┐          ┌───────▼──────────────┐
   │ output/           │          │ data/uploads/Audio/  │
   │ ComfyUI/output/   │          │ (Documents)          │
   └───────────────────┘          └──────────────────────┘

   Cloud LLM (optional):
     Ollama-cloud  DeepSeek V4  →  chatbots / text generation
     (backend routes via OpenAI-compatible cloud provider)

   Backend voice (voice_api.py):
     whisper-cli (STT)  tools/voice/whisper.cpp/models/ggml-*.bin
     whisper.cpp server :5800  (optional, GUAARDVARK_USE_WHISPER_SERVER=1)
     Piper TTS          tools/voice/piper-models/*.onnx

   All plugins share the GPU Memory Orchestrator (VRAM request/evict).
```

## Key facts

| Component | Port | Started by | Models | Output |
|-----------|------|-----------|--------|--------|
| Backend Flask | 5000 | `start.sh` | — | — |
| ComfyUI (img/video) | 8188 | `plugins/comfyui/scripts/start.sh` | `plugins/comfyui/ComfyUI/models/` | `plugins/comfyui/ComfyUI/output/` |
| Audio Foundry (voice/music/fx) | 8206 | `plugins/audio_foundry/scripts/start.sh` | `~/.cache/huggingface/hub/` | `data/uploads/Audio/` |
| Ollama (local, Qwen vision) | 11434 | `start.sh` (systemd / `ollama serve`) | `~/.ollama/models/` (qwen3.8:27b-mlx, gemma4, embeddings) | — |
| Ollama-cloud (DeepSeek V4) | — | backend cloud provider (OpenAI-compatible) | remote | — |
| whisper (STT) | — / 5800 | `start.sh` (optional server) | `tools/voice/whisper.cpp/models/` | — |
| Piper (TTS) | — | `voice_api.py` subprocess | `tools/voice/piper-models/` | — |

- **Local Ollama** runs **Qwen** for vision (`qwen3.8:27b-mlx`, the preferred
  vision-capable model for character generation) plus local LLM / RAG / agents.
- **Ollama-cloud** (optional) serves **DeepSeek V4** for chatbots and other text
  generation, routed through the backend's OpenAI-compatible cloud provider.
- **Audio Foundry** uses two sibling venvs: `venv/` (dispatcher + voice + fx) and
  `venv-music/` (ACE-Step only, driven via subprocess).
- **`AUDIO_FOUNDRY_URL`** (`.env`) points the backend at the Audio Foundry
  plugin — default `http://localhost:8206`, overridable for a remote host.
- **whisper** is STT (speech→text), **not** part of Audio Foundry's generation.
- **Piper** is an alternative local TTS engine in the backend, separate from
  Audio Foundry's Chatterbox/Kokoro.
