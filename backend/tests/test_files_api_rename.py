"""PUT /api/files/document/<id> and /folder/<id> — rename.

A document rename changed only the display name; the file on disk, its
thumbnail and the registry path kept the old name. And both routes checked
names against the string '<>:"/\\|?*\\x00-\\x1f' character by character, so a
hyphen (or an "x") was an invalid character and "my-notes.txt" was refused.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask
from sqlalchemy.exc import SQLAlchemyError

from backend.api import files_api
from backend.api.files_api import files_bp, has_invalid_name_chars
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
def tree(app, uploads):
    """Reports/ holding notes.txt (with a thumbnail) and other.txt."""
    (uploads / "Reports" / "thumbnails").mkdir(parents=True)
    (uploads / "Reports" / "notes.txt").write_text("top")
    (uploads / "Reports" / "thumbnails" / "notes.jpg").write_bytes(b"jpg")
    (uploads / "Reports" / "other.txt").write_text("other")

    reports = Folder(name="Reports", path="Reports")
    db.session.add(reports)
    db.session.flush()
    notes = DBDocument(filename="notes.txt", path="Reports/notes.txt", folder_id=reports.id,
                       type="text", size=3, index_status="INDEXED")
    other = DBDocument(filename="other.txt", path="Reports/other.txt", folder_id=reports.id,
                       type="text", size=5, index_status="INDEXED")
    db.session.add_all([notes, other])
    db.session.commit()
    return {"reports": reports, "notes": notes, "other": other}


def test_name_check_rejects_separators_and_controls_only():
    assert not has_invalid_name_chars("my-notes_v2 (final).txt")
    assert not has_invalid_name_chars("x-y-z")
    for bad in ('a/b', 'a\\b', 'a:b', 'a*b', 'a?b', 'a"b', 'a<b', 'a>b', 'a|b', 'a\x00b', 'a\tb'):
        assert has_invalid_name_chars(bad), bad


def test_rename_moves_the_file_its_thumbnail_and_the_registry_path(client, tree, uploads):
    r = client.put(f"/api/files/document/{tree['notes'].id}", json={"filename": "my-notes.txt"})

    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["filename"] == "my-notes.txt"
    doc = db.session.get(DBDocument, tree["notes"].id)
    assert doc.path == "Reports/my-notes.txt"
    assert (uploads / "Reports" / "my-notes.txt").read_text() == "top"
    assert not (uploads / "Reports" / "notes.txt").exists()
    assert (uploads / "Reports" / "thumbnails" / "my-notes.jpg").exists()
    assert not (uploads / "Reports" / "thumbnails" / "notes.jpg").exists()


def test_rename_refuses_invalid_characters(client, tree, uploads):
    r = client.put(f"/api/files/document/{tree['notes'].id}", json={"filename": "no?way.txt"})

    assert r.status_code == 400
    assert (uploads / "Reports" / "notes.txt").exists()


def test_rename_refuses_a_name_a_sibling_already_has(client, tree, uploads):
    r = client.put(f"/api/files/document/{tree['notes'].id}", json={"filename": "other.txt"})

    assert r.status_code == 409
    assert (uploads / "Reports" / "notes.txt").exists()
    assert (uploads / "Reports" / "other.txt").read_text() == "other"
    assert db.session.get(DBDocument, tree["notes"].id).path == "Reports/notes.txt"


def test_rename_moves_the_file_back_when_the_commit_fails(client, tree, uploads, monkeypatch):
    def failing_commit():
        raise SQLAlchemyError("disk full")

    monkeypatch.setattr(files_api.db.session.registry(), "commit", failing_commit)

    r = client.put(f"/api/files/document/{tree['notes'].id}", json={"filename": "renamed.txt"})

    assert r.status_code == 500
    assert (uploads / "Reports" / "notes.txt").read_text() == "top"
    assert not (uploads / "Reports" / "renamed.txt").exists()
    assert (uploads / "Reports" / "thumbnails" / "notes.jpg").exists()
    assert not (uploads / "Reports" / "thumbnails" / "renamed.jpg").exists()


def test_folder_rename_accepts_a_hyphen(client, tree, uploads):
    r = client.put(f"/api/files/folder/{tree['reports'].id}", json={"name": "Q1-Reports"})

    assert r.status_code == 200, r.get_json()
    assert (uploads / "Q1-Reports" / "notes.txt").exists()
    assert db.session.get(DBDocument, tree["notes"].id).path == "Q1-Reports/notes.txt"
