"""Clear Completed on the Video page's Batch Queue clears the server's queue
snapshot, for every browser, and leaves every file and record alone.

The generator is built without its worker thread or any service; the route
test stands a bare Flask app up with the blueprint. No backend, GPU,
database or network.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from types import SimpleNamespace

from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.api import batch_video_generation_api as api
from backend.services.batch_video_generator import BatchVideoGenerator


def _status(batch_id, status):
    return SimpleNamespace(
        batch_id=batch_id, status=status, stage=None, current_item=None, progress_pct=None,
        total_videos=1, completed_videos=1 if status == "completed" else 0,
        failed_videos=1 if status == "error" else 0, start_time=datetime.now(), end_time=None,
        metadata={"display_name": batch_id}, error=None,
    )


def _generator(rows, running=None):
    gen = object.__new__(BatchVideoGenerator)
    gen.batch_lock = threading.RLock()
    gen.queue_order = [batch_id for batch_id, _ in rows]
    gen.active_batches = {batch_id: _status(batch_id, status) for batch_id, status in rows}
    gen.cancel_events = {batch_id: threading.Event() for batch_id, _ in rows}
    gen._running_batch_id = running
    return gen


def test_finished_batches_leave_the_queue_and_active_ones_stay():
    gen = _generator([("done", "completed"), ("failed", "error"), ("stopped", "cancelled"),
                      ("waiting", "queued"), ("busy", "running")], running="busy")

    removed = gen.clear_finished_queue()

    assert removed == ["done", "failed", "stopped"]
    assert [q["batch_id"] for q in gen.list_queue()] == ["waiting", "busy"]
    assert set(gen.active_batches) == {"waiting", "busy"}
    # Events stay: the worker consumes them itself when it dequeues a batch.
    assert "stopped" in gen.cancel_events


def test_a_batch_cancelled_while_waiting_is_still_skipped_after_clear():
    # Queue A, queue B, cancel B, Clear Completed: B is still in the worker's
    # input queue and must be skipped when the worker reaches it, not rendered.
    gen = _generator([("A", "queued"), ("B", "queued")])
    gen._save_metadata = lambda status: None
    gen.cancel_events["B"].set()
    gen.active_batches["B"].status = "cancelled"

    assert gen.clear_finished_queue() == ["B"]

    assert gen._skip_if_cancelled("A", gen.active_batches["A"]) is False
    b = _status("B", "cancelled")
    assert gen._skip_if_cancelled("B", b) is True
    assert b.status == "cancelled"
    assert "B" not in gen.cancel_events  # consumed by the worker, not by Clear


def test_a_batch_still_marked_running_is_never_cleared():
    # The worker marks the status before it clears _running_batch_id; the
    # window between the two must not drop the batch it is finishing.
    gen = _generator([("finishing", "completed")], running="finishing")

    assert gen.clear_finished_queue() == []
    assert gen.queue_order == ["finishing"]


def test_clearing_touches_no_file(tmp_path):
    gen = _generator([("done", "completed")])
    gen.base_output_dir = tmp_path
    (tmp_path / "done").mkdir()
    (tmp_path / "done" / "clip.mp4").write_bytes(b"clip")
    (tmp_path / "done" / "batch_metadata.json").write_text("{}")

    gen.clear_finished_queue()

    assert (tmp_path / "done" / "clip.mp4").read_bytes() == b"clip"
    assert (tmp_path / "done" / "batch_metadata.json").exists()


def test_an_id_whose_batch_was_deleted_is_dropped_from_the_order():
    gen = _generator([("gone", "completed"), ("kept", "queued")])
    gen.active_batches.pop("gone")

    assert gen.clear_finished_queue() == []
    assert gen.queue_order == ["kept"]


def test_the_route_clears_and_returns_the_remaining_queue(monkeypatch):
    gen = _generator([("done", "completed"), ("waiting", "queued")])
    monkeypatch.setattr(api, "get_batch_video_generator", lambda: gen)
    app = Flask(__name__)
    app.register_blueprint(api.batch_video_bp)
    client = app.test_client()

    response = client.post("/api/batch-video/queue/clear-finished")

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["removed"] == ["done"]
    assert [q["batch_id"] for q in data["queue"]] == ["waiting"]


def test_the_route_takes_post_only():
    app = Flask(__name__)
    app.register_blueprint(api.batch_video_bp)
    methods = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}
    assert "POST" in methods["/api/batch-video/queue/clear-finished"]
    assert "GET" not in methods["/api/batch-video/queue/clear-finished"]
