"""POST /api/files/image/edit — Save as Copy from the image editor.

The copy path built DBDocument(file_size=..., file_type=...) against a model
whose columns are ``size`` and ``type``, which raised before anything was
saved; the size assignment after the write set an attribute no column backs.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

pytest.importorskip("PIL")
from PIL import Image

from backend.api.files_api import files_bp
from backend.models import Document as DBDocument, Folder, db


@pytest.fixture
def uploads(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    return root


@pytest.fixture
def app(uploads):
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "UPLOAD_FOLDER": str(uploads),
    })
    db.init_app(app)
    app.register_blueprint(files_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def photo(app, uploads):
    (uploads / "Images").mkdir()
    path = uploads / "Images" / "photo.png"
    Image.new("RGB", (40, 20), (200, 30, 30)).save(path)
    folder = Folder(name="Images", path="Images")
    db.session.add(folder)
    db.session.flush()
    doc = DBDocument(filename="photo.png", path="Images/photo.png", type=".png",
                     folder_id=folder.id, size=path.stat().st_size, index_status="STORED")
    db.session.add(doc)
    db.session.commit()
    return doc


def test_save_as_copy_writes_the_file_and_a_row_with_its_real_size(client, photo, uploads):
    resp = client.post("/api/files/image/edit", json={
        "document_id": photo.id,
        "operations": [{"type": "rotate", "angle": 90}],
        "save_mode": "copy",
    })

    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["data"]
    assert data["filename"] == "photo_edited.png"
    assert data["path"] == "Images/photo_edited.png"
    assert (data["width"], data["height"]) == (20, 40)

    written = uploads / "Images" / "photo_edited.png"
    assert written.exists()
    row = db.session.get(DBDocument, data["document_id"])
    assert row.id != photo.id
    assert row.size == written.stat().st_size > 0
    assert data["file_size"] == row.size
    assert row.type == ".png"
    assert row.folder_id == photo.folder_id
    assert row.index_status == "NOT_INDEXED"


def test_a_second_copy_gets_a_numbered_name(client, photo, uploads):
    body = {"document_id": photo.id, "operations": [{"type": "flip", "direction": "horizontal"}],
            "save_mode": "copy"}
    client.post("/api/files/image/edit", json=body)
    resp = client.post("/api/files/image/edit", json=body)

    assert resp.get_json()["data"]["filename"] == "photo_edited_2.png"
    assert (uploads / "Images" / "photo_edited_2.png").exists()


def test_overwrite_with_a_new_format_updates_type_and_size(client, photo, uploads):
    resp = client.post("/api/files/image/edit", json={
        "document_id": photo.id,
        "operations": [{"type": "rotate", "angle": 90}],
        "save_mode": "overwrite",
        "format": "jpeg",
    })

    assert resp.status_code == 200, resp.get_json()
    row = db.session.get(DBDocument, photo.id)
    assert row.filename == "photo.jpg"
    assert row.path == "Images/photo.jpg"
    assert row.type == ".jpg"
    assert row.size == (uploads / "Images" / "photo.jpg").stat().st_size > 0
    assert not (uploads / "Images" / "photo.png").exists()
