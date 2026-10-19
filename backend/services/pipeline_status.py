"""Music video and Film Crew runs in get_generation_status's shape.

Both pipelines keep their state on a row (MusicVideo, Production): a stage, a
status (``failed_<stage>`` on failure), an error blob, and per-cut or per-shot
records. These builders read the dicts the pipelines' own APIs serve
(``GET /api/music-video/<id>``, ``GET /api/production/<id>``), so the backend
and the MCP server answer the same way. Pure: no I/O.

Ids: the generate tools return a numeric id that both pipelines share as a
number space, so get_generation_status takes it with a prefix:
``music_video:12`` or ``production:5`` (``mv:``, ``film_crew:`` and ``film:``
are accepted too).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from backend.services.job_types import RenderErrorKind, describe_failure, failure_kind

_ID = re.compile(r"^(music_video|mv|production|film_crew|film)[:_-](\d+)$", re.IGNORECASE)
_KIND = {"music_video": "music_video", "mv": "music_video",
         "production": "production", "film_crew": "production", "film": "production"}

# Film Crew stages in order; progress is the share of stages passed.
FILM_STAGES = ["draft", "screenwriting", "casting", "cinematography", "storyboard_gen",
               "awaiting_approval", "rendering", "complete"]
# Stages that wait on a person rather than run.
FILM_GATES = {"casting", "awaiting_approval"}
MV_GATES = {"awaiting_approval"}


def parse_pipeline_id(text: str) -> Optional[tuple[str, int]]:
    """("music_video" | "production", id) for a prefixed id, else None."""
    m = _ID.match((text or "").strip())
    if not m:
        return None
    return _KIND[m.group(1).lower()], int(m.group(2))


def status_id(kind: str, row_id: int) -> str:
    """The id a generate tool hands out for get_generation_status."""
    return f"{kind}:{row_id}"


def _normalized(stage: str, status: str, gates: set[str]) -> str:
    """One of running, waiting, complete, failed, cancelled."""
    status = status or stage or ""
    if status.startswith("failed"):
        return "failed"
    if status.startswith("cancelled") or stage == "cancelled":
        return "cancelled"
    if stage == "complete":
        return "complete"
    if stage in gates:
        return "waiting"
    return "running"


def _pipeline_failure(state: str, error_blob: Any) -> Optional[dict]:
    if state == "cancelled":
        return describe_failure(RenderErrorKind.CANCELLED, "Cancelled")
    if state != "failed":
        return None
    blob = error_blob if isinstance(error_blob, dict) else {}
    message = str(blob.get("error") or "the pipeline stopped")
    stage = blob.get("stage")
    if stage:
        message = f"{stage}: {message}"
    # The pipelines store the kind when the failure carried one.
    kind = blob.get("error_kind") or failure_kind(blob.get("error"))
    return describe_failure(kind, message)


def music_video_info(d: dict) -> dict:
    """A music video (the dict GET /api/music-video/<id> returns)."""
    stage = d.get("current_stage") or "draft"
    state = _normalized(stage, d.get("status") or "", MV_GATES)
    progress = d.get("progress") or {}
    files = []
    if d.get("output_document_id"):
        files.append({"url": f"/api/files/document/{d['output_document_id']}/download"})
    note = None
    if state == "waiting":
        note = ("Waiting for the cut plan to be approved in Studio (POST /api/music-video/"
                f"{d.get('id')}/approve after the user agrees); nothing renders until then.")
        held = d.get("clips_needing_review")
        if held:
            note = f"{held} rendered cut(s) are held for review in Studio; the video is not assembled until each is approved or re-rendered."
    elif state == "running" and d.get("clips_needing_review"):
        note = f"{d['clips_needing_review']} rendered cut(s) held for review in Studio."
    failure = _pipeline_failure(state, d.get("error_blob"))
    return {
        "kind": "music video", "noun": "", "batch_id": status_id("music_video", d.get("id")),
        "status": state, "stage": stage,
        "progress": progress.get("overall_pct") if state == "running" else None,
        "message": _active_stage_message(progress),
        "completed": d.get("clips_done"), "total": d.get("clip_count") or d.get("cut_count"),
        "failed": None,
        "error": failure["message"] if failure else None, "errors": [],
        "failure": failure, "failures": [] if failure else None,
        "eta_seconds": progress.get("eta_seconds"),
        "files": files, "note": note,
        "studio_url": f"/music-video?id={d.get('id')}",
    }


def _active_stage_message(progress: dict) -> str:
    for row in progress.get("stages") or []:
        if row.get("state") in ("active", "queued", "waiting"):
            return row.get("message") or row.get("label") or ""
    return ""


def production_info(d: dict) -> dict:
    """A Film Crew production (the dict GET /api/production/<id> returns)."""
    stage = d.get("current_stage") or "draft"
    state = _normalized(stage, d.get("status") or "", FILM_GATES)
    shots = d.get("shots") or []
    boards = sum(1 for s in shots if s.get("storyboard_image_path"))
    clips = sum(1 for s in shots if s.get("video_clip_path"))
    progress = None
    if state == "running" and stage in FILM_STAGES:
        progress = int(100 * FILM_STAGES.index(stage) / (len(FILM_STAGES) - 1))
    if stage == "rendering":
        done, total, message = clips, len(shots), f"rendering clips: {clips}/{len(shots)}"
    else:
        done, total = None, None
        message = f"storyboards: {boards}/{len(shots)}" if shots and stage in ("storyboard_gen", "awaiting_approval") else ""
    note = None
    if state == "waiting":
        note = ("Waiting for casting to be confirmed in Studio." if stage == "casting"
                else "Waiting for the storyboard to be approved in Studio; nothing renders until then.")
    failure = _pipeline_failure(state, d.get("error_blob"))
    return {
        "kind": "Film Crew production", "noun": "", "batch_id": status_id("production", d.get("id")),
        "status": state, "stage": stage, "progress": progress, "message": message,
        "completed": done, "total": total, "failed": None,
        "error": failure["message"] if failure else None, "errors": [],
        "failure": failure, "failures": [] if failure else None,
        "files": [], "note": note, "studio_url": "/film-crew",
    }
