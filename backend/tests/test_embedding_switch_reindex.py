"""Changing the embedding model clears the old vectors and indexes again.

A switch to a model of the same width (embeddinggemma to embeddinggemma-2,
both 768) kept the index, so the new model searched the old model's vectors.
Search then switched itself off on the model mismatch and nothing re-indexed.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.api import indexing_api
from backend.models import Document as DBDocument, db
from backend.services import indexing_service


class _Cursor:
    def __init__(self, tables, ran):
        self._tables, self._ran = tables, ran

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._ran.append(sql)

    def fetchall(self):
        return [(t,) for t in self._tables]


class _Conn:
    autocommit = False

    def __init__(self, tables, ran):
        self._tables, self._ran = tables, ran

    def cursor(self):
        return _Cursor(self._tables, self._ran)

    def close(self):
        pass


@pytest.fixture
def pg(monkeypatch):
    tables = ["data_guaardvark_global_768", "data_guaardvark_global_2560",
              "data_guaardvark_research_global_768"]
    ran = []
    monkeypatch.setattr(indexing_service, "_vector_backend", lambda: "pgvector")
    monkeypatch.setattr(indexing_service, "_pg_connect", lambda: _Conn(tables, ran))
    return ran


def test_tables_of_the_width_are_emptied_not_dropped(pg):
    result = indexing_service.empty_vector_tables(768)
    assert result["emptied"] == ["data_guaardvark_global_768", "data_guaardvark_research_global_768"]
    assert [s for s in pg if s.startswith(("TRUNCATE", "DROP"))] == [
        'TRUNCATE TABLE "data_guaardvark_global_768"',
        'TRUNCATE TABLE "data_guaardvark_research_global_768"',
    ]


def test_a_reset_drops_other_widths_and_keeps_the_active_one(pg):
    result = indexing_service.drop_all_vector_stores(keep_width=768)
    assert result["dropped"] == ["data_guaardvark_global_2560"]


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


def _doc(name, status):
    doc = DBDocument(filename=name, path=name, type=os.path.splitext(name)[1], index_status=status)
    db.session.add(doc)
    db.session.commit()
    return doc


@pytest.fixture
def dispatch(monkeypatch):
    sent = []
    monkeypatch.setitem(sys.modules, "backend.celery_tasks_isolated",
                        types.SimpleNamespace(index_document_task=object()))
    monkeypatch.setattr(indexing_api, "get_unified_progress", lambda: object())
    monkeypatch.setattr(indexing_api, "_get_indexing_paused_setting", lambda: False)

    def fake_dispatch(progress, task, doc):
        sent.append(doc.id)
        return None

    monkeypatch.setattr(indexing_api, "_dispatch_document", fake_dispatch)
    return sent


def test_indexed_documents_are_queued_again(app, dispatch):
    a = _doc("a.md", "INDEXED")
    b = _doc("b.txt", "INDEXED")
    media = _doc("frame.png", "STORED")
    backlog = _doc("c.py", "PENDING")

    assert indexing_api.requeue_indexed_documents() == {"queued": 2, "pending": 0}
    assert dispatch == [a.id, b.id]
    assert db.session.get(DBDocument, a.id).index_status == "INDEXING"
    assert db.session.get(DBDocument, media.id).index_status == "STORED"
    assert db.session.get(DBDocument, backlog.id).index_status == "PENDING"


def test_while_paused_they_are_left_pending(app, dispatch, monkeypatch):
    monkeypatch.setattr(indexing_api, "_get_indexing_paused_setting", lambda: True)
    a = _doc("a.md", "INDEXED")

    assert indexing_api.requeue_indexed_documents() == {"queued": 0, "pending": 1}
    assert dispatch == []
    assert db.session.get(DBDocument, a.id).index_status == "PENDING"


def test_a_refused_task_stops_the_queueing(app, dispatch, monkeypatch):
    a = _doc("a.md", "INDEXED")
    b = _doc("b.md", "INDEXED")
    monkeypatch.setattr(indexing_api, "_dispatch_document", lambda p, t, doc: RuntimeError("redis down"))

    assert indexing_api.requeue_indexed_documents() == {"queued": 0, "pending": 2}
    assert db.session.get(DBDocument, a.id).index_status == "ERROR"
    assert db.session.get(DBDocument, a.id).error_message == "redis down"
    assert db.session.get(DBDocument, b.id).index_status == "PENDING"
