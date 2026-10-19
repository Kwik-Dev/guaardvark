"""Counts come from the vector table: chunks apart from RAPTOR summaries, the
documents that have rows, and per document the chunks actually stored.

The SQL itself runs against PostgreSQL in
backend/tests/integration/test_vector_table_pg.py; here the connection is a
stand-in that answers what the database would, so the shaping is checked."""
from types import SimpleNamespace

import pytest
from flask import Flask

from backend.services import indexing_service as isvc


class _Conn:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.sql = []

    def cursor(self):
        conn = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                conn.sql.append((sql, params))

            def fetchone(self):
                return conn.answers.pop(0)

        return Cur()

    def close(self):
        pass


@pytest.fixture
def pg(monkeypatch):
    monkeypatch.setattr(isvc, "_vector_backend", lambda: "pgvector")
    monkeypatch.setattr(isvc, "_pg_table_name", lambda project_id=None, profile=None: "t_768")
    monkeypatch.setattr(isvc, "_active_embed_dim", lambda: 768)

    def use(conn):
        monkeypatch.setattr(isvc, "_pg_connect", lambda: conn)
        return conn
    return use


def test_stats_separate_summaries_from_chunks(pg):
    pg(_Conn(("public.data_t_768",), (12, 10, 2, 3), (4096,)))
    stats = isvc.vector_store_stats()
    assert (stats["rows"], stats["chunks"], stats["summaries"], stats["documents"]) == (12, 10, 2, 3)
    assert stats["size_bytes"] == 4096


def test_a_documents_chunks_are_counted_not_estimated(pg):
    conn = pg(_Conn((7, 812.4)))
    assert isvc.document_vector_stats(42) == {"chunks": 7, "avg_chars": 812}
    sql, params = conn.sql[0]
    assert params == ("doc\\_42\\_%",) and "raptor_summary" in sql


def test_without_pgvector_there_is_no_count(monkeypatch):
    monkeypatch.setattr(isvc, "_vector_backend", lambda: "simple")
    assert isvc.document_vector_stats(42) is None


def test_index_info_counts_the_vector_table(monkeypatch, tmp_path):
    from backend.api import index_mgmt_api as im

    monkeypatch.setattr(im, "llama_index_available", True)
    monkeypatch.setattr(isvc, "vector_store_stats", lambda *a, **k: {
        "backend": "pgvector", "exists": True, "rows": 12, "chunks": 10, "summaries": 2, "documents": 3})
    app = Flask(__name__)
    app.config["STORAGE_DIR"] = str(tmp_path)  # no docstore.json, as under pgvector
    app.register_blueprint(im.index_mgmt_bp)
    body = app.test_client().get("/api/meta/index-info").get_json()
    assert (body["chunk_count"], body["summary_count"], body["document_count"]) == (10, 2, 3)
    assert body["count_source"] == "vector_table"
