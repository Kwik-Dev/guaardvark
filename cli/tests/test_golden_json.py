"""Golden ``--json`` snapshots for the read-only command surface (CLI_PLAN 4.4).

Covers the fork-owned groups and the read-only upstream groups alike. The ad-hoc
contract tests (`test_fork_phase*_commands.py`, `test_system_json_contracts.py`) assert
the *values* a command maps a response into; they only catch shape drift where someone
remembered to look. This tier pins the whole emitted payload of each command against a
file in ``cli/tests/golden/``. A renamed or dropped key is a breaking change for every
script that reads ``--json``, and it now fails CI naming the exact path that moved.

Regenerate deliberately::

    pytest cli/tests --update-golden

then read the diff before committing it. Volatile fields (clocks, measured spans, the
temporary home) are normalised by the ``golden`` fixture, so a snapshot is identical on
every machine; their *presence* is still part of the contract.

Only read-only commands are snapshotted — read-only queries, including a diagnostic
`POST` that changes nothing (e.g. `quality scorecard`), but nothing that writes, approves
or renders. The ``--yes``-gated writes are covered by their own gate tests, which assert
the refusal (and the request body) rather than the emitted shape, and a command that
spends GPU is covered by `test_fork_gpu_gate.py`.
The fake backend answers each route with a fixed body, so the snapshot is about
*shape*, not about the backend.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from llx.main import app

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


@dataclass(frozen=True)
class Case:
    name: str
    argv: list
    routes: list = field(default_factory=list)


def _r(method: str, path: str, payload, status: int = 200) -> dict:
    return {"method": method, "path": path, "payload": payload, "status": status}


_SUBJECTS = {"success": True, "data": {"subjects": [
    {"id": 1, "name": "Elara", "kind": "character", "training_status": "ready",
     "lora_path": "/models/elara.safetensors"},
    {"id": 5, "name": "Lumin Seed", "kind": "prop", "training_status": "untrained"},
]}}

CASES = [
    # --- guard -------------------------------------------------------------
    Case("guard.status", ["guard", "status"], [
        _r("GET", "/api/settings/inbound_guard", {"success": True, "data": {"mode": "off", "scan_count": 2, "held": 0}}),
    ]),
    Case("guard.scans", ["guard", "scans"], [
        _r("GET", "/api/settings/inbound_guard/scans", {"success": True, "data": {"scans": [
            {"id": 3, "decision": "held", "kind": "code", "by": "guard",
             "created_at": "2026-09-01T10:00:00", "path": "/tmp/x.py"},
        ]}}),
    ]),
    Case("guard.git", ["guard", "git", "abc123"], [
        _r("GET", "/api/settings/inbound_guard/git/abc123",
           {"success": True, "data": {"digest": "abc123", "files": ["backend/x.py"]}}),
    ]),
    # --- improve -----------------------------------------------------------
    Case("improve.status", ["improve", "status"], [
        _r("GET", "/api/self-improvement/status",
           {"success": True, "data": {"running": False, "model": "qwen", "pending": 4}}),
    ]),
    Case("improve.runs", ["improve", "runs"], [
        _r("GET", "/api/self-improvement/runs", {"success": True, "data": {"runs": [
            {"id": 9, "status": "complete", "model_name": "qwen", "created_at": "2026-09-01T10:00:00"},
        ]}}),
    ]),
    Case("improve.metrics", ["improve", "metrics"], [
        _r("GET", "/api/self-improvement/metrics",
           {"success": True, "data": {"accepted": 3, "rejected": 1, "acceptance_rate": 0.75}}),
    ]),
    Case("improve.pending", ["improve", "pending"], [
        _r("GET", "/api/self-improvement/pending-fixes", {"success": True, "data": [
            {"id": 9, "status": "pending", "file": "/repo/backend/x.py", "description": "tighten the loop"},
        ]}),
    ]),
    # --- system-map --------------------------------------------------------
    Case("system-map.health", ["system-map", "health"], [
        _r("GET", "/api/system-map/health", {"status": "ok", "findings": 2}),
    ]),
    Case("system-map.findings", ["system-map", "findings"], [
        _r("GET", "/api/system-map/findings", {"findings": [
            {"id": "f1", "severity": "high", "kind": "cycle", "label": "import loop"},
        ]}),
    ]),
    # --- content -----------------------------------------------------------
    Case("content.pages", ["content", "pages"], [
        _r("GET", "/api/content/pages", {"pages": [
            {"id": 4, "title": "Release notes", "status": "draft", "uploaded_at": "2026-09-01"},
        ]}),
    ]),
    Case("content.page", ["content", "page", "4"], [
        _r("GET", "/api/content/pages/4", {"id": 4, "title": "Release notes", "body": "hello"}),
    ]),
    Case("content.stats", ["content", "stats"], [
        _r("GET", "/api/content/stats", {"pages": 4, "uploaded": 2, "drafts": 2}),
    ]),
    # --- websearch ---------------------------------------------------------
    Case("websearch.status", ["websearch", "status"], [
        _r("GET", "/api/web-search/status", {"success": True, "data": {"backend": "brave", "available": True}}),
    ]),
    Case("websearch.search", ["websearch", "search", "aardvark docs"], [
        _r("POST", "/api/web-search/search", {"success": True, "data": {"results": [
            {"title": "Docs", "url": "https://example.test", "snippet": "the docs"},
        ]}}),
    ]),
    Case("websearch.quick-search", ["websearch", "quick-search", "aardvark"], [
        _r("POST", "/api/web-search/quick-search", {"success": True, "data": {"answer": "an aardvark"}}),
    ]),
    Case("websearch.sitemap", ["websearch", "sitemap", "https://example.test"], [
        _r("POST", "/api/web-search/sitemap", {"success": True, "data": {"urls": ["https://example.test/a"]}}),
    ]),
    # --- connections -------------------------------------------------------
    Case("connections.list", ["connections", "list"], [
        _r("GET", "/api/connections", {"connections": [
            {"id": 2, "provider": "youtube", "name": "Main channel", "status": "connected"},
        ]}),
    ]),
    Case("connections.providers", ["connections", "providers"], [
        _r("GET", "/api/connections/providers", {"providers": [
            {"id": "youtube", "auth": "oauth"},
        ]}),
    ]),
    Case("connections.environment", ["connections", "environment"], [
        _r("GET", "/api/connections/environment", {"environment": {"ffmpeg": "present"}}),
    ]),
    # --- approvals (an aggregate of three routes) --------------------------
    Case("approvals.list", ["approvals", "list"], [
        _r("GET", "/api/connections/publishes", {"publishes": [{"id": 1, "title": "Post", "status": "pending"}]}),
        _r("GET", "/api/settings/inbound_guard/scans", {"success": True, "data": {"scans": [
            {"id": 7, "decision": "held", "kind": "code"},
        ]}}),
        _r("GET", "/api/social-outreach/queue", {"queue": [{"id": 5, "platform": "reddit", "status": "draft"}]}),
    ]),
    # --- cast --------------------------------------------------------------
    Case("cast.list", ["cast", "list"], [_r("GET", "/api/cast-library", _SUBJECTS)]),
    Case("cast.show", ["cast", "show", "1"], [
        _r("GET", "/api/cast-library/subjects/1", {"subject": {
            "id": 1, "name": "Elara", "kind": "character", "training_status": "ready",
        }}),
    ]),
    Case("cast.samples", ["cast", "samples", "1"], [
        _r("GET", "/api/cast-library/subjects/1/samples", {"samples": [
            {"id": 11, "approved": True, "status": "ready", "seed": 42},
        ]}),
    ]),
    # --- upscale -----------------------------------------------------------
    Case("upscale.models", ["upscale", "models"], [
        _r("GET", "/api/upscaling/models", {"models": [{"id": "hat-l", "installed": True}]}),
    ]),
    Case("upscale.jobs", ["upscale", "jobs"], [
        _r("GET", "/api/upscaling/jobs", {"jobs": [
            {"id": "j7", "status": "running", "kind": "image", "progress": 10},
        ]}),
    ]),
    Case("upscale.status", ["upscale", "status", "j7"], [
        _r("GET", "/api/upscaling/jobs/j7", {"id": "j7", "status": "complete", "progress": 100}),
    ]),
    # --- infographic -------------------------------------------------------
    Case("infographic.models", ["infographic", "models"], [
        _r("GET", "/api/infographic/models", {"models": [{"id": "flux", "installed": True}]}),
    ]),
    Case("infographic.status", ["infographic", "status"], [
        _r("GET", "/api/infographic/status", {"ready": True, "jobs": [{"id": 3, "status": "complete"}]}),
    ]),
    Case("infographic.download-status", ["infographic", "download-status"], [
        _r("GET", "/api/infographic/models/download-status", {"model": "flux", "state": "idle"}),
    ]),
    # --- audio models (fork extension of the upstream group) ---------------
    Case("audio.models", ["audio", "models"], [
        _r("GET", "/api/audio-foundry/models", {"success": True, "data": {"models": [
            {"id": "ace-step", "installed": True},
        ]}}),
    ]),
    # --- video-editor ------------------------------------------------------
    Case("video-editor.health", ["video-editor", "health"], [
        _r("GET", "/api/video-editor/health", {"status": "ok", "ffmpeg": True}),
    ]),
    Case("video-editor.projects", ["video-editor", "projects"], [
        _r("GET", "/api/video-editor/projects", {"projects": [
            {"id": "abc123", "name": "Last Spark cut", "isDirty": False},
        ]}),
    ]),
    Case("video-editor.project", ["video-editor", "project", "abc123"], [
        _r("GET", "/api/video-editor/projects/abc123", {"project": {"id": "abc123", "name": "Last Spark cut"}}),
    ]),
    Case("video-editor.jobs", ["video-editor", "jobs"], [
        _r("GET", "/api/video-editor/jobs", {"jobs": [
            {"id": "r1", "kind": "render", "status": "running"},
        ]}),
    ]),
    Case("video-editor.filters", ["video-editor", "filters"], [
        _r("GET", "/api/video-editor/catalog/filters", {"filters": [{"id": "blur", "name": "Blur"}]}),
    ]),
    Case("video-editor.transitions", ["video-editor", "transitions"], [
        _r("GET", "/api/video-editor/catalog/transitions", {"transitions": [{"id": "fade", "name": "Fade"}]}),
    ]),
    # --- training ----------------------------------------------------------
    Case("training.datasets", ["training", "datasets"], [
        _r("GET", "/api/training_datasets", {"datasets": [
            {"id": 2, "name": "Elara refs", "path": "/data/elara", "created_at": "2026-08-01T00:00:00"},
        ]}),
    ]),
    Case("training.backends", ["training", "backends"], [
        _r("GET", "/api/plugins", {"plugins": [
            {"id": "comfyui", "status": "running", "enabled": True},
            {"id": "lora_trainer", "status": "stopped", "enabled": True},
        ]}),
    ]),
    # --- llm ---------------------------------------------------------------
    Case("llm.provider", ["llm", "provider"], [
        _r("GET", "/api/llm/provider", {"success": True, "data": {
            "provider": "ollama", "cloud_models_enabled": False, "cloud_active": False,
        }}),
    ]),
    Case("llm.models", ["llm", "models"], [
        _r("GET", "/api/llm/provider/models", {"success": True, "data": {"models": [
            {"id": "llama3", "source": "local"},
        ]}}),
    ]),
    # --- wordpress ---------------------------------------------------------
    Case("wordpress.sites", ["wordpress", "sites"], [
        _r("GET", "/api/wordpress/sites", {"sites": [
            {"id": 1, "name": "Blog", "url": "https://example.test", "is_connected": True},
        ]}),
    ]),
    Case("wordpress.site", ["wordpress", "site", "1"], [
        _r("GET", "/api/wordpress/sites/1", {"site": {"id": 1, "name": "Blog"}}),
    ]),
    Case("wordpress.pages", ["wordpress", "pages"], [
        _r("GET", "/api/wordpress/pages", {"pages": [
            {"id": 8, "title": "Hello", "slug": "hello", "status": "publish", "site_id": 1},
        ]}),
    ]),
    Case("wordpress.pull-status", ["wordpress", "pull-status", "1"], [
        _r("GET", "/api/wordpress/pull/status/1", {"success": True, "data": {"state": "idle"}}),
    ]),
    # --- film-crew (fork extensions) --------------------------------------
    Case("film-crew.subjects", ["film-crew", "subjects", "3"], [
        _r("GET", "/api/production/3/subjects", {"subjects": [
            {"id": 1, "name": "Elara", "kind": "character", "lora_path": "", "cast_required": True},
        ]}),
    ]),
    Case("film-crew.shots", ["film-crew", "shots", "3"], [
        _r("GET", "/api/production/3", {"id": 3, "name": "The Last Spark", "shots": [
            {"id": 7, "shot_number": 2, "scene_number": 1, "approved": True,
             "storyboard_image_path": "/out/sb7.png", "video_clip_path": "", "regen_count": 1,
             "description": "A lighthouse at dusk"},
        ]}),
    ]),
    Case("film-crew.templates", ["film-crew", "templates"], [
        _r("GET", "/api/production/script-templates", {"templates": [
            {"filename": "noir.md", "name": "Noir", "size_bytes": 1200},
        ]}),
    ]),
    # --- music-video (fork extensions) ------------------------------------
    # `cuts` is state-dependent on the backend: with no clips it emits the timing keys
    # (start_s/end_s/section_label/prompt), with clips it emits the clip keys. The two
    # cases pin both shapes deliberately, so a script must branch on which key is present.
    Case("music-video.cuts", ["music-video", "cuts", "1"], [
        _r("GET", "/api/music-video/1", {"id": 1, "cut_plan": [
            {"index": 0, "start_s": 0.0, "end_s": 3.5, "section_label": "intro", "prompt": "wide shot"},
        ], "clips": []}),
    ]),
    Case("music-video.clips", ["music-video", "clips", "1"], [
        _r("GET", "/api/music-video/1", {"id": 1, "cut_plan": [], "clips": [
            {"index": 0, "status": "complete", "clip_path": "/out/cut0.mp4", "storyboard_path": "/out/sb0.png"},
        ]}),
    ]),
    # Issue #7: `list` resolves `output_document_id` to a filename/logical path, and the
    # second declared route is the point — the CLI must join the document store to know
    # where the render is. `clips[]`/`cut_plan` are deliberately absent from the row.
    Case("music-video.list", ["music-video", "list"], [
        _r("GET", "/api/music-video", {"music_videos": [
            {"id": 7, "name": "Neon", "status": "complete", "current_stage": "complete",
             "cut_count": 3, "clips_done": 3, "clip_count": 3,
             "output_document_id": 234, "song_document_id": 12,
             "clips": [{"index": 0, "clip_path": "/out/cut0.mp4", "prompt": "wide"}],
             "cut_plan": [{"index": 0, "start_s": 0.0, "end_s": 3.5}]},
        ]}),
        _r("GET", "/api/files/document/234", {"data": {
            "filename": "arrangement_abc.mp4", "path": "Videos/arrangement_abc.mp4"}}),
    ]),
    # Issue #8: settings that produced an artifact must survive `--json` untouched so a script
    # can feed them back into a re-run. Each case pins the record the CLI passes through.
    Case("images.status", ["images", "status", "ImageBatch_1"], [
        _r("GET", "/api/batch-image/status/ImageBatch_1", {"data": {
            "batch_id": "ImageBatch_1", "status": "completed", "progress": 100,
            "completed_images": 1, "total_images": 1,
            "retry_data": {"mode": "text", "prompts": ["a red fox"],
                           "params": {"model": "zimage-turbo", "steps": 9, "guidance": 0.0}}}}),
    ]),
    Case("videos.status", ["videos", "status", "Batch_1"], [
        _r("GET", "/api/batch-video/status/Batch_1", {
            "batch_id": "Batch_1", "status": "completed", "total_videos": 1,
            "completed_videos": 1, "failed_videos": 0,
            "retry_data": {"mode": "text", "prompts": ["a kite"],
                           "params": {"model": "wan22-5b", "seed": 42, "num_inference_steps": 20}},
            "results": [{"item_id": "0", "success": True, "video_path": "a.mp4"}]}),
    ]),
    Case("music-video.status", ["music-video", "status", "7"], [
        _r("GET", "/api/music-video/7", {
            "id": 7, "name": "Neon", "status": "complete", "current_stage": "complete",
            "subject_ids": [1], "user_treatment": "Elara walks the overgrown path.",
            "use_lora_consistency": True, "keyframe_model": "from-lora", "i2v_model": "wan22-5b",
            "settings": {"subject_ids": [1], "use_lora_consistency": True, "max_stretch": 2},
            "cut_plan": [], "clips": []}),
    ]),
    Case("film-crew.status", ["film-crew", "status", "3"], [
        _r("GET", "/api/production/3", {
            "id": 3, "name": "The Last Spark", "status": "complete", "current_stage": "complete",
            "shots": [], "script_text": "Title: The Last Spark",
            "settings_json": {"video_model": "wan22-5b"}}),
    ]),
    # --- api escape hatch --------------------------------------------------
    Case("api.routes", ["api", "routes"], [
        _r("GET", "/api/routes", {"success": True, "data": {"routes": [
            {"rule": "/api/cast-library", "methods": "GET,POST"},
        ]}}),
    ]),
    # --- captions ----------------------------------------------------------
    Case("captions.status", ["video-editor", "captions-status", "j1"], [
        _r("GET", "/api/video-overlay/render-status/j1", {"job_id": "j1", "status": "running", "progress": 40}),
    ]),

    # --- upstream read-only groups ----------------------------------------
    # The groups below are upstream-owned. Only the read-only leaves are pinned: a leaf
    # that writes, spends GPU, opens a live loop or reads local files is left to its own
    # gate tests (the `--yes` refusal, the GPU gate), because the fake backend cannot
    # model it and the emitted shape is not the contract there.
    Case("agents.list", ["agents", "list"], [
        _r("GET", "/api/agents", {"agents": [
            {"id": "a1", "name": "Scout", "enabled": True, "tools": ["search_codebase"]},
        ]}),
    ]),
    Case("audio.voices", ["audio", "voices"], [
        _r("GET", "/api/audio-foundry/voices", {"data": {"voices": [
            {"id": "af_heart", "name": "Heart", "engine": "kokoro"},
        ]}}),
    ]),
    Case("backup.list", ["backup", "list"], [
        _r("GET", "/api/backups", {"backups": ["guaardvark-2026-09-01.tar.gz"]}),
    ]),
    Case("clients.list", ["clients", "list"], [
        _r("GET", "/api/clients", {"data": [{"id": 1, "name": "Acme", "project_count": 2}]}),
    ]),
    Case("family.list", ["family", "list"], [
        _r("GET", "/api/interconnector/nodes", {"nodes": [
            {"id": "n1", "name": "Studio-2", "host": "10.0.0.5", "port": 5000,
             "status": "online", "role": "member"},
        ]}),
    ]),
    Case("family.status", ["family", "status"], [
        _r("GET", "/api/interconnector/status", {"data": {
            "enabled": True, "role": "primary", "connected_nodes": 1,
            "pending_updates": 2, "node_id": "abcdef0123456789"},
        }),
    ]),
    Case("files.list", ["files", "list"], [
        _r("GET", "/api/files/browse", {"data": {
            "folders": [{"name": "docs"}],
            "documents": [{"id": 9, "filename": "spec.pdf", "size": 2048}]},
        }),
    ]),
    Case("gpu.status", ["gpu", "status"], [
        _r("GET", "/api/gpu/status", {"data": {
            "available": True, "owner": "none", "gpu_name": "Apple M2 Max",
            "vram_total": "64 GB", "utilization": 12},
        }),
    ]),
    Case("health", ["health"], [
        _r("GET", "/api/health", {"status": "ok", "version": "1.2.3", "uptime_seconds": 3600}),
    ]),
    Case("images.models", ["images", "models"], [
        _r("GET", "/api/batch-image/models", {"models": [
            {"id": "flux", "name": "FLUX.1", "installed": True},
        ]}),
    ]),
    Case("index.status", ["index", "status"], [
        _r("GET", "/api/entity-indexing/status", {"entity_counts": {"characters": 4, "props": 2}}),
    ]),
    Case("jobs.list", ["jobs", "list"], [
        _r("GET", "/api/meta/active_jobs", {"active_jobs": [
            {"task_id": "t1", "name": "Render", "type": "video", "status": "running"},
        ]}),
    ]),
    Case("lessons.list", ["lessons", "list", "--session", "s1"], [
        _r("GET", "/api/lessons/active", {"data": {
            "active": True, "lesson_id": "l1", "title": "Debugging the dispatch loop"},
        }),
    ]),
    Case("models.list", ["models", "list"], [
        _r("GET", "/api/model/list", {"data": {"models": [
            {"id": "gemma4:e4b", "name": "gemma4:e4b"},
        ]}}),
    ]),
    Case("plugins.list", ["plugins", "list"], [
        _r("GET", "/api/plugins", {"plugins": [
            {"id": "comfyui", "status": "running", "enabled": True, "port": 8188},
        ]}),
    ]),
    Case("plugins.status", ["plugins", "status"], [
        _r("GET", "/api/plugins/orchestrator/state", {"data": {
            "state": "ready", "plugins": [{"id": "comfyui", "status": "running"}]},
        }),
    ]),
    Case("projects.list", ["projects", "list"], [
        _r("GET", "/api/projects", {"data": [{"id": 1, "name": "The Last Spark", "client_id": 1}]}),
    ]),
    Case("quality.scorecard", ["quality", "scorecard"], [
        _r("POST", "/api/meta/quality-scorecard", {"data": {
            "score": 88, "checks": [{"name": "coverage", "ok": True}]},
        }),
    ]),
    Case("rag.status", ["rag", "status"], [
        _r("POST", "/api/tools/execute", {"success": True, "result": {
            "success": True,
            "output": "Documents (2):\nElara — 12 passages\nNoir — 3 passages",
            "metadata": {"total": 2},
        }}),
        _r("GET", "/api/meta/index-info", {"embedding_model": "bge-m3"}),
    ]),
    Case("rules.list", ["rules", "list"], [
        _r("GET", "/api/rules", {"data": [
            {"id": 1, "name": "no emojis", "level": "global", "type": "style", "is_active": True},
        ]}),
    ]),
    Case("settings.list", ["settings", "list"], [
        _r("GET", "/api/settings", {"data": {
            "settings": {"active_video_model": "wan", "theme": "dark"},
            "settable": {"active_video_model": True, "theme": False},
        }}),
    ]),
    Case("status", ["status"], [
        _r("GET", "/api/health", {"status": "ok", "version": "9.9.9", "uptime_seconds": 3600}),
        _r("GET", "/api/model/status", {"data": {"text_model": "gemma4:e4b"}}),
        _r("GET", "/api/health/celery", {"status": "up", "workers": ["w1"]}),
        _r("GET", "/api/meta/metrics", {"data": {"gpu_mem": 12.0, "cpu_percent": 18.0}}),
        _r("GET", "/api/automation/mcp/status", {"mcp_enabled": False}),
    ]),
    Case("swarm.list", ["swarm", "list"], [
        _r("GET", "/api/swarm/history", {"swarms": [
            {"id": "sw1", "status": "complete", "task_count": 3},
        ]}),
    ]),
    Case("tasks.list", ["tasks", "list"], [
        _r("GET", "/api/tasks", {"data": [
            {"id": "t1", "name": "Render", "task_type": "video", "status": "running", "progress": 40},
        ]}),
    ]),
    Case("videos.models", ["videos", "models"], [
        _r("GET", "/api/batch-video/models", {"data": {"models": [
            {"id": "wan", "name": "Wan 2.1", "is_ready": True, "active": True,
             "vram_mb": 12000, "capabilities": ["text2video"]},
        ]}}),
    ]),
    Case("websites.list", ["websites", "list"], [
        _r("GET", "/api/websites/", {"data": [
            {"id": 1, "name": "Blog", "url": "https://example.test", "page_count": 3},
        ]}),
    ]),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_json_snapshot(case, fake_backend, cli_runner, golden):
    for route in case.routes:
        fake_backend.route(
            route["method"], route["path"], status=route["status"], json=route["payload"],
        )

    result = cli_runner.invoke(app, [*case.argv, "--json"])
    golden(case.name, result)

    # The snapshot pins what the CLI *emitted*; this pins what it *called*. Routes match
    # on (method, path) alone, so without this a dropped call or a wrong path still passes
    # against the declared route set. (Query strings and bodies are not compared here —
    # the phase tests assert those.)
    expected = sorted((r["method"].upper(), r["path"]) for r in case.routes)
    observed = sorted((method, path) for method, path, _ in fake_backend.calls)
    assert observed == expected, (
        f"{case.name}: the CLI called {observed}, but the case declared {expected}"
    )


def test_no_orphan_or_missing_golden_files():
    """A deleted case must not leave a snapshot with no coverage behind."""
    on_disk = {p.stem for p in GOLDEN_DIR.glob("*.json")}
    declared = {c.name for c in CASES}
    assert on_disk == declared, (
        f"orphan snapshots (delete them): {sorted(on_disk - declared)}; "
        f"missing snapshots (run --update-golden): {sorted(declared - on_disk)}"
    )
