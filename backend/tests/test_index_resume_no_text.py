"""resume_pending_tick on a file from which no text can be read.

add_file_to_index returns falsy both when the environment fails (embedding
service, index not loaded) and when the file holds nothing to index; only the
second records no_content_reason. The first leaves the row PENDING for a later
tick; the second is about the file, so the row ends ERROR with the reason, as
it does from the upload path.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend import config
from backend.models import Document as DBDocument, db
from backend.services import indexing_service as isvc
from backend.tasks import index_resume_tasks as mod


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_INDEX_RESUME_EXTENSIONS", ".txt")
    monkeypatch.setenv("GUAARDVARK_INDEX_AUTO_RESUME", "true")
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    # The tick builds its own app context from backend.app.
    monkeypatch.setitem(sys.modules, "backend.app",
                        types.SimpleNamespace(get_or_create_app=lambda: app))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(isvc, "is_indexing_paused", lambda: False)
    monkeypatch.setattr(isvc, "_under_resource_pressure", lambda: False)
    monkeypatch.setattr(mod, "_embedding_service_reachable", lambda: True)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _pending(tmp_path, name, data=b"\x00\x01binary"):
    (tmp_path / name).write_bytes(data)
    doc = DBDocument(filename=name, path=name, type=".txt", index_status="PENDING")
    db.session.add(doc)
    db.session.commit()
    return doc.id


def _row(doc_id):
    db.session.expire_all()
    return db.session.get(DBDocument, doc_id)


def test_a_file_with_no_text_ends_error_with_its_reason(app, tmp_path, monkeypatch):
    doc_id = _pending(tmp_path, "blob.txt")

    def no_text(path, doc):
        isvc._NO_CONTENT_REASONS[str(doc.id)] = "nothing to index: no text could be read from blob.txt"
        return False
    monkeypatch.setattr(isvc, "add_file_to_index", no_text)

    result = mod.resume_pending_tick()

    row = _row(doc_id)
    assert row.index_status == "ERROR"
    assert row.error_message == "nothing to index: no text could be read from blob.txt"
    assert result["no_text"] == 1 and result["failed"] == 0


def test_an_environment_failure_still_leaves_the_row_pending(app, tmp_path, monkeypatch):
    doc_id = _pending(tmp_path, "notes.txt", b"hello")
    isvc._NO_CONTENT_REASONS.pop(str(doc_id), None)
    monkeypatch.setattr(isvc, "add_file_to_index", lambda path, doc: False)

    result = mod.resume_pending_tick()

    row = _row(doc_id)
    assert row.index_status == "PENDING"
    assert row.error_message == "add_file_to_index returned falsy"
    assert result["failed"] == 1 and result["no_text"] == 0
