# plugins/swarm — Swarm Orchestrator (parallel coding agents)

This plugin is the **Swarm Orchestrator**: a FastAPI sidecar (port 8210) that runs coding agents in **parallel** over isolated git worktrees, and it backs the **/swarm** UI page. It is **not** the Film Crew sequential video-production pipeline in [`/backend/services/swarm`](../../backend/services/swarm/README.md), whose `swarm` directory name is a legacy artifact and which backs the **/film-crew** UI page.
