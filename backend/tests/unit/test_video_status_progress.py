"""get_generation_status shows a video batch's stage and progress while it runs.

Both readers (the backend's in-process one and the MCP server's HTTP one) carry
the batch's stage and percentage, and wait_seconds waits on a running batch
even when an earlier reader, for another kind of job, failed. No GPU, network
or database: the HTTP helper and the generator module are stand-ins.
"""
from __future__ import annotations

import sys
import types

import pytest

from backend.tools import image_tools as it
from backend.utils.backend_http import BackendError

CLIP = {"success": True, "video_path": "clip.mp4"}


def _video(status, stage, pct, done=0, results=()):
    return {"status": status, "stage": stage, "progress_pct": pct, "current_item": "item_1",
            "total_videos": 1, "completed_videos": done, "results": list(results)}


@pytest.fixture
def backend(monkeypatch):
    """The MCP server's view: only the video route knows the batch."""
    state = {"video": None, "image_error": BackendError("http", "Batch not found", status=404), "polls": 0}

    def fake_http(method, path, payload=None, timeout=None):
        if path.startswith("/api/batch-video/status/"):
            state["polls"] += 1
            answer = state["video"]
            return answer(state["polls"]) if callable(answer) else answer
        if path.startswith("/api/batch-image/status/"):
            raise state["image_error"]
        raise BackendError("http", "not found", status=404)

    monkeypatch.setattr(it, "_http_json", fake_http)
    monkeypatch.setattr(it, "STATUS_POLL_S", 0)
    monkeypatch.setattr(it.time, "sleep", lambda s: None)
    tool = it.GenerationStatusTool()
    tool.set_context({"transport": "mcp"})
    state["tool"] = tool
    return state


def test_a_running_batch_shows_its_stage_and_percentage(backend):
    backend["video"] = _video("running", "generate", 1)
    res = backend["tool"].execute(batch_id="VideoBatch_1")
    assert res.success and "Progress: 1% — stage: generate" in res.output
    assert res.metadata["stage"] == "generate" and res.metadata["progress"] == 1
    assert res.metadata["current_item"] == "item_1"


def test_a_batch_waiting_for_the_gpu_shows_that_stage(backend):
    backend["video"] = _video("pending", "gpu_wait", 0)
    res = backend["tool"].execute(batch_id="VideoBatch_1")
    assert res.success and "Stage: gpu_wait" in res.output and "Progress:" not in res.output


def test_a_finished_batch_shows_neither(backend):
    backend["video"] = _video("completed", "done", 100, 1, [CLIP])
    res = backend["tool"].execute(batch_id="VideoBatch_1")
    assert res.success and "Stage:" not in res.output and "Progress:" not in res.output
    assert "File: /api/batch-video/video/VideoBatch_1/clip.mp4" in res.output


def test_wait_seconds_waits_although_the_image_route_failed(backend):
    backend["image_error"] = BackendError("plugin_offline", "Batch image generation service not available",
                                          status=503)
    backend["video"] = lambda n: _video("running", "generate", 1) if n < 3 else _video(
        "completed", "done", 100, 1, [CLIP])
    res = backend["tool"].execute(batch_id="VideoBatch_1", wait_seconds=50)
    assert res.success and backend["polls"] == 3 and "completed" in res.output

    # An id no route knows is still reported as unread, not as missing.
    backend["video"] = None
    res = backend["tool"].execute(batch_id="VideoBatch_1")
    assert not res.success and "did not answer" in res.error


def test_the_in_process_reader_carries_stage_and_percentage(monkeypatch):
    class _Status:
        status, stage, progress_pct, current_item = "running", "post", 1, "item_1"
        total_videos, error, results = 1, None, []

    module = types.ModuleType("backend.services.batch_video_generator")
    module.get_batch_video_generator = lambda: types.SimpleNamespace(get_batch_status=lambda batch_id: _Status())
    monkeypatch.setitem(sys.modules, "backend.services.batch_video_generator", module)
    monkeypatch.setattr(it, "batch_failure", lambda status: None)
    info = it.GenerationStatusTool._video_status("VideoBatch_1")
    assert info["stage"] == "post" and info["progress"] == 1 and info["current_item"] == "item_1"


def test_the_description_says_what_progress_a_video_batch_reports():
    assert "pipeline stage" in it.GenerationStatusTool.description
