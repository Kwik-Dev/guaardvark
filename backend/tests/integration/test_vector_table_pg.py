"""The vector-table SQL against a real PostgreSQL: re-index replacement by
watermark, the doc_<id>_ purge pattern, and the counts.

Runs only against a throwaway database named by GUAARDVARK_TEST_PG_DSN (e.g.
``dbname=rag_test host=/tmp port=55432 user=postgres``), never any other. The
table has PGVectorStore's columns (llama-index-vector-stores-postgres
get_data_model: id BIGINT autoincrement, text, metadata_ JSON, node_id, and the
embedding when the pgvector extension is installed).
"""
import json
import os

import pytest

DSN = os.environ.get("GUAARDVARK_TEST_PG_DSN")
if not DSN:
    pytest.skip("set GUAARDVARK_TEST_PG_DSN to a throwaway PostgreSQL database to run these",
                allow_module_level=True)
psycopg2 = pytest.importorskip("psycopg2")

from backend.services import indexing_service as isvc  # noqa: E402

TABLE = "rag1_test"


@pytest.fixture
def table(monkeypatch):
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "data_{TABLE}"')
        cur.execute(f'CREATE TABLE "data_{TABLE}" (id BIGSERIAL PRIMARY KEY, text VARCHAR NOT NULL, '
                    f'metadata_ JSON, node_id VARCHAR)')
    monkeypatch.setattr(isvc, "_vector_backend", lambda: "pgvector")
    monkeypatch.setattr(isvc, "_pg_table_name", lambda project_id=None, profile=None: TABLE)
    monkeypatch.setattr(isvc, "_pg_connect", lambda: psycopg2.connect(DSN))
    monkeypatch.setattr(isvc, "_active_embed_dim", lambda: 0)

    def insert(*rows):
        with conn.cursor() as cur:
            for text, meta in rows:
                cur.execute(f'INSERT INTO "data_{TABLE}" (text, metadata_, node_id) VALUES (%s, %s, %s)',
                            (text, json.dumps(meta), text))

    def texts():
        with conn.cursor() as cur:
            cur.execute(f'SELECT text FROM "data_{TABLE}" ORDER BY id')
            return [r[0] for r in cur.fetchall()]

    yield insert, texts
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "data_{TABLE}"')
    conn.close()


def test_a_reindex_removes_only_rows_older_than_the_watermark(table):
    insert, texts = table
    insert(("old1", {"document_id": "doc_42_aaaa"}), ("other", {"document_id": "doc_420_bbbb"}))
    mark = isvc.vector_row_watermark()
    insert(("new1", {"document_id": "doc_42_cccc"}), ("same-id-again", {"document_id": "doc_42_aaaa"}))
    removed = isvc.purge_document_vectors(42, up_to_row=mark)
    assert removed == 1 and removed.ok
    assert texts() == ["other", "new1", "same-id-again"]


def test_code_rows_stored_the_old_way_are_not_matched(table):
    """Rows written before code chunks were bound: what a purge could not reach."""
    insert, texts = table
    insert(("ast", {"document_id": "3f6c1b2e-9d4a-4c1e-8f00-1a2b3c4d5e6f"}),
           ("line", {"document_id": "None"}), ("bound", {"document_id": "doc_42_0123456789abcdef"}))
    assert isvc.purge_document_vectors(42) == 1
    assert texts() == ["ast", "line"]


def test_counts_separate_summaries_and_documents(table):
    insert, _ = table
    insert(("a1", {"document_id": "doc_42_aa", "content_type": "text"}), ("a2", {"document_id": "doc_42_bb"}),
           ("b1", {"document_id": "doc_420_cc"}), ("s", {"document_id": "None", "content_type": "raptor_summary"}))
    stats = isvc.vector_store_stats()
    assert (stats["rows"], stats["chunks"], stats["summaries"], stats["documents"]) == (4, 3, 1, 2)
    assert isvc.document_vector_stats(42) == {"chunks": 2, "avg_chars": 2}
