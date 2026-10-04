"""Which backend API areas the CLI exposes, and why the rest are not exposed.

`cli/tests/test_spec_parity.py` walks `backend/api/*_api.py` and fails unless every
area appears in exactly one of the three maps below:

``EXPOSED``      a CLI command drives it today
``PLANNED``      a command is planned; value names the phase in docs/CLI_PLAN.md
``NOT_EXPOSED``  deliberately has no CLI command; value says why

Keys are backend API module stems (`backend/api/<key>_api.py`). Keying on the module
rather than the URL prefix matters: `inbound_guard_api.py` serves
`/api/settings/inbound_guard`, so a prefix-derived key would collide with `settings`.

This is the file that stops the coverage table in `docs/CLI_SPEC.md` from drifting
from the code. When upstream adds a backend API area, the test fails and names it —
either write the command, or declare it here with a reason.
"""
from __future__ import annotations

# --- exposed ---------------------------------------------------------------
# area -> the command group(s) that drive it
EXPOSED: dict[str, str] = {
    "agent_chat": "chat (REPL agent mode)",
    "agent_control": "chat --abort (REPL)",
    "agents": "agents",
    "audio_foundry": "audio",
    "automation": "doctor, start, stop",
    "backup": "backup",
    "batch_image_generation": "images, generate image",
    "batch_video_generation": "videos",
    "bulk_generation": "jobs (bulk generation job polling)",
    "cast_library": "cast",
    "clients": "clients",
    "connections": "connections",
    "content_management": "content",
    "code_intelligence": "analyze, init (REPL)",
    "enhanced_chat": "chat",
    "entity_indexing": "index",
    "files": "files",
    "generation": "generate",
    "gpu": "gpu, dashboard",
    "indexing": "index",
    "inbound_guard": "guard",
    "infographic": "infographic",
    "interconnector": "family",
    "jobs": "jobs",
    "lessons": "lessons",
    "memory": "remember, memory (REPL)",
    "meta": "rules, status, dashboard, jobs, rag, quality",
    "model": "models, status",
    "music_video": "music-video",
    "plugins": "plugins",
    "production": "film-crew",
    "projects": "projects",
    "rag_autoresearch": "rag eval, autoresearch",
    "rules": "rules",
    "settings": "settings",
    "self_improvement": "improve",
    "social_outreach": "outreach",
    "swarm": "swarm",
    "system_map": "system-map",
    "tasks": "tasks",
    "tools": "tools, tool (REPL)",
    "training_datasets": "training",
    "unified_chat": "chat, ask",
    "upscaling": "upscale",
    "voice": "audio tts, audio transcribe",
    "video_editor": "video-editor",
    "web_search": "web",
    "websites": "websites",
}

# --- planned ---------------------------------------------------------------
# area -> planned command group and the phase in docs/CLI_PLAN.md section 3
PLANNED: dict[str, str] = {
    "wordpress": "wordpress — CLI_PLAN 3.11 (Phase 4)",
    "llm_provider": "llm — CLI_PLAN 3.1 (Phase 4)",
}

# --- not exposed -----------------------------------------------------------
# area -> the reason there is no CLI command
NOT_EXPOSED: dict[str, str] = {
    "addresses": "contacts / address book — Studio-only surface",
    "admin_filename_cleanup": "maintenance endpoint for filename cleanup; no user-facing command",
    "auth": "web session login; the CLI authenticates with an API key instead",
    "brain": "chat-brain routing internals; reached through chat, not directly",
    "cache": "cache diagnostics",
    "cache_stats": "cache statistics shown in the Studio footer",
    "celery_monitor": "worker introspection for the Studio monitor; doctor reads health generically",
    "chat_sessions": "session sidebar data; the chat group covers the CLI need",
    "claude_advisor": "advisor surface, Studio-only",
    "cluster": "multi-node cluster management",
    "code_execution": "code-editor execution — an editing surface (CLI_PLAN D2)",
    "code_search": "exact-text search; reached through code-intelligence for CLI purposes",
    "csv_compare": "CSV comparison UI",
    "diagnostics": "diagnostics bundle for support",
    "distributed": "distributed execution internals",
    "doc_query": "document Q&A internals; search serves the CLI",
    "docs": "in-app documentation browser",
    "enhanced_context_generation": "context generation internals",
    "entity_links": "knowledge-graph link editing (Studio)",
    "excel": "spreadsheet comparison UI",
    "file_operations": "bulk file operations used by the documents UI",
    "google_indexing": "search-engine submission workflow (Studio)",
    "gpu_orchestrator": "GPU budget internals; gpu and dashboard surface the state",
    "hierarchy": "folder-tree internals; files uses the files API",
    "image": "raw image serving for the Studio",
    "index_mgmt": "index administration internals",
    "log": "log API for the Studio viewer; the CLI reads the log directory directly",
    "metadata_indexing": "metadata indexing internals",
    "node": "node registration for the Interconnector; family covers the user surface",
    "orchestrator": "plugin orchestrator internals; plugins surfaces status",
    "output": "output registration helper; the CLI registers through files and generation",
    "outputs": "external plugin registration endpoint, called by plugins and not by the CLI",
    "progress_test": "developer progress harness",
    "query": "document Q&A internals; search serves the CLI",
    "rag_debug": "RAG debugging",
    "reboot": "service restart; start and stop cover it",
    "retrieve": "retrieval internals",
    "search": "document search internals; the search command reaches KB search through the tools API",
    "self_code": "self-code scanning, Studio-only",
    "simple_chat": "legacy chat endpoint superseded by the chat group",
    "state": "Studio UI session state",
    "system": "system info surfaces through meta; status and health read that",
    "task_scheduler": "scheduler internals; tasks is the user surface",
    "unified_generation": "generation internals behind images and videos",
    "unified_jobs_resource": "job resource internals behind jobs",
    "upload": "upload internals; files upload uses the files API",
    "video_overlay": "text-overlay editing, Studio-only",
}


def classify(area: str) -> str:
    """``"exposed"``, ``"planned"``, ``"not_exposed"`` or ``"unknown"``."""
    if area in EXPOSED:
        return "exposed"
    if area in PLANNED:
        return "planned"
    if area in NOT_EXPOSED:
        return "not_exposed"
    return "unknown"


def all_declared() -> dict[str, str]:
    """Every declared area mapped to its category."""
    return {
        **{a: "exposed" for a in EXPOSED},
        **{a: "planned" for a in PLANNED},
        **{a: "not_exposed" for a in NOT_EXPOSED},
    }
