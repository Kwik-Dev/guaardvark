"""PUT /api/files/document/<id> and /folder/<id> — rename.

A document rename changes the shown name only; the file on disk and
document.path keep theirs, since chunk metadata, batch thumbnails and
batch_metadata.json are keyed by that path. Both routes used to check names
against the string '<>:"/\\|?*\\x00-\\x1f' character by character, so a hyphen
(or an "x") was an invalid character and "my-notes.txt" was refused.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.api.files_api import document_name_problem, files_bp, has_invalid_name_chars
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
    """Reports/ holding notes.txt and other.txt."""
    (uploads / "Reports").mkdir(parents=True)
    (uploads / "Reports" / "notes.txt").write_text("top")
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


def test_character_check_rejects_separators_and_controls_only():
    assert not has_invalid_name_chars("my-notes_v2 (final).txt")
    assert not has_invalid_name_chars("x-y-z")
    for bad in ('a/b', 'a\\b', 'a:b', 'a*b', 'a?b', 'a"b', 'a<b', 'a>b', 'a|b', 'a\x00b', 'a\tb'):
        assert has_invalid_name_chars(bad), bad


@pytest.mark.parametrize("name", [
    "my-notes.txt", "x-y-z", "Report (final) v2.pdf", "console.log", "nul-report.txt", "a.b.c",
])
def test_document_name_check_accepts_ordinary_names(name):
    assert document_name_problem(name) is None


@pytest.mark.parametrize("name, words", [
    ("", "Invalid filename"),
    (".", '"." or ".."'),
    ("..", '"." or ".."'),
    (".hidden", "start with a dot"),
    ("a/b.txt", "cannot contain any of"),
    ("a\x7fb.txt", "control characters"),
    ("a\x01b.txt", "control characters"),
    ("notes.", "end with a dot or a space"),
    ("notes ", "end with a dot or a space"),
    ("CON", "reserved device name"),
    ("con.txt", "reserved device name"),
    ("Com9.log", "reserved device name"),
    ("lpt1", "reserved device name"),
    ("NUL.tar.gz", "reserved device name"),
    ("x" * 256, "too long"),
])
def test_document_name_check_refuses_with_a_reason(name, words):
    problem = document_name_problem(name)
    assert problem and words in problem, (name, problem)


def test_rename_changes_the_shown_name_only(client, tree, uploads):
    r = client.put(f"/api/files/document/{tree['notes'].id}", json={"filename": "my-notes.txt"})

    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["filename"] == "my-notes.txt"
    doc = db.session.get(DBDocument, tree["notes"].id)
    assert doc.filename == "my-notes.txt"
    assert doc.path == "Reports/notes.txt"
    assert (uploads / "Reports" / "notes.txt").read_text() == "top"
    assert not (uploads / "Reports" / "my-notes.txt").exists()


@pytest.mark.parametrize("name, words", [
    ("no?way.txt", "cannot contain any of"),
    ("CON.txt", "reserved device name"),
    (".notes", "start with a dot"),
    ("notes.", "end with a dot or a space"),
])
def test_rename_refuses_bad_names_with_a_message(client, tree, name, words):
    r = client.put(f"/api/files/document/{tree['notes'].id}", json={"filename": name})

    assert r.status_code == 400, r.get_json()
    assert words in r.get_json()["message"]
    assert db.session.get(DBDocument, tree["notes"].id).filename == "notes.txt"


def test_rename_refuses_a_name_a_sibling_already_has(client, tree):
    r = client.put(f"/api/files/document/{tree['notes'].id}", json={"filename": "other.txt"})

    assert r.status_code == 409
    assert db.session.get(DBDocument, tree["notes"].id).filename == "notes.txt"


def test_folder_rename_accepts_a_hyphen_and_moves_the_directory(client, tree, uploads):
    r = client.put(f"/api/files/folder/{tree['reports'].id}", json={"name": "Q1-Reports"})

    assert r.status_code == 200, r.get_json()
    assert (uploads / "Q1-Reports" / "notes.txt").exists()
    assert db.session.get(DBDocument, tree["notes"].id).path == "Q1-Reports/notes.txt"
