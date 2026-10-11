"""reconcile_stuck_indexing — INDEXING rows that nothing is indexing.

resume_pending_tick works from PENDING and STORED, so a row left INDEXING by a
dead worker, or saved under the old model default by a path that queued
nothing, sat there for good. At startup a row older than the bound becomes
PENDING when this pipeline has a loader for it, NOT_INDEXED otherwise. Age is
the only signal: Bulk Index marks rows INDEXING before queuing, with no job
id, and a queued task is invisible to any worker probe.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import Document as DBDocument, db
from backend.tasks import index_resume_tasks as mod


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_INDEX_RESUME_EXTENSIONS", ".txt,.pdf")
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _row(filename, status="INDEXING", age=timedelta(days=3), job_id=None):
    doc = DBDocument(filename=filename, path=filename, type=os.path.splitext(filename)[1],
                     index_status=status, indexing_job_id=job_id)
    db.session.add(doc)
    db.session.flush()
    doc.updated_at = datetime.now() - age
    db.session.commit()
    return doc.id


def _status(doc_id):
    db.session.expire_all()
    return db.session.get(DBDocument, doc_id).index_status


def test_old_rows_go_to_pending_or_not_indexed_by_format(app):
    text = _row("report.txt")
    render = _row("frame_0001.png")
    done = _row("done.txt", status="INDEXED")

    out = mod.reconcile_stuck_indexing()

    assert out == {"pending": 1, "not_indexed": 1}
    assert _status(text) == "PENDING"
    assert _status(render) == "NOT_INDEXED"
    assert _status(done) == "INDEXED"
    assert db.session.get(DBDocument, text).error_message == mod.STUCK_INDEXING_NOTE


def test_a_row_younger_than_the_bound_is_left_alone(app):
    assert mod.STUCK_INDEXING_AFTER >= timedelta(hours=6)
    young = _row("fresh.txt", age=timedelta(hours=5))
    queued = _row("queued.png", age=timedelta(minutes=1))

    assert mod.reconcile_stuck_indexing() == {"pending": 0, "not_indexed": 0}
    assert _status(young) == "INDEXING"
    assert _status(queued) == "INDEXING"


def test_a_row_with_a_live_job_is_left_alone(app, monkeypatch):
    monkeypatch.setattr(mod, "_job_is_live", lambda job_id: job_id == "job-live")
    running = _row("running.txt", job_id="job-live")
    orphan = _row("orphan.txt", job_id="job-gone")

    out = mod.reconcile_stuck_indexing()

    assert out == {"pending": 1, "not_indexed": 0}
    assert _status(running) == "INDEXING"
    assert _status(orphan) == "PENDING"


def test_uploaded_at_stands_in_when_updated_at_is_missing(app):
    doc = DBDocument(filename="old.txt", path="old.txt", type=".txt", index_status="INDEXING")
    db.session.add(doc)
    db.session.flush()
    doc.updated_at = None
    doc.uploaded_at = datetime.now() - timedelta(days=2)
    db.session.commit()

    assert mod.reconcile_stuck_indexing() == {"pending": 1, "not_indexed": 0}
    assert _status(doc.id) == "PENDING"
