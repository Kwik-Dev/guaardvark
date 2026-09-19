"""list_documents must list documents, not RAPTOR's corpus summaries."""

import pytest

from backend.tools import knowledge_tools
from backend.tools.knowledge_tools import ListDocumentsTool

# One row per indexed passage, the shape the pgvector table holds.
PASSAGES = [
    {"source_filename": "handbook.pdf", "content_type": "text", "parsed_by": "pymupdf"},
    {"source_filename": "handbook.pdf", "content_type": "text", "parsed_by": "pymupdf"},
    {"source_filename": "notes.md", "content_type": "text", "parsed_by": "markdown"},
    {"source_filename": "[corpus summary L1#0]", "content_type": "raptor_summary", "parsed_by": "raptor"},
    {"source_filename": "[corpus summary L1#1]", "content_type": "raptor_summary", "parsed_by": "raptor"},
    {"source_filename": "[corpus summary L2#0]", "content_type": "raptor_summary", "parsed_by": "raptor"},
]


@pytest.fixture
def executed_sql(monkeypatch):
    """Stand in for Postgres, applying whatever exclusion the SQL asks for."""
    seen = []

    def fake_query(sql, params):
        seen.append(sql)
        rows = PASSAGES
        if "IS DISTINCT FROM 'raptor_summary'" in sql or "IS DISTINCT FROM 'raptor'" in sql:
            rows = [
                r for r in rows
                if r["content_type"] != "raptor_summary" and r["parsed_by"] != "raptor"
            ]

        if sql.strip().startswith("SELECT count(DISTINCT"):
            return [(len({r["source_filename"] for r in rows}),)], None

        grouped = {}
        for row in rows:
            entry = grouped.setdefault(row["source_filename"], {"chunks": 0, "parsed_by": row["parsed_by"]})
            entry["chunks"] += 1
        listing = [(src, v["chunks"], 1, v["parsed_by"]) for src, v in grouped.items()]
        listing.sort(key=lambda r: r[1], reverse=True)
        return listing, None

    monkeypatch.setattr(knowledge_tools, "_table", lambda: ("data_vec_768", None))
    monkeypatch.setattr(knowledge_tools, "_query", fake_query)
    return seen


def test_raptor_summaries_are_not_listed_as_documents(executed_sql):
    result = ListDocumentsTool().execute()

    assert result.success, result.error
    assert "corpus summary" not in result.output
    assert "raptor" not in result.output
    assert "handbook.pdf" in result.output
    assert "notes.md" in result.output


def test_raptor_summaries_are_not_counted_in_the_total(executed_sql):
    result = ListDocumentsTool().execute()

    assert result.metadata["total"] == 2
    assert result.metadata["returned"] == 2
    assert "2 document(s) indexed" in result.output


def test_both_queries_exclude_summaries(executed_sql):
    ListDocumentsTool().execute()

    listing_sql, total_sql = executed_sql[0], executed_sql[1]
    assert total_sql.strip().startswith("SELECT count(DISTINCT")
    for sql in (listing_sql, total_sql):
        assert "IS DISTINCT FROM 'raptor_summary'" in sql
        assert "IS DISTINCT FROM 'raptor'" in sql


def test_name_filter_still_applies_alongside_the_exclusion(executed_sql):
    ListDocumentsTool().execute(name_contains="hand")

    listing_sql = executed_sql[0]
    assert "ILIKE %s" in listing_sql
    assert "IS DISTINCT FROM 'raptor_summary'" in listing_sql
    assert listing_sql.count("WHERE") == 1
