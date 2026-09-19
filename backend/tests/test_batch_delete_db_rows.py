"""Deleting one batch removes its database mirrors, and only its own.

Before this, the per-batch delete routes removed the directory and the batch
record and left the Document rows, the Folder row and the job_history rows
behind — the state Settings > Maintenance > "Delete History" was written to
clean up in bulk.
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import Document, Folder, JobHistory, RetentionAudit, db
from backend.services import generation_history_service as svc


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def seeded(app):
    """Two image batches and one video batch, each with folder, docs, history."""
    images_root = Folder(name="Images", path="Images")
    videos_root = Folder(name="Videos", path="Videos")
    db.session.add_all([images_root, videos_root])
    db.session.flush()

    doomed = Folder(name="ImageBatch_x", path="Images/ImageBatch_x", parent_id=images_root.id)
    spared = Folder(name="ImageBatch_z", path="Images/ImageBatch_z", parent_id=images_root.id)
    video = Folder(name="VideoBatch_y", path="Videos/VideoBatch_y", parent_id=videos_root.id)
    db.session.add_all([doomed, spared, video])
    db.session.flush()

    db.session.add_all([
        Document(filename="a.png", path="Images/ImageBatch_x/images/a.png", folder_id=doomed.id),
        Document(filename="b.png", path="Images/ImageBatch_x/images/b.png", folder_id=doomed.id),
        Document(filename="c.png", path="Images/ImageBatch_z/images/c.png", folder_id=spared.id),
        Document(filename="clip.mp4", path="Videos/VideoBatch_y/clip.mp4", folder_id=video.id),
    ])
    now = datetime.now()
    db.session.add_all([
        JobHistory(id="unified:ImageBatch_x", kind="unified", native_id="ImageBatch_x",
                   label="img", status="completed", finished_at=now,
                   job_metadata={"process_type": "image_generation"}),
        JobHistory(id="unified:ImageBatch_z", kind="unified", native_id="ImageBatch_z",
                   label="img", status="completed", finished_at=now,
                   job_metadata={"process_type": "image_generation"}),
        JobHistory(id="video:VideoBatch_y", kind="video_gen", native_id="VideoBatch_y",
                   label="vid", status="completed", finished_at=now),
        JobHistory(id="unified:indexing", kind="unified", native_id="indexing_1",
                   label="index", status="completed", finished_at=now,
                   job_metadata={"process_type": "indexing"}),
    ])
    db.session.commit()
    return app


def _paths():
    return {p for (p,) in db.session.query(Folder.path)}


def _doc_paths():
    return {p for (p,) in db.session.query(Document.path)}


def _history_ids():
    return {i for (i,) in db.session.query(JobHistory.id)}


def test_image_batch_rows_go_and_the_neighbouring_batch_stays(seeded):
    removed = svc.delete_batch_history_rows(image_batch_ids=["ImageBatch_x"])

    assert removed == {"documents": 2, "folders": 1, "job_history": 1}
    assert "Images/ImageBatch_x" not in _paths()
    assert "Images/ImageBatch_z" in _paths()
    assert _doc_paths() == {
        "Images/ImageBatch_z/images/c.png",
        "Videos/VideoBatch_y/clip.mp4",
    }
    assert _history_ids() == {"unified:ImageBatch_z", "video:VideoBatch_y", "unified:indexing"}


def test_video_batch_rows_go(seeded):
    removed = svc.delete_batch_history_rows(video_batch_ids=["VideoBatch_y"])

    assert removed == {"documents": 1, "folders": 1, "job_history": 1}
    assert "Videos/VideoBatch_y" not in _paths()
    assert "video:VideoBatch_y" not in _history_ids()
    assert "Videos" in _paths(), "the root folder is never a target"


def test_deletion_is_recorded_in_the_retention_audit(seeded):
    svc.delete_batch_history_rows(image_batch_ids=["ImageBatch_x"], triggered_by="batch_image_delete")

    rows = db.session.query(RetentionAudit).all()
    assert len(rows) == 1
    assert rows[0].kind == "generation_history_batch"
    assert rows[0].operation == "manual_delete"
    assert rows[0].triggered_by == "batch_image_delete"


def test_unknown_batch_removes_nothing(seeded):
    removed = svc.delete_batch_history_rows(image_batch_ids=["ImageBatch_nope"])

    assert removed == {"documents": 0, "folders": 0, "job_history": 0}
    assert len(_paths()) == 5
    assert db.session.query(RetentionAudit).count() == 0


def test_image_delete_route_clears_the_rows(seeded, tmp_path, monkeypatch):
    from backend.api import batch_image_generation_api as api

    batch_dir = tmp_path / "Images" / "ImageBatch_x"
    batch_dir.mkdir(parents=True)
    (batch_dir / "batch_metadata.json").write_text("{}")
    forgotten = []
    generator = SimpleNamespace(
        _ACTIVE_STATUSES=frozenset({"running", "queued"}),
        forget_batch=forgotten.append,
    )
    monkeypatch.setattr(api, "service_available", True)
    monkeypatch.setattr(api, "get_batch_image_generator", lambda: generator)
    monkeypatch.setattr(
        api, "_load_batch_status",
        lambda gen, bid: SimpleNamespace(status="completed", output_dir=str(batch_dir)),
    )

    with seeded.test_request_context():
        response = api.delete_batch("ImageBatch_x")
    payload = (response[0] if isinstance(response, tuple) else response).get_json()

    assert payload["success"] is True
    assert payload["data"]["deleted"] == {"documents": 2, "folders": 1, "job_history": 1}
    assert forgotten == ["ImageBatch_x"]
    assert not batch_dir.exists()
    assert "Images/ImageBatch_x" not in _paths()


def test_video_generator_delete_clears_the_rows(seeded, tmp_path):
    from backend.services.batch_video_generator import BatchVideoGenerator

    batch_dir = tmp_path / "Videos" / "VideoBatch_y"
    batch_dir.mkdir(parents=True)
    (batch_dir / "clip.mp4").write_bytes(b"y")

    generator = object.__new__(BatchVideoGenerator)
    generator.batch_lock = threading.Lock()
    generator.active_batches = {"VideoBatch_y": object()}
    generator._get_batch_dir = lambda bid: batch_dir

    assert generator.delete_batch("VideoBatch_y") is True
    assert not batch_dir.exists()
    assert generator.active_batches == {}
    assert "Videos/VideoBatch_y" not in _paths()
    assert "video:VideoBatch_y" not in _history_ids()


def test_video_generator_delete_of_missing_dir_is_false(seeded, tmp_path):
    from backend.services.batch_video_generator import BatchVideoGenerator

    generator = object.__new__(BatchVideoGenerator)
    generator.batch_lock = threading.Lock()
    generator.active_batches = {}
    generator._get_batch_dir = lambda bid: tmp_path / "gone"

    assert generator.delete_batch("VideoBatch_y") is False
    assert "Videos/VideoBatch_y" in _paths()
