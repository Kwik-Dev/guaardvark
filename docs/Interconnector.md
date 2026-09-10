Good question — this is exactly the right way to think about it. The honest answer: **yes, you can assemble a fleet-control system from these pieces, but the Interconnector alone isn't the control bus** — you'd use it *together with* the per-node API/MCP, each for its strength. Here's the realistic architecture.

## What each piece is good for

| Layer | Role for a fleet | Nature |
|---|---|---|
| **Per-node Guaardvark instance** | The "brain" + "hands" of each robot — agent loop, tools, and (for software robots) **desktop/screen control** | runs on each robot |
| **HTTP API** (`:5055`, ~90 modules) | **Real-time command + telemetry** per node: send a task, read status, stream progress | request/response + Socket.IO |
| **MCP server** (`python -m backend.mcp`) | Expose a node's tools to *one* local controller (Claude Code etc.); less suited for many remote connections (stdio) | stdio, local |
| **Interconnector** | **Fleet-wide async sync**: push config/code/learnings/images to all nodes, broadcast safety **directives**, approval gates | master→client, async |

## A realistic "fleet control" architecture
```
              ┌──────────────── CONTROL PLANE (you / orchestrator) ───────────┐
              │  sends commands via each node's HTTP API  ·  reads status      │
              └───┬──────────────┬──────────────┬──────────────────────────────┘
                  │ API          │ API          │ API
            ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
            │ Robot 1   │  │ Robot 2   │  │ Robot N   │   each = Guaardvark instance
            │ agent     │  │ agent     │  │ agent     │   (screen control, tools,
            │ control   │  │ control   │  │ control   │    MCP, API)
            └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
                  │              │              │
                  └── Interconnector (async): push code/model updates,
                      learnings, safety directives to ALL nodes
```
- **Real-time control** = a controller (or the "master" Guaardvark's agent) calls each node's **HTTP API** (`POST /api/...` task, `GET` status, Socket.IO for live events).
- **Fleet-wide sync/updates** = **Interconnector** pushes the same code, config, learned fixes, and safety directives to every node with approval gates.
- **Per-robot skill/control** = the node's own agent loop + screen/desktop automation (or whatever you attach).

## Important caveats
1. **Interconnector ≠ real-time robot bus.** It's async master→client push with approvals — great for *syncing updates to the fleet*, wrong for low-latency per-command control. Use the **API** for that.
2. **MCP is stdio/local** — not ideal as the remote fleet protocol. For remote per-node control, use the **HTTP API** (or expose an SSE/streamable transport). Don't try to have 50 nodes connect to an MCP stdio pipe.
3. **"Robot" here = software robot** — Guaardvark's screen agents control **desktop apps, browsers, files, workflows** (Xvfb + XFCE desktop on Linux). It has no direct physical-robot/servo-hardware I/O. If you mean physical robots, Guaardvark is the **brain/orchestrator**; the hardware actuator layer is a separate bridge.
4. **Security** — you're exposing control of machines over the network. Gate it with per-node API keys (`GUAARDVARK_API_KEY`), keep it on trusted/LAN, and the Interconnector's approval gates help.

## Bottom line
**Yes** — combined, you can build a controllable software-robot fleet:
- **API/MCP** = per-node command + telemetry
- **Interconnector** = fleet-wide sync, config propagation, learnings, and directives
- **Per-node agent** = the robot's ability to do work (desktop control, tools, RAG, generation)

But don't expect the Interconnector itself to be a live robot-command bus — it's the fleet *synchronization/control-policy* layer; the per-node **HTTP API** is your real-time command channel.

Want me to sketch a concrete wiring (which endpoints to call, how to auth each node, and a minimal "send task to fleet" flow) on your setup?

----


**Guaardvark does not treat Interconnector as a distributed job queue** (it is not Kubernetes or a multi-node Celery cluster). Interconnector is an application-level **master/client sync and coordination layer**: it keeps projects, rules, documents, and (with approval) self-improvement learnings aligned across machines, and reports each node’s capabilities (GPU, plugins, architecture).

Long-running work is handled mainly by **Celery** (background jobs with progress tracking) and by **agents / Multi-Agent Swarm** (ReACT loops, often Celery-backed on one machine). You can still run **durable, multi-day style work** across Interconnector clients by designing the task so pieces are independent, share the same project/rules/docs, and report results back through the synced state.

### Recommended pattern for a long durable task across clients

**1. Put the task in a shared Project (master is source of truth)**  
- Create one Project on the master.  
- Put the goal, constraints, rules/prompts, input documents, and any templates there.  
- Enable Interconnector sync for **projects**, **rules**, and **documents** so every client has the same context and policy.  
- Optionally keep learnings/sync under approval so edge nodes do not silently rewrite the master.

**2. Decompose into independent, restartable subtasks**  
Break the long job into units that:
- Can run on different machines without constant live coordination  
- Are **idempotent** (safe to retry if a node restarts)  
- Write clear intermediate outputs (files, notes, or documents in the shared project)

Examples of good decompositions:
- Large research → one subtask per source domain / query set  
- Codebase work → one module or directory per agent  
- Data processing / indexing → shards of files or time ranges  
- Multi-car / edge experiments → one car or one experiment config per client  
- Content pipelines → research → draft → review as separate stages

**3. Use capability reporting to place work**  
Interconnector reports what each node can do (VRAM, plugins, ARM vs x86). Use that to decide:
- **Master** — planning, aggregation, large models, GPU-heavy generation/training, final synthesis  
- **Strong clients** — medium agents, local RAG, lighter generation  
- **Light clients (e.g. Raspberry Pi)** — data collection, simple monitoring, lightweight agents, local inference

You (or a master-side agent) assign subtasks to nodes that match the required capability.

**4. Run the subtasks as local long-running work on each client**  
On each client:
- Open the synced Project  
- Launch an **agent** (or a Swarm of workers on that machine) with a clear scoped goal and the shared Rules  
- Or queue background/Celery-style jobs that already exist for generation, indexing, etc., when they fit  

Swarm itself is primarily **parallel agents on one machine** (isolated worktrees, shared memory, Celery-backed workers, aggregator). It is excellent for parallelizing *within* a node; across nodes you rely on the shared project + separate runs.

**5. Persist progress in the shared project so the work is durable**  
- Agents should write intermediate results, logs, and status into project documents/files (or notes) that Interconnector can sync.  
- Prefer “checkpoint every N steps / every completed subtask” so a reboot or temporary master outage does not lose everything.  
- Clients continue with last-synced state if the master is offline; they resume sync when the master returns.

**6. Aggregate on the master**  
- When subtasks finish, results appear (or are pulled) via document/project sync.  
- Run a supervisor/aggregator agent on the master to merge findings, resolve conflicts, produce the final deliverable, and optionally trigger self-improvement or learning broadcast (with approval).

**7. Optional: scheduled or recurring pieces**  
Guaardvark uses **Celery Beat** for scheduled/idle-triggered work (e.g. self-improvement checks, RAG experiments). You can use scheduled modes for periodic subtasks (re-index, re-check, nightly experiments) while the overall campaign lives in the shared project.

### What this is good for
- Multi-day research + synthesis across several machines  
- Large refactor or analysis split by module, with shared rules  
- Edge fleets (Pis / Donkey Cars / laptops) collecting data or running local agents while the master trains or aggregates  
- Content or media pipelines where generation runs where the GPU is, and lighter steps run on clients  
- Self-improving loops where validated fixes/learnings can be offered to the fleet under approval

### What it is *not* (current design)
- Automatic “submit one job and Interconnector fans it out to every client”  
- A replacement for a true distributed workflow engine (Temporal, full multi-node Celery with shared broker across all nodes, Kubernetes jobs, etc.)  
- Low-latency closed-loop control that needs millisecond coordination between nodes (keep that local)

### Practical checklist
1. Master + clients linked in Interconnector (strong API keys, stable LAN, only needed sync categories on).  
2. One shared Project + Rules that define the overall goal and subtask format.  
3. Explicit subtask list (even a simple markdown checklist in the project).  
4. Assign by capability; run agents/jobs locally on each client.  
5. Write checkpoints into synced docs/files.  
6. Aggregate and finalize on the master; approve any learning/code broadcasts before they spread.

If you describe the concrete long task (e.g. “research X across 10 sources for a week,” “process 50k images,” “multi-car data collection + model training,” “large codebase migration”), a more specific decomposition and placement plan can be outlined for master vs clients.