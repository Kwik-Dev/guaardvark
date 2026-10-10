"""Rebuild Index — which rows it re-indexes and how it calls the indexer.

It took every row not INDEXING, media included, so a corpus with generated
images and audio registered as documents ran vision or a missing transcriber
on each and left them PENDING. And it passed db_doc.project_id as
add_file_to_index's third positional argument (progress_callback), so the
first document with a project raised TypeError after the index was reset.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.api import index_mgmt_api as mod
from backend.models import Document as DBDocument, Project, db


@pytest.fixture
def uploads(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    return root


@pytest.fixture
def app(uploads, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_INDEX_RESUME_EXTENSIONS", ".txt,.pdf")
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "UPLOAD_FOLDER": str(uploads),
    })
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _row(filename, status="INDEXED", type_=None, project_id=None):
    doc = DBDocument(filename=filename, path=filename,
                     type=type_ if type_ is not None else os.path.splitext(filename)[1],
                     index_status=status, project_id=project_id)
    db.session.add(doc)
    db.session.commit()
    return doc


def test_candidates_are_text_formats_not_already_queued(app):
    report = _row("report.txt")
    manual = _row("manual.PDF", type_="pdf")  # extension on the path, type without the dot
    by_type = _row("scan", type_=".pdf")
    _row("frame_0001.png")
    _row("voice.wav", status="NOT_INDEXED")
    _row("clip.mp4", status="ERROR")
    _row("queued.txt", status="INDEXING")
    fresh = _row("notes.txt", status="NOT_INDEXED")

    assert [d.id for d in mod._rebuild_candidates()] == [report.id, manual.id, by_type.id, fresh.id]


def test_indexer_is_called_with_path_and_row_only(app, uploads):
    proj = Project(name="P")
    db.session.add(proj)
    db.session.commit()
    (uploads / "report.txt").write_text("x")
    (uploads / "other.txt").write_text("y")
    with_project = _row("report.txt", project_id=proj.id)
    without = _row("other.txt")
    missing = _row("gone.txt")
    calls = []

    def fake_add_file_to_index(file_path, db_document, progress_callback=None):
        calls.append((file_path, db_document.id, progress_callback))
        return db_document.id == with_project.id

    indexed, errors = mod._reindex_documents(
        fake_add_file_to_index, [with_project, without, missing], str(uploads))

    assert (indexed, errors) == (1, 2)
    assert calls == [
        (str(uploads / "report.txt"), with_project.id, None),
        (str(uploads / "other.txt"), without.id, None),
    ]
    assert db.session.get(DBDocument, missing.id).index_status == "ERROR"
