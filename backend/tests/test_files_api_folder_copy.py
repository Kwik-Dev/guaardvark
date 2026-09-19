"""POST /api/files/folder/<id>/copy — deep copy of a folder in the File Manager.

The File Manager could copy a document and move a folder, but not copy one.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

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
def tree(app, uploads):
    """Reports/ with a file and a Q1/ subfolder holding another, plus Archive/."""
    (uploads / "Reports" / "Q1").mkdir(parents=True)
    (uploads / "Reports" / "summary.txt").write_text("top")
    (uploads / "Reports" / "Q1" / "january.txt").write_text("nested")
    (uploads / "Archive").mkdir()

    reports = Folder(name="Reports", path="Reports", notes="quarterlies", tags="finance")
    archive = Folder(name="Archive", path="Archive")
    db.session.add_all([reports, archive])
    db.session.flush()
    q1 = Folder(name="Q1", path="Reports/Q1", parent_id=reports.id)
    db.session.add(q1)
    db.session.flush()
    db.session.add_all([
        DBDocument(filename="summary.txt", path="Reports/summary.txt", folder_id=reports.id,
                   type="text", size=3, index_status="INDEXED"),
        DBDocument(filename="january.txt", path="Reports/Q1/january.txt", folder_id=q1.id,
                   type="text", size=6, index_status="INDEXED"),
    ])
    db.session.commit()
    return {"reports": reports.id, "archive": archive.id, "q1": q1.id}


def test_copy_into_another_folder_duplicates_the_whole_tree(client, tree, uploads):
    resp = client.post(f"/api/files/folder/{tree['reports']}/copy",
                       json={"target_folder_id": tree["archive"]})

    assert resp.status_code == 201, resp.get_json()
    new_folder = resp.get_json()["data"]
    assert new_folder["path"] == "Archive/Reports"
    assert new_folder["name"] == "Reports"
    assert new_folder["id"] != tree["reports"]

    assert (uploads / "Archive" / "Reports" / "summary.txt").read_text() == "top"
    assert (uploads / "Archive" / "Reports" / "Q1" / "january.txt").read_text() == "nested"

    paths = {p for (p,) in db.session.query(Folder.path)}
    assert {"Reports", "Reports/Q1", "Archive", "Archive/Reports", "Archive/Reports/Q1"} == paths
    doc_paths = {p for (p,) in db.session.query(DBDocument.path)}
    assert "Archive/Reports/summary.txt" in doc_paths
    assert "Archive/Reports/Q1/january.txt" in doc_paths
    assert "Reports/summary.txt" in doc_paths, "the original is untouched"


def test_copied_documents_are_marked_unindexed(client, tree):
    client.post(f"/api/files/folder/{tree['reports']}/copy",
                json={"target_folder_id": tree["archive"]})

    copied = db.session.query(DBDocument).filter(
        DBDocument.path.like("Archive/%")
    ).all()
    assert copied
    assert all(d.index_status == "NOT_INDEXED" for d in copied)


def test_copy_carries_the_folder_properties(client, tree):
    resp = client.post(f"/api/files/folder/{tree['reports']}/copy",
                       json={"target_folder_id": tree["archive"]})

    new_folder = db.session.get(Folder, resp.get_json()["data"]["id"])
    assert new_folder.notes == "quarterlies"
    assert new_folder.tags == "finance"


def test_null_target_copies_into_the_root_under_a_free_name(client, tree, uploads):
    resp = client.post(f"/api/files/folder/{tree['reports']}/copy",
                       json={"target_folder_id": None})

    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["data"]["path"] == "Reports (Copy)"
    assert resp.get_json()["data"]["parent_id"] is None
    assert (uploads / "Reports (Copy)" / "Q1" / "january.txt").exists()


def test_a_second_copy_gets_its_own_name(client, tree):
    client.post(f"/api/files/folder/{tree['reports']}/copy", json={"target_folder_id": None})
    resp = client.post(f"/api/files/folder/{tree['reports']}/copy", json={"target_folder_id": None})

    assert resp.get_json()["data"]["path"] == "Reports (Copy 2)"


def test_copy_into_itself_is_rejected(client, tree):
    resp = client.post(f"/api/files/folder/{tree['reports']}/copy",
                       json={"target_folder_id": tree["reports"]})

    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_COPY"


def test_copy_into_own_subfolder_is_rejected(client, tree):
    resp = client.post(f"/api/files/folder/{tree['reports']}/copy",
                       json={"target_folder_id": tree["q1"]})

    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_COPY"


def test_unknown_folder_is_404(client, tree):
    resp = client.post("/api/files/folder/9999/copy", json={"target_folder_id": None})

    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "FOLDER_NOT_FOUND"


def test_unknown_target_is_404_and_copies_nothing(client, tree):
    before = db.session.query(Folder).count()
    resp = client.post(f"/api/files/folder/{tree['reports']}/copy",
                       json={"target_folder_id": 9999})

    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "DEST_NOT_FOUND"
    assert db.session.query(Folder).count() == before


def test_document_copy_contract_is_unchanged(client, tree, uploads):
    """Its quirks included: it passes 201 as the message and answers 200."""
    doc_id = db.session.query(DBDocument.id).filter_by(path="Reports/summary.txt").scalar()

    resp = client.post(f"/api/files/document/{doc_id}/copy",
                       json={"destination_path": "Archive"})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["message"] == 201
    data = resp.get_json()["data"]
    assert data["path"] == "Archive/summary.txt"
    assert data["index_status"] == "NOT_INDEXED"
    assert (uploads / "Archive" / "summary.txt").read_text() == "top"
