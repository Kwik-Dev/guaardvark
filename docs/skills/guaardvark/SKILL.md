---
name: guaardvark
description: Guaardvark CLI for chatting with the local AI assistant, running agents, generating images/videos, and managing RAG, files, tasks, and backups from the terminal.
---

# Guaardvark CLI

Guaaardvark is a self-hosted, offline-first AI workstation. The `guaardvark` CLI (package `guaardvark`, command alias `llx` in the source tree) is a terminal front-end for the same Flask backend that powers the web UI and MCP server.

## Installation & binary

- Installed via PyPI: `pip install guaardvark`
- Binary: `guaardvark` (on PATH, e.g. `~/.local/bin/guaardvark`). The source-tree alias is `llx` (`cd cli && pip install -e .`).
- The CLI talks to a running Guaardvark backend (default `http://localhost:5000`, or the `FLASK_PORT` you configured — e.g. `5055`). Start services with `./start.sh` (or `guaardvark start`).

## Global options (apply to most commands)

```bash
guaardvark --json ...        # machine-readable JSON output (scripting)
guaardvark --server <url>    # override server URL, e.g. -s http://localhost:5055
guaardvark --timeout <sec>   # request timeout
guaardvark --non-interactive # do not drop into the REPL when no subcommand given
```

## Core commands

### Status & system
```bash
guaardvark status          # system dashboard / health
guaardvark health          # health check
guaardvark doctor          # run environment health check (or repair)
guaardvark start | stop    # start / stop Guaardvark services
guaardvark models          # LLM model management (list active models)
```

### Chat & AI assistant

```bash
guaardvark chat "explain this codebase"        # chat with the LLM (with RAG context)
guaardvark chat --resume                        # continue last conversation
guaardvark chat --session <id>                  # resume a specific session
guaardvark chat --list                          # list recent sessions
guaardvark chat --export --session <id>         # export conversation to markdown
guaardvark chat --no-rag                        # disable RAG context
guaardvark chat --project <id>                  # scope RAG to a project
echo "hi" | guaardvark chat                     # piped input
guaardvark ask "one-shot question"              # one-off message (no REPL)
```

### Agents

```bash
guaardvark agents list                          # list configured agents
guaardvark agents info                          # agent details + available tools
guaardvark agents run --agent <name> "prompt"   # run an agent (auto-selects best if omitted)
guaardvark agents update                        # update agent config
guaardvark recipes list | show | validate       # inspect/validate agent recipes
```

### Media generation

```bash
guaardvark images list                          # list image batches
guaardvark images generate "a red fox"          # generate images from a prompt
guaardvark images status <batch>                # batch generation status
guaardvark images models                        # list image models
guaardvark images delete <batch>                # delete a batch
guaardvark videos generate "prompt"             # generate video from text
guaardvark videos from-image <img> "prompt"     # video from a source image
guaardvark videos status | models | list        # video batch info
guaardvark generate image "prompt" | csv "..."   # convenience generate helpers
```

### RAG / search / files

```bash
guaardvark search "query"                       # semantic search over indexed docs
guaardvark index <path>                          # index files/dirs for RAG
guaardvark rag                                   # RAG index inspection/evaluation
guaardvark files ...                             # file and folder management
```

### Operations

```bash
guaardvark backup                                # backup / restore
guaardvark tasks                                 # create, run, monitor tasks
guaardvark jobs                                  # background job management
guaardvark logs                                  # log viewing / analysis
guaardvark outreach                            # social outreach (status, queue, approve)
guaardvark settings                              # application settings
guaardvark family                                 # Interconnector family network
```

## Interactive REPL slash commands

Running bare `guaardvark` starts a REPL:

```
/imagine <prompt>       generate an image
/video <prompt>         generate a video
/voice <text>           text-to-speech
/agent                  toggle autonomous agent mode
/web                    open the web UI
/ingest <path>          index files/directories for RAG
/search <query>         semantic search
/models list            list available Ollama models
/remember <text>        save to persistent memory
/backup create          create a system backup
/jobs list|watch       monitor background tasks
/config                view/change settings
/help                  full command reference
```

## Notes

- The CLI reads model/provider settings from the backend (e.g. the active LLM provider — local Ollama, Mistral, or OpenAI-compatible — configured in the web UI or via `POST /api/llm/provider`).
- For scripting/automation, prefer `--json` and `--non-interactive`.
- Media generation and model availability depend on the backend's configured providers (local Ollama/ComfyUI, or cloud LLM providers).
