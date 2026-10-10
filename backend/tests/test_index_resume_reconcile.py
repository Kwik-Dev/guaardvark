"""reconcile_stuck_indexing — INDEXING rows that nothing is indexing.

resume_pending_tick works from PENDING and STORED, so a row left INDEXING by a
dead worker, or saved under the old model default by a path that queued
nothing, sat there for good. At startup such a row becomes PENDING when this
pipeline has a loader for it, NOT_INDEXED otherwise.
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


def test_old_rows_go_to_pending_or_not_indexed_by_format(app, monkeypatch):
    monkeypatch.setattr(mod, "_celery_has_live_indexing", lambda: None)
    text = _row("report.txt")
    render = _row("frame_0001.png")
    done = _row("done.txt", status="INDEXED")

    out = mod.reconcile_stuck_indexing()

    assert out == {"pending": 1, "not_indexed": 1}
    assert _status(text) == "PENDING"
    assert _status(render) == "NOT_INDEXED"
    assert _status(done) == "INDEXED"
    assert db.session.get(DBDocument, text).error_message == mod.STUCK_INDEXING_NOTE


def test_a_young_row_is_left_alone_unless_no_worker_is_indexing(app, monkeypatch):
    young = _row("fresh.txt", age=timedelta(minutes=5))

    monkeypatch.setattr(mod, "_celery_has_live_indexing", lambda: True)
    assert mod.reconcile_stuck_indexing() == {"pending": 0, "not_indexed": 0}
    assert _status(young) == "INDEXING"

    monkeypatch.setattr(mod, "_celery_has_live_indexing", lambda: None)
    assert mod.reconcile_stuck_indexing() == {"pending": 0, "not_indexed": 0}
    assert _status(young) == "INDEXING"

    monkeypatch.setattr(mod, "_celery_has_live_indexing", lambda: False)
    assert mod.reconcile_stuck_indexing() == {"pending": 1, "not_indexed": 0}
    assert _status(young) == "PENDING"


def test_a_row_with_a_live_job_is_left_alone(app, monkeypatch):
    monkeypatch.setattr(mod, "_celery_has_live_indexing", lambda: False)
    monkeypatch.setattr(mod, "_job_is_live", lambda job_id: job_id == "job-live")
    running = _row("running.txt", job_id="job-live")
    orphan = _row("orphan.txt", job_id="job-gone")

    out = mod.reconcile_stuck_indexing()

    assert out == {"pending": 1, "not_indexed": 0}
    assert _status(running) == "INDEXING"
    assert _status(orphan) == "PENDING"


def test_probe_can_be_skipped(app, monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "_celery_has_live_indexing", lambda: calls.append(1) or False)
    old = _row("old.txt")

    assert mod.reconcile_stuck_indexing(probe_workers=False) == {"pending": 1, "not_indexed": 0}
    assert calls == []
    assert _status(old) == "PENDING"
