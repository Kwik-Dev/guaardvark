# Guaardvark — Feature Guide with Screenshots

*A visual walkthrough of every major surface in Guaardvark, a self-hosted, offline-first AI workstation. One feature per page, ordered as captured.*

---

<div style="page-break-after: always;"></div>

## 1. Dashboard

![Dashboard](img/Screenshot%20Capture%20-%20http---localhost-5173-.png)

The Guaardvark dashboard is the landing page of the React/Vite web UI. It surfaces system health, active jobs, recent activity, and quick-access entry points to every major surface — chat, agents, media generation, tools, and plugins — in one glance. Multiple themes (Dark Gray, Light, Guaardvark, Fallout, Vader, Elon's Musk) are available from Settings.

<div style="page-break-after: always;"></div>

## 2. Chat

![Chat](img/Screenshot%20Capture%20-%20http---localhost-5173-chat.png)

The chat surface is Guaardvark's primary conversational interface. Every message enters the three-tier **AgentBrain** router: **Reflex** (pattern-match, <100 ms, zero LLM calls) → **Instinct** (single pre-warmed shot) → **Deliberation** (full ReACT tool-use loop). You can chat with a local Ollama model or route to a cloud provider (Mistral, OpenAI-compatible). A thumbs-up (👍) on any response captures a **Lesson Pearl** for the memory system.

<div style="page-break-after: always;"></div>

## 3. Code Editor

![Code Editor](img/Screenshot%20Capture%20-%20http---localhost-5173-code-editor.png)

A built-in **Monaco** code editor for viewing and editing files directly in the browser. It integrates with the agent tool ecosystem so an AI assistant can read, write, and patch files in the workspace. The editor supports syntax highlighting for all major languages and is the surface used by the self-improvement engine to display proposed fixes.

<div style="page-break-after: always;"></div>

## 4. Documents (RAG)

![Documents](img/Screenshot%20Capture%20-%20http---localhost-5173-documents.png)

The **Documents** screen manages the RAG (retrieval-augmented generation) pipeline. Use `/ingest <path>` from the CLI or this UI to index files and directories. Guaardvark builds a hybrid **BM25 + vector** store with AST-aware code chunking and entity extraction. Indexed documents become searchable knowledge the chat agent can ground its answers in — fully offline, via local Ollama embeddings.

<div style="page-break-after: always;"></div>

## 5. Image Generation

![Image Generation](img/Screenshot%20Capture%20-%20http---localhost-5173-images.png)

Generate images offline using the built-in diffusers pipeline (**Z-Image Turbo**, **SDXL**) or route to **ComfyUI** for FLUX and other engines. Controls include prompt, resolution, batch count, and face/anatomy settings. On Apple Silicon, set `GUAARDVARK_ZIMAGE_USE_COMFYUI=1` to route Z-Image generation through a ComfyUI server instead of the CUDA-only download path.

<div style="page-break-after: always;"></div>

## 6. Notes

![Notes](img/Screenshot%20Capture%20-%20http---localhost-5173-notes.png)

A persistent notes surface for freeform text, knowledge capture, and agent-generated summaries. Notes integrate with the broader memory system — the **AgentMemory** long-term store with importance, confidence, trust-weight, and rank — and can be searched alongside RAG-indexed documents.

<div style="page-break-after: always;"></div>

## 7. Film Crew

![Film Crew](img/Screenshot%20Capture%20-%20http---localhost-5173-film-crew.png)

The **Film Crew** is Guaardvark's marquee sequential media pipeline. Five role agents turn a logline into a finished video: **Screenwriter** → **Casting** (LoRA assignment) → **Cinematographer** → **Storyboard** (keyframe generation) → **Editor** (timeline assembly). Renders are resumable — each shot's clip is saved to the database the moment it finishes, so interruptions only lose the in-flight shot, not the whole film. Run via the `/film-crew` UI.

<div style="page-break-after: always;"></div>

## 8. Cast / LoRA Management

![Cast](img/Screenshot%20Capture%20-%20http---localhost-5173-cast.png)

The **Cast** screen manages visual identities — **LoRA** models and stock characters — that the Film Crew's Casting agent assigns to roles. Consistent character faces across every shot of a film are achieved by reusing the same LoRA seed throughout the pipeline. Upload, browse, and organize your character library here.

<div style="page-break-after: always;"></div>

## 9. Cast Detail

![Cast Detail](img/Screenshot%20Capture%20-%20http---localhost-5173-cast-1.png)

A detail view for a single cast member / LoRA: preview images, trigger words, associated metadata, and the films/shots where it has been used. This is where you curate and refine the character library that gives Film Crew productions their visual consistency.

<div style="page-break-after: always;"></div>

## 10. Music Video

![Music Video](img/Screenshot%20Capture%20-%20http---localhost-5173-music-video.png)

The **Music Video** generator pairs a generated or uploaded audio track with a video clip produced by **Wan 2.2 image-to-video**. It handles audio-driven clip selection, beat-aligned cuts, and prompt enhancement. Like Film Crew, it renders via the ComfyUI pipeline and is resumable on interruption.

<div style="page-break-after: always;"></div>

## 11. Video Editor

![Video Editor](img/Screenshot%20Capture%20-%20http---localhost-5173-video-editor.png)

A built-in **Shotcut-lite timeline editor** with video, text, and audio lanes. Trim, arrange, layer, and export clips — the final stage of the Film Crew pipeline and a standalone editor for any video project. Supports overlays, transitions, and direct export.

<div style="page-break-after: always;"></div>

## 12. Video Generation

![Video Generation](img/Screenshot%20Capture%20-%20http---localhost-5173-video.png)

Generate video offline with **Wan 2.2**, **CogVideoX**, or **LTX**. Controls include resolution tiers, frame count, denoising steps, frame interpolation, and prompt enhancement. On Apple Silicon (MPS), the recommended default is **`wan22-5b`** (~9.5 GB VRAM); the heavier A14B MoE model targets 16 GB CUDA cards.

<div style="page-break-after: always;"></div>

## 13. Batch Image Generation

![Batch Images](img/Screenshot%20Capture%20-%20http---localhost-5173-batch-images.png)

The batch image screen generates many images in a single job — for cast keyframes, storyboards, or bulk asset creation. Define a prompt matrix, resolution, and batch parameters; the job runs asynchronously via Celery and saves outputs to the media library as each image finishes.

<div style="page-break-after: always;"></div>

## 14. Audio / Music Generation

![Audio](img/Screenshot%20Capture%20-%20http---localhost-5173-audio.png)

The **Audio Foundry** generates full songs with **ACE-Step**, sound effects with **Stable Audio Open FX**, and neural voice with **Chatterbox**, **Kokoro**, or **Piper**. Voice cloning is supported with a consent gate. Outputs are fully offline and saved to the local media library for use in music videos, films, or standalone playback.

<div style="page-break-after: always;"></div>

## 15. Video Text Overlay

![Video Text Overlay](img/Screenshot%20Capture%20-%20http---localhost-5173-video-text-overlay.png)

Add text overlays to video clips — titles, captions, subtitles, or stylized typography. Configure font, size, color, position, and timing. This is the final polish step before export, available both in the Video Editor timeline and as a dedicated overlay tool.

<div style="page-break-after: always;"></div>

## 16. Clients

![Clients](img/Screenshot%20Capture%20-%20http---localhost-5173-clients.png)

The **Clients** screen manages client entities — organizations or people you do work for. Clients scope projects, rules, and file generation jobs. Each client can have its own set of projects, preferred models, and rule overrides, enabling one Guaardvark instance to serve multiple customers from a single database.

<div style="page-break-after: always;"></div>

## 17. Projects

![Projects](img/Screenshot%20Capture%20-%20http---localhost-5173-projects-1.png)

**Projects** group related work under a client. A project owns its own RAG index, file-generation jobs, rules, and agent context. Switching projects re-scopes the agent's knowledge and tool behavior, so conversations and generation stay focused on the current body of work.

<div style="page-break-after: always;"></div>

## 18. Websites

![Websites](img/Screenshot%20Capture%20-%20http---localhost-5173-websites.png)

Manage website entities — domains, hosting details, CMS type, and generation templates. The Websites surface feeds the **FileGen** batch content engine, which can generate WordPress-ready pages with SEO targeting, including competitor-URL scraping that extracts keywords and products to aim generated content at the same search space.

<div style="page-break-after: always;"></div>

## 19. Tasks

![Tasks](img/Screenshot%20Capture%20-%20http---localhost-5173-tasks.png)

The **Tasks** screen lists asynchronous jobs across the system — media generation, RAG indexing, file generation, self-improvement runs, and outreach dispatches. Each task shows status, progress, and owner. Tasks are executed by **Celery** workers and can survive backend restarts.

<div style="page-break-after: always;"></div>

## 20. Outreach

![Outreach](img/Screenshot%20Capture%20-%20http---localhost-5173-outreach.png)

**Social outreach** is supervised by default — drafts queue in an approval list and nothing posts without explicit human sign-off. Per-platform cadence limits, a JSONL audit trail, persona enforcement, and a global kill switch all apply. Supports multiple platforms with operator identity driven by config (never hardcoded).

<div style="page-break-after: always;"></div>

## 21. Rules & Prompts

![Rules](img/Screenshot%20Capture%20-%20http---localhost-5173-rules.png)

Declarative **prompt bundles** that steer what the LLM is told. Rules are scoped by level (`SYSTEM`, `PROJECT`, `CLIENT`, `USER_GLOBAL`, `USER_SPECIFIC`, `PROMPT`, `LEARNED`) and type (command rule, QA template, filter, formatting, system prompt). Command rules with a unique `command_label` become the prompt for a specific task — e.g. `/createfile` or CSV generation — and are injected at runtime.

<div style="page-break-after: always;"></div>

## 22. Tools

![Tools](img/Screenshot%20Capture%20-%20http---localhost-5173-tools.png)

The **tool registry** surfaces the ~70 `BaseTool` subclasses Guaardvark registers at startup. Each tool carries a **category** and flags (`is_dangerous`, `requires_approval`) that form the MCP security boundary. The Tools screen shows what's available, which require approval, and which are exposed to external agents via the MCP server.

<div style="page-break-after: always;"></div>

## 23. Agents (Agent Vision Control)

![Agents](img/Screenshot%20Capture%20-%20http---localhost-5173-agents.png)

The **Agent screen** (Agent Vision Control) lets an AI agent **see and control a real desktop** via a see-think-act loop: capture screen → analyze with a vision model (emitting `box_2d` click coordinates) → decide → act. The action vocabulary includes click, right_click, double_click, drag, hover, type, hotkey, scroll, move, wait, navigate, and done. A closed-loop **ServoController** aims, verifies, and corrects each click.

> Note: the marquee screen agents run on a Linux virtual desktop (Xvfb + XFCE); on macOS this path is off by default.

<div style="page-break-after: always;"></div>

## 24. File Generation

![File Generation](img/Screenshot%20Capture%20-%20http---localhost-5173-file-generation.png)

The **FileGen** engine generates structured content at scale — CSV, JSON, Markdown, WordPress pages — from templates and prompts. Each job is scoped to a project and client, and can incorporate RAG-sourced knowledge and competitor-URL scraping for SEO targeting. Jobs run asynchronously via Celery.

<div style="page-break-after: always;"></div>

## 25. Swarm

![Swarm](img/Screenshot%20Capture%20-%20http---localhost-5173-swarm.png)

The **Swarm Orchestrator** runs up to N coding agents in parallel, each in an isolated **git worktree**, then merges with dependency-aware conflict detection and test validation. Backends: `claude` (Claude Code, cloud) or `cline` (local, `ollama/gemma4:e4b`). Flight Mode auto-detects offline operation and falls back to local. Driven by the `/swarm` UI or the `swarm_cli.py launch` command.

<div style="page-break-after: always;"></div>

## 26. Autoresearch

![Autoresearch](img/Screenshot%20Capture%20-%20http---localhost-5173-autoresearch.png)

**RAG Autoresearch** schedules background agents that continuously improve the indexed knowledge base — discovering, ingesting, and chunking new documents without manual `/ingest`. It runs as a Celery beat job and keeps the RAG store current as your document corpus grows.

<div style="page-break-after: always;"></div>

## 27. Plugins

![Plugins](img/Screenshot%20Capture%20-%20http---localhost-5173-plugins.png)

The **Plugin Manager** discovers and controls Guaardvark's GPU-service sidecars. Each plugin (ComfyUI, Swarm, Discord, Audio Foundry, etc.) ships a `plugin.json` manifest with id, port, VRAM estimate, and endpoints. The manager starts, stops, and health-checks each one; the **System Resource Orchestrator** arbitrates VRAM so heavy plugins don't trample each other or Ollama.

<div style="page-break-after: always;"></div>

## 28. Connections (Interconnector)

![Connections](img/Screenshot%20Capture%20-%20http---localhost-5173-connections.png)

The **Interconnector** syncs data across multiple Guaardvark instances: chat history, learnings (self-improvement fixes), images, files, backups, and hardware profiles. It broadcasts entity/file batches with approval gates and pushes safety directives. This is the data-sync + control layer that, combined with per-node HTTP API / MCP, enables a fleet of software robots.

<div style="page-break-after: always;"></div>

## 29. Approvals

![Approvals](img/Screenshot%20Capture%20-%20http---localhost-5173-approvals.png)

The **Approvals** queue is the human-gate for two safety-critical systems: **outreach drafts** (nothing posts without explicit approval) and **self-improvement fixes** (every proposed code change is staged as a `PendingFix` for review before apply). The queue shows what's pending, what was approved/rejected, and the audit trail.

<div style="page-break-after: always;"></div>

## 30. System Map

![System Map](img/Screenshot%20Capture%20-%20http---localhost-5173-system-map.png)

The **System Map** is Guaardvark's X-ray: a dependency graph, reachability analysis, tool graph, and findings view of the entire codebase. Generated by `backend/services/system_mapper/`, it's the authoritative picture of blast radius — what depends on what, what's reachable, and where the risks live. Useful before any self-improvement or large refactor.

<div style="page-break-after: always;"></div>

## 31. Settings

![Settings](img/Screenshot%20Capture%20-%20http---localhost-5173-settings.png)

The **Settings** screen is the control panel for the entire workstation: **Model Management** (master switch, provider selection, model dropdowns for Ollama / Mistral / OpenAI-compatible), **Theme** selection, **MCP** configuration, **Uncle Claude** escalation settings, **self-improvement** toggles (`self_improvement_enabled`, `codebase_locked`, `self_improvement_apply_enabled`), and system-wide defaults. All changes persist to the database and take effect on next restart (or immediately where supported).

<div style="page-break-after: always;"></div>

---

*End of feature guide. 31 screens, one per feature, ordered by capture time. See [GUAARDVARK_GUIDE.md](GUAARDVARK_GUIDE.md) for the full textual reference and [ARCHITECTURE.md](ARCHITECTURE.md) for system design.*
