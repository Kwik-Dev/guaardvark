"""Batch-image API: form-data booleans, disk-resolved downloads, and the
image delete/rename routes keeping thumbnails and in-memory status in step.

Everything runs against a Flask test client with a stub generator; no server
process is started.
"""
from __future__ import annotations

import io
import json
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

import backend.api.batch_image_generation_api as m


# ---- _as_bool --------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,default,expected",
    [
        (None, True, True),
        (None, False, False),
        (True, False, True),
        (False, True, False),
        ("false", True, False),
        ("False", True, False),
        ("0", True, False),
        ("off", True, False),
        ("", True, False),
        ("true", False, True),
        ("1", False, True),
        (1, False, True),
        (0, True, False),
    ],
)
def test_as_bool_handles_form_strings(raw, default, expected):
    assert m._as_bool(raw, default) is expected


def test_parse_generation_params_respects_form_false():
    form = {
        "preserve_order": "false",
        "generate_thumbnails": "false",
        "save_metadata": "false",
        "auto_enhance": "false",
        "enhance_faces": "false",
        "restore_faces": "true",
        "remove_background": "false",
        "content_preset": "",
    }
    params, _ = m._parse_generation_params(form)
    assert params["preserve_order"] is False
    assert params["generate_thumbnails"] is False
    assert params["save_metadata"] is False
    assert params["auto_enhance"] is False
    assert params["enhance_faces"] is False
    assert params["restore_faces"] is True
    assert params["remove_background"] is False
    assert params["content_preset"] is None


# ---- small helpers ---------------------------------------------------------
def test_thumbnail_candidates_prefers_generator_jpg():
    assert m._thumbnail_candidates("shot.png") == ["shot.jpg", "shot.png"]
    assert m._thumbnail_candidates("shot.jpg") == ["shot.jpg"]


def test_progress_percentage_counts_failures_as_finished():
    st = SimpleNamespace(total_images=4, completed_images=3, failed_images=1)
    assert m._progress_percentage(st) == 100
    st = SimpleNamespace(total_images=0, completed_images=0, failed_images=0)
    assert m._progress_percentage(st) == 0


# ---- route fixture ---------------------------------------------------------
class _Result:
    def __init__(self, image_path, thumbnail_path=None):
        self.prompt_id = "p1"
        self.success = True
        self.image_path = image_path
        self.thumbnail_path = thumbnail_path
        self.generation_time = 0.1
        self.error = None
        self.metadata = {}


class _StubGenerator:
    _ACTIVE_STATUSES = frozenset({"queued", "pending", "running", "processing"})

    def __init__(self, status):
        self.status = status
        self.batch_lock = threading.Lock()
        self.active_batches = {status.batch_id: status}
        self.forgotten = []

    def find_batch_status(self, batch_id, include_results=False):
        return self.status if batch_id == self.status.batch_id else None

    def get_batch_status(self, batch_id):
        return self.active_batches.get(batch_id)

    def forget_batch(self, batch_id):
        self.forgotten.append(batch_id)
        self.active_batches.pop(batch_id, None)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    batch_dir = tmp_path / "ImageBatch_01"
    (batch_dir / "images").mkdir(parents=True)
    (batch_dir / "thumbnails").mkdir()
    (batch_dir / "images" / "shot.png").write_bytes(b"\x89PNG")
    (batch_dir / "thumbnails" / "shot.jpg").write_bytes(b"\xff\xd8")
    (batch_dir / "images" / "other.png").write_bytes(b"\x89PNG")
    (batch_dir / "thumbnails" / "other.jpg").write_bytes(b"\xff\xd8")

    results = [
        {"prompt_id": "p1", "success": True,
         "image_path": str(batch_dir / "images" / "shot.png"),
         "thumbnail_path": str(batch_dir / "thumbnails" / "shot.jpg")},
        {"prompt_id": "p2", "success": True,
         "image_path": str(batch_dir / "images" / "other.png"),
         "thumbnail_path": str(batch_dir / "thumbnails" / "other.jpg")},
    ]
    (batch_dir / "batch_metadata.json").write_text(json.dumps({
        "batch_id": "ImageBatch_01",
        "status": "completed",
        "total_images": 2,
        "completed_images": 2,
        "failed_images": 0,
        "results": results,
    }))

    status = SimpleNamespace(
        batch_id="ImageBatch_01",
        status="completed",
        output_dir=str(batch_dir),
        total_images=2,
        completed_images=2,
        failed_images=0,
        display_name=None,
        results=[
            _Result(results[0]["image_path"], results[0]["thumbnail_path"]),
            _Result(results[1]["image_path"], results[1]["thumbnail_path"]),
        ],
    )
    gen = _StubGenerator(status)

    monkeypatch.setattr(m, "service_available", True, raising=False)
    monkeypatch.setattr(m, "get_batch_image_generator", lambda: gen)

    app = Flask(__name__)
    app.register_blueprint(m.batch_image_bp)
    return app.test_client(), batch_dir, gen


def test_delete_image_removes_jpg_thumbnail_and_syncs_memory(batch):
    c, batch_dir, gen = batch
    resp = c.delete("/api/batch-image/image/ImageBatch_01/shot.png")
    assert resp.status_code == 200, resp.data
    assert not (batch_dir / "images" / "shot.png").exists()
    assert not (batch_dir / "thumbnails" / "shot.jpg").exists()
    assert (batch_dir / "thumbnails" / "other.jpg").exists()

    meta = json.loads((batch_dir / "batch_metadata.json").read_text())
    assert [Path(r["image_path"]).name for r in meta["results"]] == ["other.png"]
    assert meta["completed_images"] == 1

    st = gen.active_batches["ImageBatch_01"]
    assert [Path(r.image_path).name for r in st.results] == ["other.png"]
    assert st.completed_images == 1


def test_rename_image_renames_jpg_thumbnail_and_syncs_memory(batch):
    c, batch_dir, gen = batch
    resp = c.put("/api/batch-image/image/ImageBatch_01/shot.png/rename", json={"new_name": "hero"})
    assert resp.status_code == 200, resp.data
    assert (batch_dir / "images" / "hero.png").exists()
    assert (batch_dir / "thumbnails" / "hero.jpg").exists()
    assert not (batch_dir / "thumbnails" / "shot.jpg").exists()

    meta = json.loads((batch_dir / "batch_metadata.json").read_text())
    renamed = next(r for r in meta["results"] if r["prompt_id"] == "p1")
    assert Path(renamed["image_path"]).name == "hero.png"
    assert Path(renamed["thumbnail_path"]).name == "hero.jpg"

    st = gen.active_batches["ImageBatch_01"]
    assert Path(st.results[0].image_path).name == "hero.png"
    assert Path(st.results[0].thumbnail_path).name == "hero.jpg"


def test_rename_batch_updates_in_memory_display_name(batch):
    c, batch_dir, gen = batch
    resp = c.put("/api/batch-image/rename/ImageBatch_01", json={"name": "Sunsets"})
    assert resp.status_code == 200, resp.data
    assert gen.active_batches["ImageBatch_01"].display_name == "Sunsets"
    meta = json.loads((batch_dir / "batch_metadata.json").read_text())
    assert meta["display_name"] == "Sunsets"


def test_download_zips_completed_batch_resolved_from_disk(batch):
    c, batch_dir, gen = batch
    # Nothing in memory: the batch predates this process.
    gen.active_batches.clear()
    resp = c.get("/api/batch-image/download/ImageBatch_01")
    assert resp.status_code == 200, resp.data
    names = set(zipfile.ZipFile(io.BytesIO(resp.data)).namelist())
    assert {"images/shot.png", "thumbnails/shot.jpg", "batch_metadata.json"} <= names


def test_delete_batch_refuses_queued(batch):
    c, batch_dir, gen = batch
    gen.status.status = "queued"
    resp = c.delete("/api/batch-image/delete/ImageBatch_01")
    assert resp.status_code == 409
    assert batch_dir.exists()
    assert gen.forgotten == []


def test_delete_batch_removes_dir_and_forgets(batch):
    c, batch_dir, gen = batch
    resp = c.delete("/api/batch-image/delete/ImageBatch_01")
    assert resp.status_code == 200, resp.data
    assert not batch_dir.exists()
    assert gen.forgotten == ["ImageBatch_01"]


def test_template_is_served_inline(batch):
    c, _, _ = batch
    resp = c.get("/api/batch-image/template")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert resp.data.startswith(b"prompt,negative_prompt,style,width,height,steps,guidance,seed")
