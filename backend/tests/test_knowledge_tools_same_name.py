"""Two indexed files can share a filename (different folders or projects). The
navigation tools keep them apart by document id, and say so when a bare name
does not identify one file.

A stand-in for Postgres answers the queries the tools send from a small passage
table, so nothing here touches a database.
"""

import pytest

from backend.tools import knowledge_tools
from backend.tools.knowledge_tools import (
    DocumentOutlineTool,
    ListDocumentsTool,
    ReadDocumentSectionTool,
    _contains,
    _document_key,
    _shown_path,
)

UPLOADS = "/srv/app/data/uploads"

# One row per indexed passage: (id, source_filename, document key, file_path, heading_path, text).
PASSAGES = [
    (1, "README.md", "12", f"{UPLOADS}/ClientA/README.md", "Welcome", "Client A workspace."),
    (2, "README.md", "40", f"{UPLOADS}/Product Docs/README.md", "Install", "Run the installer."),
    (3, "README.md", "40", f"{UPLOADS}/Product Docs/README.md", "Usage", "Start the app."),
    (4, "notes.md", "7", f"{UPLOADS}/notes.md", "Ideas", "One idea."),
]


@pytest.fixture
def db(monkeypatch):
    """Answer each query shape the tools send; record the SQL and parameters."""
    seen = []

    def rows_for(sql, params):
        """Passages of params[0], narrowed to a document key when the SQL filters on one."""
        rows = [r for r in PASSAGES if r[1] == params[0]]
        if f"{knowledge_tools._DOC_KEY} = %s" in sql:
            rows = [r for r in rows if r[2] == params[1]]
        return rows

    def fake_query(sql, params):
        seen.append((sql, params))
        if "OVER (PARTITION BY src)" in sql:  # list_documents
            files = {}
            for _id, src, doc, path, heading, _text in PASSAGES:
                entry = files.setdefault((src, doc), {"chunks": 0, "headings": set(), "path": path})
                entry["chunks"] += 1
                entry["headings"].add(heading)
            sharing = {}
            for src, _doc in files:
                sharing[src] = sharing.get(src, 0) + 1
            listing = [(src, v["chunks"], len(v["headings"]), "markdown", doc, v["path"], sharing[src])
                       for (src, doc), v in files.items()]
            listing.sort(key=lambda r: (-r[1], r[0], r[4]))
            limit, offset = params[-2], params[-1]
            return listing[offset:offset + limit], None
        if sql.strip().startswith("SELECT count(DISTINCT"):
            return [(len({(r[1], r[2]) for r in PASSAGES}),)], None
        if "GROUP BY 1 ORDER BY 3 DESC" in sql:  # which files carry this name
            files = {}
            for row in rows_for(sql, params):
                files.setdefault(row[2], [row[3], 0])[1] += 1
            return sorted(((doc, path, n) for doc, (path, n) in files.items()),
                          key=lambda r: (-r[2], r[0])), None
        if "GROUP BY 1, 2" in sql:  # the outline
            return [(row[4], "", 1) for row in rows_for(sql, params)], None
        if sql.startswith("SELECT count(*)"):
            return [(len(rows_for(sql, params)),)], None
        return [(row[5], {"heading_path": row[4]}) for row in rows_for(sql, params)], None

    monkeypatch.setattr("backend.config.UPLOAD_DIR", UPLOADS)
    monkeypatch.setattr(knowledge_tools, "_table", lambda: ("data_vec_768", None))
    monkeypatch.setattr(knowledge_tools, "_query", fake_query)
    return seen


# --------------------------------------------------------------------------
# list_documents
# --------------------------------------------------------------------------
def test_files_sharing_a_name_are_listed_separately_with_their_id_and_folder(db):
    result = ListDocumentsTool().execute()

    assert result.success, result.error
    assert "3 document(s) indexed" in result.output
    assert "  README.md — 2 passages, 2 sections [markdown] · document_id 40 · Product Docs/README.md" in result.output
    assert "  README.md — 1 passages [markdown] · document_id 12 · ClientA/README.md" in result.output
    assert result.metadata["shared_names"] == ["README.md"]
    assert "pass that document_id to get_document_outline or read_document_section" in result.output


def test_a_name_held_by_one_file_is_listed_as_before(db):
    result = ListDocumentsTool().execute()

    assert "  notes.md — 1 passages [markdown]\n" in result.output + "\n"
    assert "document_id 7" not in result.output
    assert UPLOADS not in result.output


# --------------------------------------------------------------------------
# get_document_outline / read_document_section
# --------------------------------------------------------------------------
@pytest.mark.parametrize("tool_class", [DocumentOutlineTool, ReadDocumentSectionTool])
def test_a_shared_name_without_an_id_lists_the_choices_and_reads_nothing(db, tool_class):
    result = tool_class().execute(source_filename="README.md")

    assert result.success
    assert "'README.md' is the name of 2 indexed files" in result.output
    assert "document_id 40 — Product Docs/README.md (2 passage(s))" in result.output
    assert "document_id 12 — ClientA/README.md (1 passage(s))" in result.output
    assert result.metadata["ambiguous"] is True
    assert "Client A workspace." not in result.output and "Run the installer." not in result.output
    assert len(db) == 1, "only the lookup of which files carry the name should run"


def test_outline_with_an_id_shows_that_file_only(db):
    result = DocumentOutlineTool().execute(source_filename="README.md", document_id=40)

    assert "OUTLINE — README.md · document_id 40 · Product Docs/README.md (2 passages)" in result.output
    assert "Install" in result.output and "Usage" in result.output
    assert "Welcome" not in result.output


@pytest.mark.parametrize("document_id", [12, "12", "doc_12_9f2c"])
def test_read_with_an_id_returns_that_files_passages_only(db, document_id):
    result = ReadDocumentSectionTool().execute(source_filename="README.md", document_id=document_id)

    assert result.success, result.error
    assert "README.md · document_id 12 · ClientA/README.md · whole document — passages 1-1 of 1" in result.output
    assert "Client A workspace." in result.output
    assert "Run the installer." not in result.output


@pytest.mark.parametrize("tool_class", [DocumentOutlineTool, ReadDocumentSectionTool])
def test_a_name_held_by_one_file_needs_no_id(db, tool_class):
    result = tool_class().execute(source_filename="notes.md")

    assert result.success, result.error
    assert "is the name of" not in result.output
    assert "document_id" not in result.output
    assert all(f"{knowledge_tools._DOC_KEY} = %s" not in sql for sql, _params in db)


def test_an_id_the_name_does_not_have_lists_the_ones_it_has(db):
    result = DocumentOutlineTool().execute(source_filename="README.md", document_id=7)

    assert result.success
    assert "'README.md' has no document_id 7" in result.output
    assert "document_id 40" in result.output and "document_id 12" in result.output


def test_a_document_id_that_is_not_a_number_is_an_error(db):
    result = ReadDocumentSectionTool().execute(source_filename="README.md", document_id="the big one")

    assert result.success is False
    assert "document_id must be the number list_documents prints" in result.error
    assert db == []


def test_an_unknown_name_is_a_notice(db):
    result = DocumentOutlineTool().execute(source_filename="missing.md")

    assert result.success
    assert "No indexed content for 'missing.md'" in result.output


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (None, (None, None)),
    ("", (None, None)),
    (12, ("12", None)),
    (" 012 ", ("12", None)),
    ("doc_12_9f2c", ("12", None)),
])
def test_document_key_accepts_the_number_or_the_stored_id(value, expected):
    assert _document_key(value) == expected


@pytest.mark.parametrize("path,expected", [
    (f"{UPLOADS}/ClientA/Contracts/terms.md", "ClientA/Contracts/terms.md"),
    (f"{UPLOADS}/notes.md", "notes.md"),
    ("/somewhere/else/project/notes.md", "project/notes.md"),
    (None, ""),
])
def test_shown_path_is_relative_to_the_uploads_folder(monkeypatch, path, expected):
    monkeypatch.setattr("backend.config.UPLOAD_DIR", UPLOADS)

    assert _shown_path(path) == expected


def test_filter_text_is_matched_literally(db):
    assert _contains("my_doc 100%") == "%my\\_doc 100\\%%"
    assert _contains("a\\b") == "%a\\\\b%"

    ListDocumentsTool().execute(name_contains="my_doc")
    ReadDocumentSectionTool().execute(source_filename="notes.md", heading_path="50%_off")

    listing_sql, listing_params = db[0]
    assert "ILIKE %s ESCAPE '\\'" in listing_sql
    assert listing_params[0] == "%my\\_doc%"
    read_sql, read_params = next((sql, params) for sql, params in db if "'heading_path' ILIKE" in sql)
    assert "ILIKE %s ESCAPE '\\'" in read_sql
    assert "%50\\%\\_off%" in read_params
