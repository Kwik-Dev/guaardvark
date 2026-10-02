"""Read any job the CLI prints an id for, through the route that owns it.

The commands hand out ids of different shapes: image batches (ImageBatch_…),
video batches (VideoBatch_…), bulk CSV jobs (bulk_gen_…), Audio Foundry jobs
(32 hex characters), task numbers, and the unified "kind:native_id" form of
/api/jobs. Each is answered by its own status route; this module picks the
route from the id and returns one shape for `jobs status` and `jobs watch`:

    {"id", "kind", "status", "percent", "message", "files", "raw"}

`status` is one of pending, running, completed, failed, cancelled.
"""

from __future__ import annotations

import re
from typing import Any

from llx.client import LlxClient, LlxError

TERMINAL = ("completed", "failed", "cancelled")

_STATUS = {
    "queued": "pending", "pending": "pending", "waiting": "pending", "start": "running",
    "running": "running", "processing": "running", "in_progress": "running", "paused": "running",
    "complete": "completed", "completed": "completed", "done": "completed", "success": "completed",
    "error": "failed", "failed": "failed", "failure": "failed",
    "cancelled": "cancelled", "canceled": "cancelled", "cancelling": "running",
}

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _norm(status: Any) -> str:
    return _STATUS.get(str(status or "").strip().lower(), str(status or "unknown").lower())


def _unwrap(data: dict) -> dict:
    inner = data.get("data") if isinstance(data, dict) else None
    return inner if isinstance(inner, dict) else (data or {})


def _pct(done: Any, total: Any) -> float | None:
    try:
        total = float(total or 0)
        return None if total <= 0 else max(0.0, min(100.0, float(done or 0) / total * 100))
    except (TypeError, ValueError):
        return None


def _image(client: LlxClient, job_id: str) -> dict:
    d = _unwrap(client.get(f"/api/batch-image/status/{job_id}", include_results="true"))
    done, total = d.get("completed_images") or 0, d.get("total_images") or 0
    files = [f"/api/batch-image/image/{job_id}/{r['image_path'].rsplit('/', 1)[-1]}"
             for r in d.get("results") or [] if r.get("success") and r.get("image_path")]
    return {"kind": "image", "status": _norm(d.get("status")),
            "percent": d.get("progress_percentage", _pct(done, total)),
            "message": d.get("gpu_wait_reason") or f"{done} of {total} image(s)",
            "error": d.get("error"), "files": files, "raw": d}


def _video(client: LlxClient, job_id: str) -> dict:
    d = _unwrap(client.get(f"/api/batch-video/status/{job_id}"))
    done, total = d.get("completed_videos") or 0, d.get("total_videos") or 0
    files = [f"/api/batch-video/video/{job_id}/{r['video_path']}"
             for r in d.get("results") or [] if r.get("success") and r.get("video_path")]
    pct = d.get("progress_pct")
    return {"kind": "video", "status": _norm(d.get("status")),
            "percent": pct if pct is not None else _pct(done, total),
            "message": d.get("stage") or f"{done} of {total} clip(s)",
            "error": d.get("error"), "files": files, "raw": d}


def _bulk(client: LlxClient, job_id: str) -> dict:
    d = _unwrap(client.get(f"/api/bulk-generate/status/{job_id}"))
    p = d.get("progress_status") or {}
    return {"kind": "bulk CSV", "status": _norm(p.get("status")), "percent": p.get("progress"),
            "message": p.get("message") or "", "error": None,
            "files": [d["output_filename"]] if d.get("output_filename") else [], "raw": d}


def _audio(client: LlxClient, job_id: str) -> dict:
    d = _unwrap(client.get(f"/api/audio-foundry/jobs/{job_id}"))
    prog = d.get("progress") or {}
    result = d.get("result") or {}
    path = result.get("path") or result.get("output_path")
    return {"kind": f"audio ({d.get('intent')})" if d.get("intent") else "audio",
            "status": _norm(d.get("status")),
            "percent": 100.0 if _norm(d.get("status")) == "completed" else _pct(prog.get("current"), prog.get("total")),
            "message": prog.get("stage") or "", "error": d.get("error"),
            "files": [path] if path else [], "raw": d}


def _unified(client: LlxClient, job_id: str) -> dict:
    d = client.get(f"/api/jobs/{job_id}")
    return {"kind": d.get("kind", "job"), "status": _norm(d.get("status")), "percent": d.get("progress"),
            "message": d.get("label") or "", "error": d.get("error_message"),
            "files": [], "raw": d}


def read_job(client: LlxClient, job_id: str) -> dict:
    """Return the job's state in one shape. Raises LlxError if no route knows the id."""
    job_id = job_id.strip()
    if job_id.startswith("ImageBatch_") or job_id.startswith("batch_"):
        readers = [_image]
    elif job_id.startswith("VideoBatch_"):
        readers = [_video]
    elif job_id.startswith("bulk_gen_"):
        readers = [_bulk]
    elif ":" in job_id:
        readers = [_unified]
    elif job_id.isdigit():
        readers = [lambda c, i: _unified(c, f"task:{i}")]
    elif _HEX32.match(job_id):
        readers = [_audio, lambda c, i: _unified(c, f"unified:{i}")]
    else:
        readers = [_image, _video, lambda c, i: _unified(c, f"unified:{i}")]

    last: LlxError | None = None
    for reader in readers:
        try:
            info = reader(client, job_id)
        except LlxError as e:
            if e.status_code in (400, 404):
                last = e
                continue
            raise
        info["id"] = job_id
        return info
    raise LlxError(f"No job with id {job_id} (checked the image, video, audio, CSV and task queues)",
                   last.status_code if last else 404)
