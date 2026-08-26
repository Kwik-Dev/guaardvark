# Guaardvark — A Practical Guide

*A self-hosted, offline-first AI workstation. Everything on your machine: agents, media generation, RAG, voice, a 70+ tool engine, and a 70+ surface for controlling it all.*


- https://guaardvark.com/features/

  > [made by a solo developer](https://github.com/guaardvark#support-the-project)

This guide is written as an introduction to Guaardvark — what it is, how it's architected, every feature area, and realistic use cases. It complements the official [`ARCHITECTURE.md`](ARCHITECTURE.md) (system design), [`CAPABILITIES.md`](../CAPABILITIES.md) (feature enumeration), and the [`README`](../README.md) (marquee overview). See the root [`VERSION`](../VERSION) file for the current release.

---

## Table of contents
- [Guaardvark — A Practical Guide](#guaardvark--a-practical-guide)
  - [Table of contents](#table-of-contents)
  - [1. What is Guaardvark?](#1-what-is-guaardvark)
  - [2. The four surfaces](#2-the-four-surfaces)
    - [The CLI \& REPL](#the-cli--repl)
  - [3. Architecture in brief](#3-architecture-in-brief)
  - [4. LLM / providers](#4-llm--providers)
    - [The chat providers](#the-chat-providers)
    - [Configuration flow](#configuration-flow)
    - ["Uncle Claude" (the escalation / guardian model)](#uncle-claude-the-escalation--guardian-model)
  - [5. Agents \& the agent screen](#5-agents--the-agent-screen)
  - [6. Skills \& recipes](#6-skills--recipes)
  - [7. Media generation](#7-media-generation)
  - [8. Knowledge: RAG, search \& memory](#8-knowledge-rag-search--memory)
    - [Lesson Pearls (teaching memory)](#lesson-pearls-teaching-memory)
  - [9. Rules \& Prompts](#9-rules--prompts)
  - [10. Swarm, the Film Crew \& the Interconnector](#10-swarm-the-film-crew--the-interconnector)
    - [Swarm (parallel coding agents) — `plugins/swarm/`](#swarm-parallel-coding-agents--pluginsswarm)
    - [Film Crew (sequential media pipeline)](#film-crew-sequential-media-pipeline)
    - [Interconnector (multi-machine sync)](#interconnector-multi-machine-sync)
  - [11. Self-improvement](#11-self-improvement)
  - [12. The plugin system](#12-the-plugin-system)
  - [13. Integrations: Discord, MCP, CLI](#13-integrations-discord-mcp-cli)
    - [Discord (`plugins/discord/`)](#discord-pluginsdiscord)
    - [MCP](#mcp)
    - [Tool calling from a prompt](#tool-calling-from-a-prompt)
  - [14. The lesson pearls memory system](#14-the-lesson-pearls-memory-system)
  - [15. Realistic use cases](#15-realistic-use-cases)
  - [16. Common gotchas (from real use)](#16-common-gotchas-from-real-use)
  - [17. Where to learn more](#17-where-to-learn-more)
- [Choosing Your AI Agent: A Comparative Manual for Guaardvark and Hermes](#choosing-your-ai-agent-a-comparative-manual-for-guaardvark-and-hermes)
  - [1. The Philosophical Divide: "Studio" vs. "Assistant"](#1-the-philosophical-divide-studio-vs-assistant)
    - [At a Glance: Comparative Profiles](#at-a-glance-comparative-profiles)
  - [2. Surfaces and Interfaces: Where the Work Happens](#2-surfaces-and-interfaces-where-the-work-happens)
    - [Interface Entry Points](#interface-entry-points)
      - [Guaardvark’s Four Surfaces](#guaardvarks-four-surfaces)
      - [Hermes’ Primary Entry Points](#hermes-primary-entry-points)
    - [Technical Distinction: Vision vs. Textual Parsing](#technical-distinction-vision-vs-textual-parsing)
  - [3. Guaardvark’s Specialty: The Film Crew Media Pipeline](#3-guaardvarks-specialty-the-film-crew-media-pipeline)
    - [The Film Crew Sequential Pipeline](#the-film-crew-sequential-pipeline)
    - [Local Media Generation and Hardware Costs](#local-media-generation-and-hardware-costs)
  - [4. Hermes’ Specialty: Scheduled Automations and Gateway Access](#4-hermes-specialty-scheduled-automations-and-gateway-access)
    - [Key Automation and Access Features](#key-automation-and-access-features)
  - [5. The Learning Loop: Lesson Pearls vs. Honcho Dialectic](#5-the-learning-loop-lesson-pearls-vs-honcho-dialectic)
    - [Guaardvark: Task-Oriented Memory](#guaardvark-task-oriented-memory)
    - [Hermes: User-Oriented Memory](#hermes-user-oriented-memory)
    - [Memory Comparison Table](#memory-comparison-table)
  - [6. The "So What?" Decision Matrix](#6-the-so-what-decision-matrix)
    - [Choose Guaardvark If...](#choose-guaardvark-if)
    - [Choose Hermes If...](#choose-hermes-if)
    - [Architectural Principles for the New Learner](#architectural-principles-for-the-new-learner)

---

## 1. What is Guaardvark?

Guaardvark is a **self-hosted, offline-first AI workstation**. It runs entirely on your hardware — no cloud APIs required — and combines:

- An **AI assistant / chat** (local or cloud LLM) with agent capabilities
- **Autonomous screen agents** that see and control a real desktop
- **Parallel coding-agent swarms** in isolated git worktrees
- **Media generation**: text/image-to-video, image gen, audio/music, neural voice, 4K/8K upscaling
- **RAG** over your own documents and code
- **Voice chat**, a 70+ tool engine, a browser UI, a CLI/REPL, and an MCP server

It's built as a **modular monolith + GPU-plugin sidecars**: a Flask backend, a React/Vite frontend, a Python CLI, and per-feature plugins (ComfyUI, Swarm, Discord, Audio Foundry, etc.).

> **Key principle:** local-first and offline by default. No telemetry or cloud unless you explicitly opt in. Media generation and embeddings stay local; only chat can optionally route to a cloud provider.

---

## 2. The four surfaces

| Surface | What it is | Best for |
|---|---|---|
| **Web UI** | React 18 / Vite / MUI dashboard | visual, interactive work (media, dashboards, agent screen) |
| **CLI + REPL** | `guaardvark` / `llx` | chat, quick actions, scripting, automation |
| **HTTP API** | Flask, ~90 auto-discovered blueprints | programmatic control, integrations |
| **MCP server** | `python -m backend.mcp` | external AI agents call Guaardvark's tools |

**Web UI themes:** the dashboard ships multiple themes (Dark Gray, Light, Guaardvark, Elon's Musk, Fallout, Vader) switched via **Settings → "Change Theme"**, persisted in the browser's localStorage. Media generation and embeddings stay local regardless of which chat provider is selected.

All four talk to the same Flask backend.

### The CLI & REPL
- `guaardvark chat "..."` / `guaardvark ask "..."` — one-shot chat
- bare `guaardvark` — an **interactive REPL** ("chat-first, with slash commands"): `/imagine`, `/video`, `/voice`, `/agent`, `/web`, `/ingest`, `/search`, `/models`, `/remember`, `/backup`, `/jobs`, `/config`, `/help`
- `guaardvark --json` for machine-readable output, `--server <url>` to point at an instance
- Command modules: `agents`, `images`, `videos`, `generate`, `search`, `index`, `rag`, `files`, `projects`, `clients`, `websites`, `tasks`, `jobs`, `backup`, `outreach`, `settings`, `family`, `status`, `dashboard`, `recipes`

---

## 3. Architecture in brief

```
Browser / CLI / MCP client
        │ HTTP + WebSocket / stdio
        ▼
Flask backend  (create_app() singleton, ~90 blueprints auto-discovered)
        │
        ├── AgentBrain (Reflex → Instinct → Deliberation routing)
        ├── Tool registry (~70 BaseTool classes, categorized + danger flags)
        ├── Agent executor (see-think-act loop)
        ├── RAG + memory/lessons
        ├── Generation (image/video/audio/voice)
        ├── Swarm + Film Crew
        └── MCP (bidirectional, default-deny)
        │
    PostgreSQL · Redis · Ollama · Celery · plugin sidecars (ComfyUI, etc.)
```

Key points:
- **Flask app singleton** — `create_app()` runs once per process; access via `get_or_create_app()`.
- **Blueprint auto-discovery** — drop a module exporting a `Blueprint` in `backend/api/` and it's registered; no central wiring.
- **Tool registry** — tools carry a category and `is_dangerous` / `requires_approval` flags; these are the MCP security boundary.
- **AgentBrain** — Reflex (<100 ms, pattern-match) → Instinct (single-shot) → Deliberation (ReACT).
- **DB schema sync** — `scripts/schema_sync.py` diffs `models.py` against the live DB (not classic migration replay).
- **Async** — Celery workers + beat for video, training, self-improvement, outreach, RAG autoresearch.

The definitive system reference is [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 4. LLM / providers

Chat/assistant LLM selection is provider-driven (`backend/services/llm_provider.py`). Local Ollama is the default and stays fully offline unless you opt in.

### The chat providers
|Provider|How to enable|Notes|
|---|---|---|
|**Ollama (local)**|default (`127.0.0.1:11434`)|the default; always available|
|**Remote/cloud Ollama**|`OLLAMA_BASE_URL=https://<host>:11434` in `.env`|point Ollama at a remote/cloud instance|
|**Mistral (cloud)**|`MISTRAL_API_KEY` + master switch + select provider|the built-in cloud chat provider|
|**OpenAI-compatible (cloud)**|`GUAARDVARK_OPENAI_API_KEY` / `_BASE_URL` / `_MODEL` + enable + select|one client covers OpenAI, OpenRouter, Groq, Together, vLLM, Ollama's `/v1`, Gemini (OpenAI-compat)|

OpenAI-compatible is a single provider that speaks the OpenAI chat-completions protocol, so the same code works for many endpoints.

The Settings → Model Management UI exposes the **master switch**, a provider toggle (Ollama / Mistral / OpenAI-compatible), and **model dropdowns** for Mistral and OpenAI-compatible (backed by `/api/llm/provider/<provider>-model`). The locally saved chat-model choice also lives in **`data/active_model.json`** (`active_model`), read by the backend at startup if the model is still available in Ollama.

### Configuration flow
Add the key to **`.env`**, then (via Settings → Model Management in the UI, or API):
```
POST /api/llm/cloud-enabled {"enabled":true}      # master switch (default OFF)
POST /api/llm/provider {"provider":"openai|mistral"}
POST /api/llm/provider/openai-model {"model":"..."}   # or /mistral-model
POST /api/llm/provider/test
```
- **Embeddings/RAG always stay on local Ollama** regardless of the chat provider (so the RAG vector store stays consistent).
- Reverting to local = select `ollama` or flip the master switch off.

### "Uncle Claude" (the escalation / guardian model)
A separate, optional **guardian/mentor** layer — not the main chat LLM:
- **Escalation** — route hard problems / every message (by mode) to a cloud provider
- **Guardian** — review proposed **self-improvement** code changes before apply
- **Advisor** — system health recommendations

Configured via `GUAARDVARK_ESCALATION_PROVIDER` / `_MODEL` / `_API_KEY` / `_BASE_URL` (Anthropic or any OpenAI-compatible). Escalation modes:
- `manual` (default) — no auto escalation; only guardian review + on-demand API
- `smart` — escalate **when the local model fails** (auto on exception/empty)
- `always` — every chat response routed through the escalation provider

> Note: as of this guide, only `always` and `smart` are wired in the chat engine. `smart` was implemented to escalate on local-model failure.

---

## 5. Agents & the agent screen

The **Agent screen** (Agent Vision Control) lets an agent **see and control a real desktop**:

- **see-think-act loop** (`agent_control_service.py`): capture screen → analyze with a vision model (e.g. `gemma4:e4b`, emitting `box_2d` click coordinates) → decide → act
- **Action vocabulary**: `click`, `right_click`, `double_click`, `drag`, `hover`, `type`, `hotkey`, `scroll`, `move`, `wait`, `navigate`, `done`
- **ServoController**: closed-loop click targeting (aim → verify → correct)
- **Recipes**: deterministic action sequences that bypass the loop for reliability
- **Self-calibration**: `servo_knowledge_store` + `servo_self_improvement` tune per-model scale factors

**Platform caveat:** the marquee screen agents run on a **Linux virtual desktop (Xvfb + XFCE)**. On macOS, `DESKTOP_AUTOMATION_ENABLED` defaults to `false`, so the screen-agent path is effectively off unless enabled. A **Raspberry Pi** (Linux/ARM) is a natural fit for the screen stack (see [use cases](#realistic-use-cases)).

## 6. Skills & recipes

Guaaardvark's "skills" are called **agent recipes** — deterministic, reusable action sequences stored in `data/agent/recipes.json`. They **bypass the see-think-act loop** for reliability:

```json
{
  "description": "Navigate to a domain-shaped URL in the current tab.",
  "triggers": ["^(?:navigate|go)\\s+to\\s+..."],
  "steps": [{ "action": "hotkey", "keys": ["ctrl", "l"] }, ...]
}
```

- **`triggers`** = regex matched against natural-language requests
- **`steps`** = ordered deterministic actions (hotkey / type / click / wait)
- optional **`success_proof`** = a vision-readable condition that must hold for the recipe to count as successful
- CLI: `guaardvark recipes list | show | validate`

**Distinguish:** `Rules & Prompts` are *prompt bundles* that steer what the model is told; **recipes/skills** are *executable actions*; **plugins** are *runnable functionality*. They're complementary, not the same.

---

## 7. Media generation

|Area|What's built in|
|---|---|
|**Images**|Offline diffusers generator (Z-Image Turbo, SDXL) + ComfyUI/FLUX via ComfyUI + batch + face/anatomy controls|
|**Video**|Wan 2.2, CogVideoX, LTX; resolution tiers, frame interpolation, prompt enhancement, ComfyUI + offline fallbacks|
|**Audio/music**|ACE-Step full-song generation, Stable Audio Open FX, neural voice (Chatterbox/Kokoro/Piper), consent-gated voice cloning|
|**Upscaling**|Real-ESRGAN family, HAT-L, NMKD, etc. → 4K/8K, two-pass, video frame-by-frame|
|**Editing**|built-in Shotcut-lite timeline editor (video/text/audio lanes)|

**Z-Image via ComfyUI (reuse your ComfyUI instead of re-downloading):**
```
GUAARDVARK_ZIMAGE_USE_COMFYUI=1   # in .env
```
When set, plain Z-Image generation routes to your running ComfyUI (reusing its `z_image_turbo_bf16.safetensors`) instead of the offline download path. Model filenames overridable via `GUAARDVARK_ZIMAGE_UNET/_CLIP/_VAE/_SAMPLER/_SCHEDULER`, and cfg via `GUAARDVARK_ZIMAGE_CFG` (default 1.0 — a ComfyUI KSampler needs `cfg ≥ 1.0`; `cfg 0` yields junk).

---

## 8. Knowledge: RAG, search & memory

- **RAG** — hybrid BM25 + vector, AST-aware code chunking, per-project indexes, entity extraction, RAG Autoresearch
- **`/search`** — semantic search over indexed docs
- **`/ingest <path>`** — index files/dirs for RAG
- **Memory** — `AgentMemory` long-term store (notes, facts, instructions) with importance/confidence/trust-weight/rank

### Lesson Pearls (teaching memory)
- A **pearl** = one "this worked" capture, recorded when you **thumbs-up (👍)** a response/tool call (`ToolFeedback.positive=True`).
- A **lesson** = a user-bracketed group of pearls: `POST /api/lessons/start` → 👍 during → `POST /api/lessons/<id>/end`.
- **Distillation** collapses a lesson's ordered pearls into **one structured, reusable `AgentMemory`** (title + ordered, parameterized steps — *"find this and do that,"* not self-reflection).
- 👍 works in any chat and is always recorded, but **structured lesson distillation requires the Begin/End bracket**.

---

## 9. Rules & Prompts

Declarative **prompt bundles** that steer what the LLM is told. Rules are DB rows (`Rule` model) with:
- **`level`** (scope + precedence): `SYSTEM`, `PROJECT`, `CLIENT`, `USER_GLOBAL`, `USER_SPECIFIC`, `PROMPT`, `LEARNED`
- **`type`**: `PROMPT_TEMPLATE`, `QA_TEMPLATE`, `COMMAND_RULE`, `FILTER_RULE`, `FORMATTING_RULE`, `SYSTEM_PROMPT`, `OTHER`
- **`command_label`** (unique, e.g. `/createfile`), **`rule_text`**, **`target_models`** (default `__ALL__`), **`is_active`**, **`project_id`**

**How a rule is used:** at runtime a rule is fetched by its `command_label`/level and injected into the prompt:
- **Command rules** — `get_active_command_rule(label, db, model)` — become the prompt for a command (e.g. codegen, CSV generation)
- **SYSTEM rules** — merged into the chat system prompt (ordered by priority)
- **QA templates** — default prompt template

Precedence: `SYSTEM (0) → LEARNED (1) → everything else`.

---

## 10. Swarm, the Film Crew & the Interconnector

### Swarm (parallel coding agents) — `plugins/swarm/`
Runs **up to N coding agents in parallel**, each in an isolated **git worktree**, then merges with dependency-aware conflict detection and test validation.
- **Backends**: `claude` (Claude Code, cloud) or `cline` (local, `ollama/gemma4:e4b`)
- **Flight Mode**: auto-detect offline → fall back to local
- Config in `plugins/swarm/config.yaml`; driven by the `/swarm` UI
- CLI: `python plugins/swarm/swarm_cli.py launch <plan.md> [--flight-mode] [--max-agents N] [--auto-merge]`

> **Naming note:** `plugins/swarm/` (parallel coding) is **not** the Film Crew. The Film Crew is a *separate, sequential* video-production pipeline in `backend/services/swarm/` (a legacy directory name) backed by the `/film-crew` UI.

### Film Crew (sequential media pipeline)
Five role agents turn a logline into a finished video: **Screenwriter → Casting (LoRAs) → Cinematographer → Storyboard → Editor**. Runs in `/backend/services/swarm/` + `production_swarm_tasks.py`.

### Interconnector (multi-machine sync)
A master/client layer that **syncs data across Guaardvark instances**:
- syncs **chat history**, **learnings** (self-improvement fixes), **images**, **files**, **backups**, **hardware profiles**
- **broadcasts** entity/file batches with approval gates; pushes safety **directives**

> **It does not create live agent-to-agent conversation.** It's a data-sync + control layer. For cross-machine *control*, combine it with each node's **HTTP API / MCP**. That combination is what you'd use to build a **fleet of software robots** (per-node command + telemetry via API; fleet-wide config/learnings/directives via Interconnector).

---

## 11. Self-improvement

The engine finds and fixes bugs automatically — **with human gates**:
```
test → dispatch code_assistant agent → verify → broadcast (via Interconnector)
```
**Modes:**
- **Scheduled** — periodic test suite (`pytest` on a subset), parse failures, fix, re-verify
- **Reactive** — triggered by **runtime exceptions** (the app's 500 handler extracts the traceback, with a per-file:line cooldown) — *not* log-file scanning
- **Directed** — manual tasks

**Safety:**
- toggles `self_improvement_enabled`, `codebase_locked` (+ lockfile), `self_improvement_apply_enabled`
- optional **Uncle Claude** guardian review
- every fix staged as a **PendingFix** — a human approves/rejects in the Settings UI before it's applied
- audit trail (`SelfImprovementRun`, `changes_made`, JSONL)

---

## 12. The plugin system

Plugins are packaged functionality under `plugins/<id>/` with a `plugin.json` manifest (id, type, port, `vram_estimate_mb`, endpoints, config defaults).
- **Discovery:** `PluginRegistry` scans `plugins/` for `plugin.json` at startup; live state in `data/plugin_state.json`
- **Lifecycle:** `PluginManager` starts/stops/health-checks; service plugins run `scripts/start.sh` via a CUDA-safe `plugin_runner` sidecar
- **Types:** `service` (Discord, ComfyUI, Swarm), `extension`, `tool` (adds agent tools), `ui`
- **GPU/VRAM:** heavy GPU plugins are arbitrated so they don't trample each other or Ollama

**A plugin is NOT a rule.** Rules are prompt text that steer the model; plugins are runnable functionality. A `tool` plugin registers tools the agents can call (see Section 13).

---

## 13. Integrations: Discord, MCP, CLI

### Discord (`plugins/discord/`)
A Discord bot (port 8200) that fronts Guaardvark:
- slash commands `/chat`, `/claude`, `/imagine`, `/video`, `/search`, `/status`, `/voice`
- channel chat (responds to @mention), supervised outreach, rate limits, admin roles, VIP welcome DMs
- needs `DISCORD_BOT_TOKEN` and a server to invite the bot into

### MCP
- **As a server:** `python -m backend.mcp` (stdio) exposes tools/resources to MCP clients (Claude Desktop, Cursor, Zed) under a **default-deny** policy (`backend/mcp/config.py`) — desktop/agent/system/browser tools are hidden by default; only safe tools + read-only outputs are exposed.
- **Startup integration:** `GUAARDVARK_START_MCP=1` makes `start.sh` smoke-test the MCP server (`list-tools`) and print ready-to-paste client config snippets (`GUAARDVARK_MCP_CLIENTS`). `stop.sh` reaps any orphaned MCP process.
- **As a client:** `mcp_connect` / `mcp_execute` call external MCP servers.
- **Caveat:** the server is **stdio-only** — clients must spawn it (not a persistent daemon you can point many agents at). For remote/agent control, use the HTTP API.

### Tool calling from a prompt
Agents call tools via a **structured `<tool_call>`** (XML or JSON) the LLM emits:
```xml
<tool_call><tool>image_generate</tool><prompt>a red fox</prompt></tool_call>
```
`parse_tool_calls_xml` extracts name + params; the executor looks up `registry.get_tool(name)` and runs it, returning the result as an observation.

---

## 14. The lesson pearls memory system

Covered in Section 5 — a compact summary:
- **Pearls** = 👍-captured "this worked" moments
- **Lessons** = Begin/End-bracketed groups of pearls
- **Distillation** = LLM turns each lesson into a **structured, parameterized, actionable** `AgentMemory` (title + steps) that's recalled later
- **Reconciler (Phase 5)** = groups cross-session `belief_update` memories; once ≥3 sessions agree, proposes an edit to a knowledge file (`self_knowledge_compact.md`, `recipes.json`) as a **PendingFix**

---

## 15. Realistic use cases

| Use case | How Guaardvark helps |
|---|---|
| **Local AI assistant with your data** | Chat + RAG over your documents, all offline |
| **Autonomous screen automation (robot)** | Agent screen + see-think-act + recipes drive a real desktop — best on **Linux / Raspberry Pi** (not macOS by default) |
| **Media content machine** | image/video/audio/music generation + upscaling + editing on your GPU |
| **AI coding swarms** | parallel agents in worktrees for large refactors (Claude Code or local cline; pi can be added as a backend later) |
| **Cross-machine learning** | self-improvement → learnings → broadcast to other instances via the Interconnector |
| **A fleet of software robots** | per-node **API/MCP** for real-time control + **Interconnector** for fleet-wide config/sync/directives |
| **SEO/competitive content** | FileGen batch CSV with a **competitor URL** field: it scrapes the competitor page, extracts keywords/products, and steers the generated WordPress pages to target the same space |
| **Chat with Guaardvark from Discord** | the Discord plugin |
| **Programmatic control from an AI agent (pi)** | HTTP API / CLI (pi has no native MCP client yet; a pi MCP bridge is planned) |

---

## 16. Common gotchas (from real use)

- **Port 5000 conflict (macOS):** ControlCenter / AirPlay Receiver owns `:5000`. Set a different backend port (`FLASK_PORT=5055`) in `.env`, then `./start.sh`.
- **Python 3.12 required.** The ML stack has no wheels for 3.13/3.14. On macOS, `brew install python@3.12` (the bootstrap does this automatically).
- **ComfyUI "not installed" is about the bundled plugin**, not your external ComfyUI — routing just needs `COMFYUI_URL` to point at a reachable server.
- **Z-Image through ComfyUI:** set `GUAARDVARK_ZIMAGE_USE_COMFYUI=1`; keep the ComfyUI KSampler cfg ≥ 1.0.
- **Smart escalation** previously listed "auto when local fails" but wasn't implemented; that behavior is now implemented (local failure → escalation provider).
- **The main chat LLM** supports Ollama / Mistral / OpenAI-compatible. OpenAI/OpenRouter as the *main* chat model requires the OpenAI-compatible provider + base URL.
- **Pillars** are separate: `Rules & Prompts` (prompt bundles), `recipes/skills` (deterministic screen actions), `plugins` (runnable functionality), `MCP` (agent protocol).

---

## 17. Where to learn more

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — authoritative system design, DB, config, safety-critical systems
- [`CAPABILITIES.md`](../CAPABILITIES.md) — exhaustive enumerated features / tools / models / plugins
- [`README`](../README.md) — marquee overview and quick start
- [`INSTALL.md`](../INSTALL.md) — install details
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — contributing / development
- `docs/skills/guaardvark/SKILL.md` — the Guaardvark CLI skill (for AI coding agents)


---

# Choosing Your AI Agent: A Comparative Manual for Guaardvark and Hermes

As the landscape of autonomous computing shifts from simple large language model (LLM) wrappers to complex agentic systems, architects must choose between two dominant deployment patterns. This manual evaluates Guaardvark, a localized media and automation workstation, and Hermes, a persistent, multi-platform assistant. While both offer autonomous capabilities, their underlying architectures—from hardware requirements to memory models—are optimized for radically different operational environments.

## 1. The Philosophical Divide: "Studio" vs. "Assistant"

The choice between these systems is a choice of environment. Guaardvark is a "Studio on one box," designed to centralize high-intensity media production and desktop automation on a single, powerful workstation. It prioritizes Flight Mode, a fully offline operation protocol that ensures data remains local and generation remains unmetered.

Hermes follows the philosophy of "The agent that grows with you." It is designed as a lightweight, persistent entity that follows the user across devices, leveraging cloud infrastructure or low-power servers. Hermes focuses on long-term user modeling and multi-channel accessibility rather than localized heavy lifting.

### At a Glance: Comparative Profiles

| Metric | Guaardvark | Hermes |
|---|---|---|
| Primary Identity | Self-hosted AI Workstation | Self-improving Autonomous Agent |
| Deployment Model | Local-first; "Flight Mode" Offline | Multi-platform (Cloud, VPS, or Termux) |
| UI Paradigm | Visual Dashboard and Virtual Desktop | TUI and Messaging Gateways |
| Hardware Reqs | High (Dedicated NVIDIA GPU) | Flexible (Low-cost VPS to GPU clusters) |

The divergence begins at the interface layer; where you work determines which agent's "hands" are most effective for the task at hand.

## 2. Surfaces and Interfaces: Where the Work Happens

Interface design dictates how an agent interacts with its host system. Guaardvark relies on vision-based coordinate control, whereas Hermes utilizes text-based accessibility structures.

### Interface Entry Points

#### Guaardvark’s Four Surfaces

1. Web UI: A React/Vite dashboard featuring a draggable VNC viewer. This allows users to watch the agent control a real Linux virtual desktop (Xvfb + XFCE) in real-time.
2. CLI + REPL (llx): For chat-first interactions, scripting, and modular command execution.
3. HTTP API: Approximately 90 auto-discovered modules providing full programmatic control.
4. MCP Server: A bidirectional server that exposes tools to external clients like Claude Desktop or Cursor.

#### Hermes’ Primary Entry Points

1. Terminal User Interface (TUI): A high-performance command-line environment with multiline editing and streaming tool output.
2. Messaging Gateways: Direct interaction via Telegram, Discord, Slack, WhatsApp, Signal, or Email.

### Technical Distinction: Vision vs. Textual Parsing

A critical architectural difference lies in how these agents "see" the computer. Guaardvark utilizes a vision-based coordinate system (e.g., Gemma4 vision) to emit precise pixel coordinates. It employs a "ServoController" strategy—moving, verifying, and correcting—to interact with any standard Linux desktop application.

Hermes, conversely, interacts with the digital world primarily through text-based accessibility trees and cloud-hosted browsers (such as Browserbase). By assigning reference IDs to DOM elements, it navigates the web through structured data rather than raw visual input, making it exceptionally fast for data-heavy tasks but less suited for non-web GUI automation.

## 3. Guaardvark’s Specialty: The Film Crew Media Pipeline

Guaardvark excels in high-end media creation through its "Director Engine." Unlike simple text-to-video prompts, Guaardvark employs Sequential Parallelism. The engine breaks a creative logline into a formal production pipeline where each stage must be completed and validated before the next begins.

### The Film Crew Sequential Pipeline

1. Screenwriter: Generates the script and shot-by-shot scene breakdown.
2. Casting: Assigns visual identities to local LoRAs or stock characters.
3. Cinematographer: Defines camera angles, lenses, and movement for every shot.
4. Storyboard: Generates keyframe images via the local image pipeline.
5. Editor: Assembles clips into a finished video using the built-in timeline editor.

### Local Media Generation and Hardware Costs

Running state-of-the-art media models locally requires specific VRAM allocations. Architects should note that while some weights may fit on smaller cards, generation preflight often creates a higher memory ceiling.

| Model Family | Primary Capability | VRAM Requirement |
|---|---|---|
| Wan 2.2 | Text/Image-to-Video | 16GB (Preflight Req) |
| CogVideoX | Text-to-Video | 16GB - 20GB |
| LTX (Distilled) | High-Duration Video | 14GB |
| ACE-Step | Full-Song Music Gen | 10GB |

By breaking complex creative tasks into specialized roles, Guaardvark ensures character consistency and cinematic composition that monolithic prompts cannot achieve.

## 4. Hermes’ Specialty: Scheduled Automations and Gateway Access

Hermes is built for persistence and lower barriers to entry. It is the ideal choice for users who require a 24/7 agent presence without maintaining high-end local hardware.

### Key Automation and Access Features

- Nous Portal: For students or architects who wish to avoid the friction of API-key collection, the Nous Portal provides a "one-stop" entry point. It covers 300+ models and provides a tool gateway for web search, image generation, and cloud browsing under a single subscription.
- Scheduled Automations (Cron): Hermes includes a natural-language scheduler. Users can command the agent to "send a briefing on my server's health to Telegram every morning at 8 AM," and the agent will handle the execution and delivery unattended.
- Serverless Persistence: Through integrations with Modal or Daytona, Hermes can exist in a "hibernation" state. This allows the agent's environment to wake on demand and hibernate when idle, providing massive cost savings compared to an always-on local machine.
- Messaging Gateways: Hermes can be reached via Telegram, Discord, and Slack, allowing for cross-platform continuity. A user can initiate a research task on their phone via Telegram and review the compiled Markdown file later on their laptop.

## 5. The Learning Loop: Lesson Pearls vs. Honcho Dialectic

Both systems are designed to self-improve, but they target different areas of development: Task-Oriented Memory versus User-Oriented Memory.

### Guaardvark: Task-Oriented Memory

Guaardvark uses the Lesson Pearls system to learn how to perform actions. When a user provides a thumbs-up (👍) to a tool call, the system captures a "Pearl."

- The Distillation Process: These pearls are bracketed into "Lessons." A local LLM then distills these successful turns into structured, parameterized "Recipes."
- Outcome: The agent gains a permanent new skill, such as a deterministic recipe for navigating a specific company's internal payroll software.

### Hermes: User-Oriented Memory

Hermes integrates with Honcho to create a dual-peer modeling system. It focuses on learning who the user is.

- The Dialectic Model: It maintains two distinct representations—the User peer (observed preferences and goals) and the AI peer (agent knowledge).
- Outcome: Hermes ensures cross-session continuity. If you mention a preference for Rust and dark-mode coding in one session, Hermes recalls and applies those preferences in every future conversation, regardless of the device used.

### Memory Comparison Table

| Feature | Guaardvark (Lesson Pearls) | Hermes (Honcho) |
|---|---|---|
| Capture Method | Positive Feedback (👍) | Dialectic turns / Observations |
| Recall Type | Parameterized Executables | Peer-card injection and Semantic search |
| Primary Focus | Task-Learning (Skills) | User-Learning (Identity) |

## 6. The "So What?" Decision Matrix

The final decision rests on your hardware constraints and the nature of your required workflows.

### Choose Guaardvark If...

- You require an offline-first environment (Flight Mode) for sensitive data.
- You need an agent that can see and control a real Linux virtual desktop to automate non-web software.
- You possess an NVIDIA GPU (16GB+ VRAM) and want to generate 4K video, music, and neural voices without per-token fees.

### Choose Hermes If...

- You require a bot that delivers automated reports via Telegram or other messaging platforms.
- You prefer a cloud-elastic model that can run on a $5 VPS, Termux (Android), or serverless infrastructure like Modal.
- You want to "skip the keys" by using the Nous Portal for immediate access to high-end models and tool use.

### Architectural Principles for the New Learner

1. Local Hardware Determinism vs. Cloud Elasticity: Guaardvark is a "thick client" requiring high-end VRAM for its media studio; Hermes is "infrastructure-agnostic," designed to hibernate on cloud sandboxes or run on mobile.
2. Vision-Based Control vs. Messaging Ubiquity: Guaardvark excels at "Computer Use" by seeing and clicking a real UI. Hermes excels at "Presence," living in your chat apps and performing web-based research.
3. Task-Specific Distillation vs. User-Centric Modeling: Guaardvark learns how to execute sequences (Recipes); Hermes learns how to relate to you (Honcho).

Whether you deploy the localized powerhouse of Guaardvark or the persistent companion of Hermes, you are engaging with the frontier of autonomous, self-improving systems.
