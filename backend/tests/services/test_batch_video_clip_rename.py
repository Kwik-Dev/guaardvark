"""Renaming a clip repoints everything that serves it.

/status serves the in-memory batch record, and every later _save_metadata
writes that record back to disk, so a rename has to land there as well as in
batch_metadata.json, the thumbnail, the metrics sidecar and the Documents row.
No GPU, network or live database: temp directories and in-memory sqlite.
"""
from __future__ import annotations

import json
import threading

import pytest
from flask import Flask

from backend.models import Document, Folder, db
from backend.services import batch_video_generator as bvg
from backend.services.batch_video_generator import (
    BatchVideoGenerator,
    BatchVideoResult,
    BatchVideoStatus,
    VideoRenameError,
)
from backend.utils.path_guard import PathEscapesRoot

BATCH = "VideoBatch_rename"
CLIP_A = "item_1/videos/clip_a.mp4"
THUMB_A = "item_1/thumbnails/clip_a_thumb.jpg"
CLIP_B = "item_2/videos/clip_b.mp4"


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    monkeypatch.setattr(bvg, "UPLOAD_DIR", str(root))
    batch_dir = root / "Videos" / BATCH
    for rel, data in ((CLIP_A, b"a"), (THUMB_A, b"t"), (CLIP_B, b"b")):
        (batch_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (batch_dir / rel).write_bytes(data)
    (batch_dir / (CLIP_A + ".metrics.json")).write_text('{"quality": {}}')
    return root


def _results():
    return [
        BatchVideoResult(item_id="item_1", success=True, video_path=CLIP_A,
                         frame_paths=[CLIP_A], thumbnail_path=THUMB_A),
        BatchVideoResult(item_id="item_2", success=True, video_path=CLIP_B),
    ]


def _generator(uploads, *, in_memory=True):
    gen = object.__new__(BatchVideoGenerator)
    gen.base_output_dir = uploads / "Videos"
    gen.batch_lock = threading.Lock()
    gen.active_batches = {}
    gen.document_moves = []
    # The Documents row is covered by its own test against sqlite.
    gen._repoint_document = lambda old, new: gen.document_moves.append((old, new))
    batch_dir = gen.base_output_dir / BATCH
    status = BatchVideoStatus(batch_id=BATCH, status="running", total_videos=2,
                              completed_videos=2, results=_results(), output_dir=str(batch_dir),
                              metadata={"display_name": "Sunsets"})
    if in_memory:
        gen.active_batches[BATCH] = status
    gen._save_metadata(status)
    return gen, batch_dir, status


def _disk(batch_dir):
    return json.loads((batch_dir / "batch_metadata.json").read_text())


def test_rename_repoints_the_live_record_the_disk_and_the_files(uploads):
    gen, batch_dir, status = _generator(uploads)

    out = gen.rename_video(BATCH, CLIP_A, "sunset")

    assert out == {
        "video_path": "item_1/videos/sunset.mp4",
        "thumbnail_path": "item_1/thumbnails/sunset_thumb.jpg",
        "old_video_path": CLIP_A,
    }
    assert (batch_dir / "item_1/videos/sunset.mp4").read_bytes() == b"a"
    assert not (batch_dir / CLIP_A).exists()
    assert (batch_dir / "item_1/thumbnails/sunset_thumb.jpg").exists()
    assert (batch_dir / "item_1/videos/sunset.mp4.metrics.json").exists()
    assert not (batch_dir / (CLIP_A + ".metrics.json")).exists()

    live = gen.get_batch_status(BATCH)
    assert live is status
    assert live.results[0].video_path == "item_1/videos/sunset.mp4"
    assert live.results[0].frame_paths == ["item_1/videos/sunset.mp4"]
    assert live.results[0].thumbnail_path == "item_1/thumbnails/sunset_thumb.jpg"
    assert live.results[1].video_path == CLIP_B
    assert _disk(batch_dir)["results"][0]["video_path"] == "item_1/videos/sunset.mp4"
    assert gen.document_moves == [(batch_dir / CLIP_A, batch_dir / "item_1/videos/sunset.mp4")]


def test_a_later_save_of_the_running_batch_keeps_the_new_name(uploads):
    gen, batch_dir, status = _generator(uploads)
    gen.rename_video(BATCH, CLIP_A, "sunset.mp4")

    status.completed_videos = 2
    gen._save_metadata(status)

    assert _disk(batch_dir)["results"][0]["video_path"] == "item_1/videos/sunset.mp4"


def test_a_batch_known_only_from_disk_keeps_its_other_fields(uploads):
    gen, batch_dir, _ = _generator(uploads, in_memory=False)
    data = _disk(batch_dir)
    data["legacy_field"] = "kept"
    (batch_dir / "batch_metadata.json").write_text(json.dumps(data))

    out = gen.rename_video(BATCH, CLIP_A, "dusk")

    on_disk = _disk(batch_dir)
    assert out["video_path"] == "item_1/videos/dusk.mp4"
    assert on_disk["results"][0]["video_path"] == "item_1/videos/dusk.mp4"
    assert on_disk["results"][0]["thumbnail_path"] == "item_1/thumbnails/dusk_thumb.jpg"
    assert on_disk["legacy_field"] == "kept"
    assert gen.get_batch_status(BATCH).results[0].video_path == "item_1/videos/dusk.mp4"


@pytest.mark.parametrize("name, status, text", [
    ("", 400, "Enter a new name"),
    ("   ", 400, "Enter a new name"),
    ("item_1/videos/x.mp4", 400, "without folders"),
    ("..\\x.mp4", 400, "without folders"),
    ("x.webm", 400, "stays .mp4"),
    ("日本", 400, "no usable characters"),
    ("???", 400, "no usable characters"),
])
def test_names_the_server_refuses(uploads, name, status, text):
    gen, batch_dir, live = _generator(uploads)
    with pytest.raises(VideoRenameError) as err:
        gen.rename_video(BATCH, CLIP_A, name)
    assert err.value.status == status and text in str(err.value)
    assert (batch_dir / CLIP_A).exists()
    assert live.results[0].video_path == CLIP_A


def test_a_rename_never_overwrites_another_file(uploads):
    gen, batch_dir, live = _generator(uploads)
    (batch_dir / "item_1/videos/taken.mp4").write_bytes(b"other")
    with pytest.raises(VideoRenameError) as err:
        gen.rename_video(BATCH, CLIP_A, "taken")
    assert err.value.status == 409
    assert (batch_dir / "item_1/videos/taken.mp4").read_bytes() == b"other"
    assert live.results[0].video_path == CLIP_A


def test_missing_and_escaping_paths(uploads):
    gen, _, _ = _generator(uploads)
    with pytest.raises(VideoRenameError) as err:
        gen.rename_video(BATCH, "item_1/videos/nope.mp4", "x")
    assert err.value.status == 404
    with pytest.raises(PathEscapesRoot):
        gen.rename_video(BATCH, "../../outside.mp4", "x")


def test_the_extension_is_added_and_the_name_made_safe(uploads):
    gen, _, _ = _generator(uploads)
    assert gen.rename_video(BATCH, CLIP_A, "Final Cut")["video_path"] == "item_1/videos/Final_Cut.mp4"
    assert gen.rename_video(BATCH, "item_1/videos/Final_Cut.mp4", "Final_Cut.mp4")["video_path"] == \
        "item_1/videos/Final_Cut.mp4"


def test_a_failed_record_save_puts_the_files_back(uploads, monkeypatch):
    gen, batch_dir, _ = _generator(uploads, in_memory=False)
    real_dump = json.dump

    def broken_dump(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(bvg.json, "dump", broken_dump)
    with pytest.raises(OSError):
        gen.rename_video(BATCH, CLIP_A, "sunset")
    monkeypatch.setattr(bvg.json, "dump", real_dump)
    assert (batch_dir / CLIP_A).exists()
    assert (batch_dir / THUMB_A).exists()
    assert not (batch_dir / "item_1/videos/sunset.mp4").exists()
    assert gen.document_moves == []


def test_delete_clears_the_live_record(uploads):
    gen, batch_dir, live = _generator(uploads)
    assert gen.delete_video(BATCH, CLIP_B) is True
    assert not (batch_dir / CLIP_B).exists()
    assert live.results[1].video_path is None
    assert _disk(batch_dir)["results"][1]["video_path"] is None
    assert gen.delete_video(BATCH, CLIP_B) is False


def test_rename_batch_survives_the_next_save(uploads):
    gen, batch_dir, live = _generator(uploads)
    assert gen.rename_batch(BATCH, "Golden hour") is True
    gen._save_metadata(live)
    assert live.metadata["display_name"] == "Golden hour"
    assert _disk(batch_dir)["metadata"]["display_name"] == "Golden hour"


# ── Documents row ────────────────────────────────────────────────────────────

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


def test_the_documents_row_follows_the_clip(app, uploads):
    folder = Folder(name=BATCH, path=f"Videos/{BATCH}")
    db.session.add(folder)
    db.session.flush()
    db.session.add_all([
        Document(filename="clip_a.mp4", path=f"Videos/{BATCH}/{CLIP_A}", folder_id=folder.id),
        Document(filename="sunset.mp4", path=f"Videos/{BATCH}/elsewhere/sunset.mp4", folder_id=folder.id),
    ])
    db.session.commit()
    batch_dir = uploads / "Videos" / BATCH

    BatchVideoGenerator._repoint_document(batch_dir / CLIP_A, batch_dir / "item_1/videos/sunset.mp4")

    doc = Document.query.filter_by(path=f"Videos/{BATCH}/item_1/videos/sunset.mp4").one()
    # Same Documents folder already holds a sunset.mp4, so the row takes the Files-app suffix.
    assert doc.filename == "sunset (2).mp4"
    assert Document.query.filter_by(path=f"Videos/{BATCH}/{CLIP_A}").first() is None


def test_a_clip_outside_uploads_leaves_the_documents_alone(app, uploads, tmp_path):
    db.session.add(Document(filename="x.mp4", path="x.mp4"))
    db.session.commit()
    BatchVideoGenerator._repoint_document(tmp_path / "x.mp4", tmp_path / "y.mp4")
    assert Document.query.one().path == "x.mp4"


# ── Route ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(uploads, monkeypatch):
    from backend.api import batch_video_generation_api as api

    gen, _, _ = _generator(uploads)
    monkeypatch.setattr(api, "get_batch_video_generator", lambda: gen)
    flask_app = Flask(__name__)
    flask_app.register_blueprint(api.batch_video_bp)
    return flask_app.test_client()


def test_the_route_answers_with_the_new_paths_and_url(client):
    resp = client.put(f"/api/batch-video/video/{BATCH}/{CLIP_A}/rename", json={"new_name": "sunset"})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["data"]["video_path"] == "item_1/videos/sunset.mp4"
    assert body["data"]["thumbnail_path"] == "item_1/thumbnails/sunset_thumb.jpg"
    assert body["data"]["old_video_path"] == CLIP_A
    assert body["data"]["url"] == f"/api/batch-video/video/{BATCH}/item_1/videos/sunset.mp4"

    status = client.get(f"/api/batch-video/status/{BATCH}").get_json()
    assert status["data"]["results"][0]["video_path"] == "item_1/videos/sunset.mp4"
    assert client.get(body["data"]["url"]).status_code == 200


def test_the_route_carries_the_refusal_text(client):
    resp = client.put(f"/api/batch-video/video/{BATCH}/{CLIP_A}/rename", json={"new_name": "x.webm"})
    assert resp.status_code == 400
    assert "stays .mp4" in json.dumps(resp.get_json())
