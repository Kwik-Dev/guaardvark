# Guaardvark CLI — specification and Studio coverage

**Status:** current as of `cloud-plus` tip `95393444` (post upstream sync to `ed37894f`).
**Plan:** [docs/CLI_PLAN.md](CLI_PLAN.md) — the phased plan to close these gaps and renew the test framework.
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
| Shell commands | **58** top-level, **234** subcommands |
| REPL commands | ~70 (a superset — includes local file/agent tooling) |
| Backend blueprints it can reach | ~60 of 82 `url_prefix` areas; **22** with no trace at all |
| Studio pages | **42** page components; ~22 have a CLI equivalent |
| **Fork commits touching `cli/` before this branch** | **0** — `cli/` was untouched upstream-owned code. `cli/llx/commands/_fork/` and the two hooks in `main.py` are the first deliberate divergence; see §11. |

---

## 2. What the CLI is

- A **thin client**. Command handlers build an HTTP request and format the response.
  There is no local state, no database access, no model execution and no render logic in
  the CLI. `cli/llx/client.py` is the whole transport.
- **Upstream-owned until now.** `git log --no-merges --oneline upstream/main..cloud-plus -- cli/`
  was empty before this work: no fork commit had ever modified the CLI, so every
  `cloud-plus` feature was absent from it *unless the backend exposed it generically*.
  §11 describes the one seam that now changes that.
- **Two front doors to the same surface:** a Typer app (shell commands) and a
  `prompt_toolkit` REPL (slash commands). See §4.

## 3. Design contract — the scope rules

These rules explain the coverage gaps in §8 and §10. They are intentional, not oversights.

1. **Operate the machine.** Status, health, doctor, start/stop, logs, GPU, plugins, jobs.
2. **Trigger generation** — images, videos, audio, music, SFX — because these are
   fire-and-forget jobs the backend queues and gates itself.
3. **Plan productions, never render them**, *by named command*. `film-crew` and
   `music-video` create a project and start the *planning* stage, then stop. Their own help
   says so:
   - `film-crew create` → *"Does not render shots."*
   - `music-video create` → *"Does not render clips."*
   - REPL meta → *"plan; render in Studio"*.

   The render is not a separate route to call: it is unlocked by an approval POST
   (`POST /api/production/<id>/casting/confirm`, `…/storyboard/approve`,
   `POST /api/music-video/<id>/approve`). Since D6 those three exist as **named, `--yes`-gated
   commands** — `film-crew confirm-casting`, `film-crew approve-storyboard`,
   `music-video approve` — because they decide what to do with output the operator already
   owns, which is the `cast approve` class, and because "render my film from the terminal"
   should not require knowing a route. The held-code / inbound-guard / publish class stays
   Studio-only. The generic `guaardvark api request` (D5, §3.1) reaches all three too, gated
   the same way, so the named command and the escape hatch never disagree.
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
   down. The `api` group's audit log is the second local file: `<GUAARDVARK_DIR>/api-audit.jsonl`.

### 3.1 The one deliberate escape hatch — `guaardvark api` (D5)

Rules 1–7 describe the *named* commands. `guaardvark api request <METHOD> <PATH>` is the
acknowledged exception, added because wrapping routes one at a time is why coverage is
permanently N of 97 — and because a route nobody wrapped used to be unreachable from the
terminal except through `curl`.

| Command | What it does |
| `api request <METHOD> <PATH>` | Send any `/api/...` request. `--data` / `--data-file` / `@file` body, repeatable `--query k=v`, `--dry-run`, `--json`. |
| `api routes` | List every route the backend serves (`GET /api/routes`, or `--docs` for docstrings), filterable by `--search` / `--method`. |
| `api audit` | Read the tier-B audit log back: what this CLI sent, refused or failed. |

The guard, in `cli/llx/commands/_fork/_api_guard.py` (deliberately a `_`-prefixed,
non-command module, so the fork contract tests skip it and it is the single place that
names a decision class):

- **Reads are free.** GET, HEAD, OPTIONS need nothing.
- **Writes need `--yes`.** POST, PUT, PATCH, DELETE are refused with exit code 2 otherwise,
  and the refusal is logged.
- **A decision-class route needs `--yes` even as a read**, so a future GET-shaped approval
  cannot slip through the read path. Matching is on whole path *segments* (`approve`,
  `reject`, `decide`, `apply`, `release-held`, `dispatch`, `confirm`, `publish`, `trigger`,
  `execute`) — `/api/connections/publishes` is a read and is not dragged through the gate.
- **Paths must start with `/api/`**, and an absolute URL is refused outright. Both keep
  every call on the backend's own gate and audit path rather than at a plugin port, which
  is what preserves D1.
- **Every attempt is appended** to `<GUAARDVARK_DIR>/api-audit.jsonl` — ok, refused,
  dry-run and error alike, with the body's **byte size, not its content**.

Because this command can reach every one of the 97 backend API areas, the coverage table
below and `api_coverage.py` now mean something narrower than they used to: they say
whether an area has a **first-class** command, not whether it is reachable. Every
`NOT_EXPOSED` entry is still reachable through `api request`.

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

## 6. Shell command reference (58 commands, 234 subcommands)

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
| `music-video` | `list, create, status, cancel, delete`; **D6:** `cuts` (the Director's plan, `--prompts` for full text), `clips` (per-cut render state), `storyboard <id> <idx> --out F`, `approve <id> --yes` **(starts the render)** |
| `film-crew` | `list, create, status, delete`; **D6:** `subjects` (extracted cast + LoRA/training state), `shots`, `shot <id> <shot> [--image F]`, `templates`, `confirm-casting <id> --yes`, `approve-storyboard <id> --yes` **(starts the render)** |

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

### Fork groups (added by `cloud-plus`; none of these exist upstream)

All fourteen live in `cli/llx/commands/_fork/` and are mounted by the registry, not by an
upstream edit — see §11. Their read-only halves are covered by
`cli/tests/test_fork_readonly_contract.py`, which proves they issue no write.

| Group | Subcommands | What it is for |
|---|---|---|
| `guard` | `status, scans, show, git, sweep` | inbound guard: what is held, and the posture. No approve/reject — that is the Studio's |
| `improve` | `status, precheck, runs, metrics, pending, trigger, toggle` | self-improvement state. Applying a fix is the Studio's |
| `system-map` | `health, snapshot, findings, dismiss` | the repository map and its findings. `dispatch` is the Studio's |
| `content` | `pages, page, stats, generations, duplicates, page-delete` | content library; `page-delete` needs `--yes` |
| `web` | `status, search, quick-search, sitemap` | web research, through the backend's outbound policy (distinct from document `search`) |
| `connections` | `list, show, test, providers, environment` | connected accounts. `oauth` stays in the Studio |
| `approvals` | `list, show` | one read-only queue: publishes, held code, outreach drafts |
| `cast` | `list, show, samples, plan, generate, cancel, approve, train, train-cancel, make-default, delete, import-lora` | the Cast Library. `train` and `delete` need `--yes`; `approve` is sample selection (§10 note) |
| `upscale` | `image, video, models, model-download, jobs, status, cancel` | Real-ESRGAN / HAT-L / SwinIR. `cancel` needs `--yes` |
| `infographic` | `generate, models, model-download, download-status, status` | infographics |
| `training` | `datasets, dataset, dataset-new, dataset-update, dataset-delete, backends` | training datasets; the run itself is `cast train` |
| `video-editor` | `health, projects, project, project-new, project-delete, jobs, job, filters, transitions, render, analyze, captions-export, captions-import, shotcut` | editor operations, not timeline authoring |
| `llm` | `provider, set, models, openai-model, mistral-model, test, cloud on\|off` | the chat provider and the master cloud switch — `cloud on` needs `--yes` |
| `wordpress` | `sites, site, site-test, pages, pull-sitemap, pull-list, pull-page, pull-bulk, pull-status, process-queue, process-run` | sites and page pull; `process-run` publishes, so it needs `--yes` |

### Extended upstream groups

| Group | Added by the fork |
|---|---|
| `audio` | `transcribe` (speech-to-text), `models`, `model-download` — added from a fork module without editing `cli/llx/commands/audio.py` |

### Generic backend access (D5) — one command, every route

| Command | Subcommands |
|---|---|
| `api` | `request`, `routes`, `audit` |

See §3.1. `api request` is the only command in the CLI that is not a curated wrapper: it
reaches routes no named command covers, which is what makes the render gates on
`production`/`music-video` and the ~25 unwrapped `files` routes reachable from the
terminal without a `curl`. Reads are free; writes and decision-class routes need `--yes`;
all attempts are audited.

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
| MusicVideoPage | ✅ `music-video` | plan **and** render: `cuts`, `clips`, `storyboard`, `approve --yes` |
| FilmCrewPage | ✅ `film-crew` | plan **and** render: `subjects`, `shots`, `shot --image`, `templates`, `confirm-casting`/`approve-storyboard --yes` |
| AgentsPage | ✅ `agents` | |
| AgentMemoryPage | ⚠️ REPL `remember`/`memory` only | no shell command |
| ApprovalsPage | ⚠️ `approvals list` | read-only; approving stays in the Studio |
| OutreachPage | ✅ `outreach` | |
| PluginsPage | ✅ `plugins` | |
| MCPServersPage | ✅ `mcp` | |
| RulesPage | ✅ `rules` | |
| SettingsPage | ✅ `settings` | |
| TaskPage | ✅ `tasks` | |
| SwarmPage | ✅ `swarm` | |
| ClientsPage | ✅ `clients` | |
| Websites / WebsiteDetail | ✅ `websites` | |
| ContentLibraryPage | ✅ `content` | |
| SystemMapPage | ✅ `system-map` | |
| CodeEditorPage | ❌ | `code-execution` API unreferenced |
| DevToolsPage / ProgressTestPage | ❌ | developer surfaces |
| StickyNotesPage | ❌ | |
| ConnectionsPage | ✅ `connections list\|show\|providers\|environment` | `oauth` stays in the Studio |
| **CastStudioPage / CastMemberPage** | ✅ `cast` | training runs are `cast train` |
| **UpscalingPage** | ✅ `upscale` | |
| **VideoEditorPage** | ⚠️ `video-editor` | editor operations, not timeline authoring |
| **VideoTextOverlayPage** | ⚠️ `api request` only | no named command; the generic escape hatch (§3.1) reaches `/api/video-overlay/*` |
| **TrainingPage** | ✅ `training` | datasets; the run itself is `cast train` |
| WordPressPages / WordPressSites | ✅ `wordpress sites\|pages\|pull-*\|process-run` | `process-run` publishes, so it needs `--yes` |
| ActivitiesPage / AutoresearchPage | ⚠️ | partial (jobs / `rag eval`) |
| NotFoundPage | — | n/a |

## 9. API coverage

The authoritative answer is not this prose but `cli/llx/commands/_fork/api_coverage.py`, which
classifies **every** `backend/api/*_api.py` module and is enforced by
`cli/tests/test_spec_parity.py` — a new backend area fails CI until it is declared. Keyed on
the module, not the URL prefix (`inbound_guard_api.py` serves `/api/settings/inbound_guard`, so
prefix keying would collide with `settings`).

At Phase 4 — the end state: **50 exposed**, **0 planned**, **47 deliberately not exposed** (97
declared). Nothing is left undecided: every backend API area is either driven by a named
command or carries a written reason, and `cli/tests/test_spec_parity.py` fails when a new
one appears without one.

Since D5, those three buckets describe **first-class commands**, not reachability. Every
one of the 47 unexposed areas is still reachable through `guaardvark api request` (§3.1);
the reason on each says why it has no *named* command, not that the route is out of reach.

Reached by a command, including: `agent-chat`, `agent-control`, `agents`, `audio-foundry`,
`automation`, `autoresearch`, `backups`, `batch-image`, `batch-video`, `bulk-generation`,
`chat`, `clients`, `code-intelligence`, `connections`, `content-management`,
`enhanced-chat`, `entity-indexing`, `files`, `generate`, `gpu`, `inbound-guard`, `indexing`,
`interconnector`, `jobs`, `lessons`, `memory`, `meta`, `model`, `music-video`, `plugins`,
`production`, `projects`, `rules`, `self-improvement`, `settings`, `social-outreach`,
`swarm`, `system-map`, `tasks`, `tools`, `unified-chat`, `voice`, `web-search`, `websites`.

Everything else is declared `NOT_EXPOSED` with a reason in that file — Studio-only surfaces,
internals, and endpoints reached indirectly. All of them remain reachable via `api request`.

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
| `/imagemodel comfyui` chat image backend | ✅ already covered | it persists the `chat_image_model` setting, which `settings get\|set chat_image_model` reaches; and `comfyui` is a registry id, so `images generate --model comfyui` works per-request. **No command was needed** — an earlier revision of this table said "appears nowhere in `cli/llx`", which was true of the token and false about the capability. |
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
| Script templates from `docs/film-crew-scripts` | ✅ | `film-crew templates` lists them; the text goes in through `film-crew create --file`. There is no create-time `--template` flag upstream and this adds none: the template *is* the script |
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
3. **ComfyUI engine selection** — covered by `settings set chat_image_model comfyui` (persistent)
   or `images generate --model comfyui` (per-request). Verify before adding a flag.
4. **Video editor** — FFmpeg stills, framing, captions, bin reorder (`video-editor`).
5. **Film Crew / music-video render control** — ✅ render (`film-crew confirm-casting`,
   `film-crew approve-storyboard`, `music-video approve`, all `--yes`); ✅ resume/retry of a
   failed production stays Studio-side, but `POST /api/production/<id>/retry` is reachable
   through `api request`; ⚠️ I2V model choice is settable at creation
   (`music-video create --model`, which persists `settings.i2v_model`) but **not** changed
   afterwards — the approval panel's dropdown is the Studio's.
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

1. Add a **new module** under `cli/llx/commands/_fork/` exporting `COMMAND_NAME` and a
   `typer.Typer` named `app`. `_fork/registry.py` discovers it automatically — do not
   edit a shared list, and do not touch `llx/main.py` beyond the two lines that are
   already there.
2. Declare the backend API area it drives in `cli/llx/commands/_fork/api_coverage.py`.
   `cli/tests/test_spec_parity.py` fails until you do — that is what keeps §9 and §10 of
   this document from drifting out of date.
3. REPL (`/foo`) commands are a separate, more expensive seam: `_register_repl_commands`
   in `slash.py` plus `COMMAND_TREE` in `command_catalog.py`, which the upstream contract
   test pins to each other. Add one only when a command genuinely needs to be REPL-only;
   no planned group does.
4. Reuse `llx/client.py`; never re-implement transport.
5. Keep the scope rules in §3: no editing surfaces, no review gates, no render triggers
   that bypass the Studio's approval. There are **two** documented exceptions, both
   `--yes`-gated so neither becomes the rule and both named in one file: the `api` group
   (§3.1, D5), which is audited, and the three render gates (§3, D6) in `render_gates.py`,
   which are the single file-wide entry in the D2 static scan's `_ALLOWED`. The named
   commands beside them keep obeying this rule unchanged.
6. Follow the pattern of the two output-registration fixes: when a backend call can fail
   silently, log an error rather than returning a body with a missing id.
7. Add a `--json` branch and a golden snapshot test (see `cli/tests/conftest.py` for the
   shared fixtures and the tier markers).

The plan for closing the rest of the gap, group by group, is [`docs/CLI_PLAN.md`](CLI_PLAN.md).

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
