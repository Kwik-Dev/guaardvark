# Guaardvark CLI — specification and Studio coverage

**Status:** current as of `cloud-plus` tip `95393444` (post upstream sync to `ed37894f`).
**Audience:** anyone asking "can I do X from the terminal?" — the answer, and the *why*.
**Scope:** the `guaardvark` command (source `cli/`, package `guaardvark`, legacy alias `llx`)
and how far it reaches into the Studio (web UI) and the backend API.

This document exists because the CLI has a quick-reference **skill** but no spec, and
because the recurring question "does the CLI cover the feature I just added?" has a
structural answer that is easy to get wrong. Everything here is verifiable in-tree;
regeneration commands are in the appendix.

---

## 1. Summary

> The `guaardvark` CLI is a **curated, upstream-owned REST client** for *operating,
> generating, and planning*. It is **not** a mirror of the Studio. Everything that spends
> GPU on a render, requires human review, or is an editing surface is intentionally
> web-UI-only.

| Property | Value |
|---|---|
| Binary | `guaardvark` (legacy alias `llx`, deprecated) |
| Entry point | `guaardvark = llx.main:run` (`cli/setup.py`) |
| Python | `>=3.12,<3.13` (the ML stack has no 3.13+ wheels) |
| Backend it fronts | the Flask app, default `http://localhost:5000`; macOS `:5055` |
| Shell commands | **43** top-level, **122** subcommands |
| REPL commands | ~70 (a superset — includes local file/agent tooling) |
| Backend blueprints it can reach | ~60 of 82 `url_prefix` areas; **22** with no trace at all |
| Studio pages | **42** page components; ~22 have a CLI equivalent |
| **Fork commits touching `cli/`** | **0** |

---

## 2. What the CLI is

- A **thin client**. Command handlers build an HTTP request and format the response.
  There is no local state, no database access, no model execution and no render logic in
  the CLI. `cli/llx/client.py` is the whole transport.
- **Upstream-owned.** `git log --no-merges --oneline upstream/main..cloud-plus -- cli/`
  returns nothing: no `cloud-plus` commit has ever modified the CLI. Consequently every
  `cloud-plus` feature is absent from it *unless the backend exposes it generically*.
- **Two front doors to the same surface:** a Typer app (shell commands) and a
  `prompt_toolkit` REPL (slash commands). See §4.

## 3. Design contract — the scope rules

These rules explain the coverage gaps in §8 and §10. They are intentional, not oversights.

1. **Operate the machine.** Status, health, doctor, start/stop, logs, GPU, plugins, jobs.
2. **Trigger generation** — images, videos, audio, music, SFX — because these are
   fire-and-forget jobs the backend queues and gates itself.
3. **Plan productions, never render them.** `film-crew` and `music-video` create a project
   and start the *planning* stage, then stop. Their own help says so:
   - `film-crew create` → *"Does not render shots."*
   - `music-video create` → *"Does not render clips."*
   - REPL meta → *"plan; render in Studio"*.
4. **Script and automate.** `--json` on every command; `--non-interactive` never falls into
   the REPL; piped stdin is a valid chat input.
5. **No editing surfaces.** Where the Studio is an *editor* (video editor, code editor,
   caption editor, cast studio, model-management UI), the CLI has nothing. Editing is
   interactive by nature and stays in the browser.
6. **Local tools are REPL-only.** File/agent conveniences (`ls`, `cd`, `read`, `grep`,
   `edit`, `run`, `test`, `todo`, `diff`, `apply`, `undo`) exist in the REPL, not as shell
   subcommands.
7. **The CLI reads some things locally.** `logs tail|search|stats` opens
   `<repo>/logs/*.log` directly rather than calling an API — so it works with the backend
   down.

## 4. Two surfaces

| | Shell (Typer) | REPL (slash) |
|---|---|---|
| Invoked | `guaardvark <cmd> [sub]` | bare `guaardvark` → `/<cmd>` |
| Needs a TTY | no | **yes** — without one, `prompt_toolkit` warns and the REPL EOFs out (`Warning: Input is not a terminal (fd=0).` → `Goodbye.`, exit 0) |
| Automation | `--json`, `--non-interactive`, pipes | history/export |
| Exclusive commands | `setup`, `completion`, `launch`, `ask`, `chat`, `search`(shell form), `analyze`, `init` | `imagine`, `video`, `voice`, `ingest`, `agent on/off/shot`, `web`, `remember`, `memory`, `todo`, `edit`, `run`, `test`, `tools`, `tool`, `context`, `suggest`, `load`, `skills`, `apply`, `undo`, `new`, `abort`, `config`, `theme`, `history`, `export` |

## 5. Global options

| Option | Meaning |
|---|---|
| `--json`, `-j` | machine-readable output (scripting) |
| `--server`, `-s URL` | override the backend URL |
| `--timeout`, `-t SEC` | request timeout |
| `--theme NAME` | `default, teal, musk, hacker, vader, guaardvark, day, auto` |
| `--verbose`, `-V` / `--quiet`, `-q` | verbosity |
| `--non-interactive` | do not start the REPL when no command is given |
| `--version`, `-v` / `--help` | version / help |

## 6. Shell command reference (43 commands, 122 subcommands)

Commands with no subcommands are marked *(leaf)*.

### System & lifecycle
| Command | Subcommands / notes |
|---|---|
| `status` *(leaf)* | server, chat model, workers, GPU, MCP, version |
| `health` *(leaf)* | one line: status, version, uptime |
| `doctor` *(leaf)* | environment health check / repair via system-manager |
| `start` *(leaf)* / `stop` *(leaf)* | start / stop services |
| `launch` *(leaf)* | launch with Ollama integration |
| `setup` *(leaf)* | point the CLI at a server and save it |
| `completion` *(leaf)* | shell completion script |
| `dashboard` *(leaf)* | live auto-refreshing metrics |
| `plugins` | `list, start, stop, enable, disable, status, logs` |
| `gpu` | `status, release` |
| `mcp` | `serve, config, install, doctor, list-tools, client` |
| `logs` | `tail, search, stats` — **reads local `<repo>/logs/*.log`** |

### Chat & AI
| Command | Notes |
|---|---|
| `chat` *(leaf)* | session management, piped input, `--resume`, `--no-rag` |
| `ask` *(leaf)* | one-shot message |
| `search` *(leaf)* | semantic search over indexed documents |
| `agents` | `list, info, run, update` |
| `recipes` | `list, show, validate` |
| `tools` (REPL) | list / invoke backend tools |

### Generation
| Command | Subcommands |
|---|---|
| `images` | `list, generate, status, models, delete` |
| `videos` | `list, generate, from-image, status, models, delete, download, combine` |
| `audio` | `voices, tts, music, sfx, play` |
| `generate` | `csv, image` |
| `quality` | `scorecard` |

### Productions (plan only)
| Command | Subcommands |
|---|---|
| `music-video` | `list, create, status, cancel, delete` |
| `film-crew` | `list, create, status, delete` |

### Data, RAG & knowledge
| Command | Subcommands |
|---|---|
| `index` | `document, status, entity, all` |
| `rag` | `status, query, entities, eval` |
| `files` | `list, upload, download, delete, mkdir` |
| `projects` | `list, create, info, delete` |
| `tasks` | `list, create, info, start, download, delete` |
| `jobs` | `list, status, watch, cancel` |
| `backup` | `create, list, download, restore, delete` |

### CRM & outreach
| Command | Subcommands |
|---|---|
| `clients` | `list, create, info, delete` |
| `websites` | `list, create, info, scrape, delete` |
| `outreach` | `status, queue, approve` |
| `rules` | `list, create, delete, export, import` |
| `settings` | `list, get, set` |
| `models` | `list, active, set` |

### Ops & agents
| Command | Subcommands |
|---|---|
| `family` | `list, status, sync, health` |
| `swarm` | `list, run, templates, status, logs` |
| `lessons` | `begin, end, list` |
| `analyze` *(leaf)* / `init` *(leaf)* | project scan / write `GUAARDVARK.md` |

## 7. REPL command reference

`cli/llx/command_catalog.py` is the source of truth and a contract test enforces it.
Beyond the shell commands above, the REPL adds:

| Group | Commands |
|---|---|
| Local files | `ls, cd, pwd, read, grep, edit, run, test, diff, apply, undo` |
| Todo | `todo list\|add\|done\|clear` |
| Agent context | `tools, tool, context, suggest, analyze, init, load, skills` |
| Memory | `remember, memory list\|search\|delete\|clear` |
| Session | `new, clear, abort, history, export, config server\|theme\|timeout\|api_key, theme` |
| Multimodal | `imagine, video, voice, ingest, agent on\|off\|shot, web` |
| Help | `help, quit, exit` |

---

## 8. Studio coverage — web UI pages vs the CLI

42 page components; ✅ = a CLI command exists, ⚠️ = partial, ❌ = none.

| Studio page | CLI | Note |
|---|---|---|
| ChatPage | ✅ `chat`, `ask` | |
| DashboardPage | ✅ `dashboard`, `status` | |
| Projects / ProjectDetail | ✅ `projects` | |
| DocumentsPage | ✅ `files` | no reader/editor surface |
| UploadPage / BulkImportDocuments | ⚠️ `files upload`, `index` | no bulk-UI parity |
| ImagesPage / BatchImageGenerator | ✅ `images` | |
| FileGenerationPage | ✅ `generate` | |
| VideoGeneratorPage | ✅ `videos` | |
| AudioFoundryPage | ✅ `audio` | |
| VoiceChatPage | ⚠️ `audio tts` | no live voice chat |
| MusicVideoPage | ⚠️ `music-video` | **plan only** |
| FilmCrewPage | ⚠️ `film-crew` | **plan only** |
| AgentsPage | ✅ `agents` | |
| AgentMemoryPage | ⚠️ REPL `remember`/`memory` only | no shell command |
| ApprovalsPage | ❌ | no approvals command |
| OutreachPage | ✅ `outreach` | |
| PluginsPage | ✅ `plugins` | |
| MCPServersPage | ✅ `mcp` | |
| RulesPage | ✅ `rules` | |
| SettingsPage | ✅ `settings` | |
| TaskPage | ✅ `tasks` | |
| SwarmPage | ✅ `swarm` | |
| ClientsPage | ✅ `clients` | |
| Websites / WebsiteDetail | ✅ `websites` | |
| ContentLibraryPage | ❌ | |
| SystemMapPage | ❌ | `system-map` API unreferenced |
| CodeEditorPage | ❌ | `code-execution` API unreferenced |
| DevToolsPage / ProgressTestPage | ❌ | developer surfaces |
| StickyNotesPage | ❌ | |
| ConnectionsPage | ❌ | `connections` API unreferenced |
| **CastStudioPage / CastMemberPage** | ❌ | **`cast-library` API unreferenced** |
| **UpscalingPage** | ❌ | **`upscaling` API unreferenced** |
| **VideoEditorPage** | ❌ | **`video-editor` API unreferenced** |
| **VideoTextOverlayPage** | ❌ | **`video-overlay` API unreferenced** |
| **TrainingPage** | ❌ | **`training_datasets` API unreferenced** |
| WordPressPages / WordPressSites | ❌ | `wordpress` API unreferenced |
| ActivitiesPage / AutoresearchPage | ⚠️ | partial (jobs / `rag eval`) |
| NotFoundPage | — | n/a |

## 9. API coverage

The CLI issues literal `/api/...` paths from its command handlers. Grepping those against
the 82 blueprint `url_prefix` values gives:

- **~60 areas reached**, including: `agent`, `agent-control`, `agents`, `audio-foundry`,
  `automation`, `autoresearch`, `backups`, `batch-image`, `batch-video`, `bulk-generate`,
  `chat`, `clients`, `code-intelligence`, `enhanced-chat`, `entity-indexing`, `files`,
  `generate`, `gpu`, `index`, `interconnector`, `jobs`, `lessons`, `memory`, `meta`,
  `model`, `music-video`, `plugins`, `production`, `projects`, `rules`, `settings`,
  `social-outreach`, `swarm`, `tasks`, `tools`, `voice`, `websites`.
- **22 areas with no trace anywhere in `cli/`**: `auth`, `brain`, `cast-library`,
  `chat-sessions`, `code-execution`, `distributed`, `enhanced-generation`, `entity-links`,
  `hierarchy`, `infographic`, `progress-test`, `rag-debug`, `scheduler`, `search-console`,
  `self-code`, `simple-chat`, `system-map`, `training_datasets`, `video-editor`,
  `video-overlay`, `web-search`, `wordpress`.

Caveats, so this table is not over-read:

- A few areas are **reached indirectly**: `rag status` uses `/api/meta/index-info` and
  `rag eval` uses `/api/autoresearch/status`, so the `rag` blueprint itself is not called.
- `logs` is **local-only** (reads `<repo>/logs/`), so it appears in no API area.
- The CLI also calls surfaces not backed by a `*_api.py` blueprint (e.g. chat/session
  endpoints under other routers).

---

## 10. Missing from the CLI — the `cloud-plus` feature delta

Every `cloud-plus` feature block from `CLOUD_PLUS_FEATURES.md`, with its CLI status.
This is the authoritative "what the fork added that the terminal cannot do".

### 10.1 Cloud LLM providers (CLOUD_PLUS_FEATURES §1)

| Feature | CLI | How it is reached today |
|---|---|---|
| OpenAI-compatible chat provider / multi-provider escalation | ⚠️ indirect | `models set` + `chat`; the `llm_provider` API has **no command** |
| Master cloud switch gating `get_default_llm` | ❌ | `settings` only if it is a settings key |
| UTF-8 forcing in cloud streaming | ⚠️ | backend, transparent when chat runs |
| Music-prompt rewriter respects cloud consent | ⚠️ | backend, transparent |
| Discord voice backend opt-in | ❌ | plugin/`.env` |

### 10.2 ComfyUI and the GPU (CLOUD_PLUS_FEATURES §2)

| Feature | CLI | Note |
|---|---|---|
| MPS support, auto-start, status check | ⚠️ | `plugins start\|stop\|status comfyui`, `gpu status\|release` — service only |
| Engine detection from live `/object_info` | ❌ | backend health probe |
| Z-Image generation via ComfyUI | ❌ | no engine flag; reachable only by passing a model id to `images generate --model` |
| `/imagemodel comfyui` chat image backend | ❌ | the token `imagemodel` appears **nowhere** in `cli/llx` |
| Per-model VRAM reserve, `GUAARDVARK_COMFYUI_RESERVE_VRAM` | ❌ | `.env` |
| Shared ComfyUI model home (`GUAARDVARK_COMFYUI_DIR`) | ❌ | `.env` |
| ComfyUI catalog "installed" fix | ❌ | surfaced through `images models` / `videos models` output only |

### 10.3 Film Crew production (CLOUD_PLUS_FEATURES §3)

| Feature | CLI | Note |
|---|---|---|
| Live `RenderProgress` during rendering | ❌ | Studio only |
| Resumable rendering + per-shot clip persistence | ❌ | Studio only |
| Storyboard generation progress indicator | ❌ | Studio only |
| Subject LoRAs refreshed/dropped during storyboard gen | ❌ | backend |
| Script templates from `docs/film-crew-scripts` | ⚠️ | `film-crew create` starts the screenwriter; template choice is Studio |
| Film Crew agents via OpenAI-compatible provider | ❌ | backend config |
| I2V speed/quality env vars | ❌ | `.env` |
| I2V model dropdown in the music-video approval panel | ❌ | Studio only — **the CLI cannot choose the I2V model** |
| Collapsible alert in ProductionDetail/CreateProductionDialog | ❌ | UI |
| `final.mp4` playback on the Production page | ❌ | Studio only |

### 10.4 Video editor and FFmpeg (CLOUD_PLUS_FEATURES §4)

| Feature | CLI | Note |
|---|---|---|
| FFmpeg still-to-video with camera motion | ❌ | **no `video-editor` command exists** |
| Configurable focus point (Ken Burns), pan directions | ❌ | Studio only |
| Framing modes (letterbox / zoom-to-fill / match-image) | ❌ | Studio only |
| Caption export/import + caption code editor | ❌ | the captions API exists; the CLI never calls it |
| Drag-to-reorder in the Bin panel | ❌ | Studio only |
| FFmpeg batch metadata / library counts | ❌ | Studio only |

### 10.5 Audio Foundry, voice, music (CLOUD_PLUS_FEATURES §5)

| Feature | CLI | Note |
|---|---|---|
| MPS support + remote-capable Audio Foundry | ✅ | `audio tts\|music\|sfx` use it |
| STT via external whisper.cpp server | ❌ | **no transcribe/STT command** exists |
| Cloud music-prompt rewriter + generation progress | ⚠️ | `audio music` triggers it; progress via `jobs watch` |

### 10.6 Cast / LoRA (CLOUD_PLUS_FEATURES §6)

| Feature | CLI | Note |
|---|---|---|
| Per-run shot count (16/32) for character generation | ❌ | **no `cast` command** |
| Cast LoRA resolved from the user message in `generate_image` | ❌ | `images generate` has `--model`, `--count`, `--from-file` — **no `--subject`/cast flag** |
| Cast generation on its own Celery queue | ❌ | backend |
| Identity sync via vision + cloud consensus | ❌ | backend |
| RunPod remote LoRA trainer | ⚠️ service only | `plugins start\|stop runpod_lora_trainer`; **no way to launch a training run** |

### 10.7 UI / model management (CLOUD_PLUS_FEATURES §7)

| Feature | CLI | Note |
|---|---|---|
| Model-management UI | ❌ | `models list\|active\|set` is the only LLM surface |
| Cloud model shown in the status bar | ⚠️ | `models active`, `status` |
| Collapsible alerts (common + snackbar) | ❌ | UI |
| MUI `Collapse`/`Grow` ref fixes | ❌ | UI |

### 10.8 Ops, MCP, launch (CLOUD_PLUS_FEATURES §8)

| Feature | CLI | Note |
|---|---|---|
| Optional MCP server startup/cleanup in start/stop | ✅ | `start`, `stop`, plus the `mcp` group |
| `.codex-ready/` project-type catalog | ❌ | repo metadata |
| Portable checkouts (no absolute home paths) | ✅ | enforced by `scripts/check_portable.sh` |
| CI runs on `cloud-plus` | — | not a CLI feature |

### 10.9 Not features, for completeness

Docs (`§9`), `.env` variables (`§10`), architecture/workflow diagrams, fork README
rewrites: configuration and documentation, with no CLI surface by definition.

### 10.10 The gap in one list

Highest-value `cloud-plus` capabilities with **no CLI surface at all**:

1. **Cast Library** — list/inspect subjects, LoRAs, samples (`cast-library`).
2. **RunPod LoRA training** — launch/monitor a training run (`training_datasets`).
3. **ComfyUI engine selection** — `/imagemodel comfyui`, Z-Image routing.
4. **Video editor** — FFmpeg stills, framing, captions, bin reorder (`video-editor`).
5. **Film Crew / music-video render control** — render, resume, I2V model choice.
6. **Upscaling** (`upscaling`).
7. **System Map** (`system-map`) and **code execution** (`code-execution`).
8. **Approvals** — the held-changes review queue.
9. **STT / transcription**.
10. **Inbound guard** posture and held changes.

---

## 11. Extending the CLI

Because the CLI is upstream-owned, any fork-only command is **permanent divergence** —
it will conflict on every upstream sync unless it lives in new files.

Rules that keep it cheap:

1. Add a **new module** under `cli/llx/commands/` and register it in
   `cli/llx/command_catalog.py` (+ the contract test), rather than editing upstream
   command bodies.
2. Reuse `llx/client.py`; never re-implement transport.
3. Keep the scope rules in §3: no editing surfaces, no review gates, no render triggers
   that bypass the Studio's approval.
4. Follow the pattern of the two output-registration fixes: when a backend call can fail
   silently, log an error rather than returning a body with a missing id.
5. The obvious first additions, all backed by existing endpoints that the CLI already
   knows how to reach: `cast list|show`, `upscale`, `training list|start`, and a
   `video-editor` group over `/api/video-editor/*`.

---

## Appendix — regeneration

```bash
# the fork's own commits touching the CLI (expect: none)
git log --no-merges --oneline upstream/main..cloud-plus -- cli/

# the shell command tree
guaardvark --help
for c in $(guaardvark --help 2>&1 | sed -n '/Commands/,/╰/p' | grep -oE '^│ [a-z-]+' | awk '{print $2}'); do
  guaardvark "$c" --help
done

# the REPL command tree (source of truth) and its contract test
sed -n '/COMMAND_TREE/,/^)/p' cli/llx/command_catalog.py
python -m pytest cli/tests/test_command_catalog_contract.py

# Studio pages and backend API areas
ls frontend/src/pages/*.jsx
grep -rhoE 'url_prefix="[^"]+"' backend/api/*_api.py | sort -u

# what the CLI actually calls
grep -rhoE '"/api/[a-z0-9_/-]+' cli/llx --include=*.py | sed 's|"/api/||' | cut -d/ -f1 | sort -u
```

Portability: this file must contain no literal absolute home path for macOS, Linux or
Windows — run `scripts/check_portable.sh` before committing any documentation change.
