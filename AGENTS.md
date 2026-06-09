# AGENTS.md — Guaardvark project rules (Grok + Claude)

This repository uses AI coding agents (Grok Build and Claude Code / compatible).

## Required reading (in order)

1. **Workflow charter** (operating principles for all sessions):  
   [`/home/llamax1/WORKFLOW.md`](file:///home/llamax1/WORKFLOW.md) — GOAL/ASSUME/PROOF/ROLLBACK, Read before write, Zero placebo, DONE/LEFT/FOLLOW, labels, verification ladder, anti-patterns. Read this first on every non-trivial task.

2. **Shared project context** (what Guaardvark is right now, layout, in-flight work, self-coding chokepoints, operator memory, dead code, gotchas):  
   [CLAUDE.md](CLAUDE.md) — loaded automatically by Grok via compatibility rules and by Claude.

3. **Grok-specific orientation** (tools, subagents, plan mode, MCP usage, skills, TUI behaviors, verification habits in this workspace):  
   [GROK.md](GROK.md) — read explicitly or rely on this AGENTS.md to surface it.

## Quick orientation pointers

- **Where things live** — see the directory tree and "Don't rebuild a directory" guidance in both CLAUDE.md and GROK.md.
- **Self-coding** — every mutation funnels through `backend/services/guarded_code_service.py::apply_exact_replacement()`. Load-bearing; treat with extreme care (full notes in CLAUDE.md §"Self-coding subsystem").
- **Frontend builds** — always `cd frontend && npm run build` before trusting JSX changes or claiming UI work complete (Vite is lenient; production Rollup is strict).
- **Grok tools** — prefer `read_file`/`grep`/`list_dir`/`search_replace` for source work; `run_terminal_command` (with `background` + monitor for long jobs) for execution/tests/git. Use `todo_write` for 3+ step work. `spawn_subagent` (with `explore`/`plan` types, `capability_mode`, `isolation=worktree`) for parallel effort. Enter `plan mode` only for genuine architectural ambiguity.
- **MCP in this session** — mcp-search, postgres, redis are connected. Discover with `search_tool` before calling anything via `use_tool`.
- **Skills** — `/load`, `/help`, `/check-work`, `/create-skill`, `/implement`, `/review`, `/best-of-n`, image/video tools, etc. Project skills live in `.grok/skills/`.
- **Project rules discovery** — Grok loads AGENTS.md / CLAUDE.md / variants + `.grok/rules/*.md` (and compat paths). Deeper files win. Use `grok inspect` (host shell) to see what is active.

## Additional references

- Full feature list: [CAPABILITIES.md](CAPABILITIES.md)
- Public docs: [README.md](README.md)
- Grok TUI user guides: `~/.grok/docs/user-guide/` (especially 07-mcp-servers, 08-skills, 12-project-rules, 16-subagents, 19-plan-mode, 20-background-tasks)
- Plugin manifests, scripts, and runtime state live under the paths documented in CLAUDE.md / GROK.md.

Update this file (and the referenced docs) in the same turn when project conventions or facts drift. This is the single source of "how we work here" for agents.

---

*Maintained for both Grok and Claude sessions on the Guaardvark codebase.*