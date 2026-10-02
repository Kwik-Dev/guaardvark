"""locate_vector_table names a scope's vector table only when it exists and is
the current one, and otherwise says why; the navigation tools pass that on.

The embedding probe and the table listing are replaced, so no database and no
embedding model are touched.
"""

import pytest

from backend.services import indexing_service as ix
from backend.tools import knowledge_tools


@pytest.fixture
def index(monkeypatch):
    """Set what the embedding probe returns and which tables exist."""
    monkeypatch.setenv("GUAARDVARK_VECTOR_STORE", "pgvector")
    monkeypatch.setattr(ix, "_test_table_prefix", lambda: "")
    # The scope comes from the saved index profiles; pin it so no settings are read.
    monkeypatch.setattr(ix, "_vector_scope", lambda project_id=None, profile=None: "global")
    monkeypatch.setattr("backend.config.get_active_embedding_model", lambda: "some-embedder")

    def arrange(width, tables):
        monkeypatch.setattr(
            ix, "_pg_table_name",
            lambda *a, **k: f"guaardvark_global_{width}" if width else None,
        )
        monkeypatch.setattr(ix, "_vector_table_names", lambda scope: sorted(tables))

    return arrange


def test_the_active_models_table_is_returned_when_it_exists(index):
    index(768, ["data_guaardvark_global_2560", "data_guaardvark_global_768"])

    assert ix.locate_vector_table(None) == ix.VectorTable("guaardvark_global_768")


def test_a_table_that_was_never_created_is_not_returned(index):
    index(1024, ["data_guaardvark_global_2560", "data_guaardvark_global_768"])

    found = ix.locate_vector_table(None)

    assert found.table is None
    assert found.reason == "no_index_for_model"
    assert "some-embedder, 1024 dimensions" in found.detail
    assert "768, 2560 dimensions" in found.detail


def test_a_fresh_install_with_no_table_says_so(index):
    index(768, [])

    found = ix.locate_vector_table(None)

    assert (found.table, found.reason) == (None, "no_index_for_model")
    assert "different embedding model" not in found.detail


def test_without_the_probe_a_single_table_is_still_unambiguous(index):
    index(None, ["data_guaardvark_global_768"])

    assert ix.locate_vector_table(None) == ix.VectorTable("guaardvark_global_768")


def test_without_the_probe_several_tables_are_not_guessed_between(index):
    index(None, ["data_guaardvark_global_2560", "data_guaardvark_global_768"])

    found = ix.locate_vector_table(None)

    assert found.table is None
    assert found.reason == "embedding_unreachable"
    assert "2 knowledge indexes exist (768, 2560 dimensions)" in found.detail


def test_without_the_probe_and_without_tables_there_is_no_index(index):
    index(None, [])

    assert ix.locate_vector_table(None).reason == "no_index"


def test_another_scopes_tables_are_not_counted(index):
    """'global_archive' shares the 'global' prefix the listing matches on."""
    index(None, ["data_guaardvark_global_768", "data_guaardvark_global_archive_2560"])

    assert ix.locate_vector_table(None) == ix.VectorTable("guaardvark_global_768")


def test_a_database_that_cannot_be_asked_is_reported(index, monkeypatch):
    index(768, [])

    def unreachable(scope):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ix, "_vector_table_names", unreachable)

    found = ix.locate_vector_table(None)

    assert (found.table, found.reason) == (None, "lookup_failed")
    assert "connection refused" in found.detail


def test_another_vector_backend_has_no_table(index, monkeypatch):
    index(768, ["data_guaardvark_global_768"])
    monkeypatch.setenv("GUAARDVARK_VECTOR_STORE", "simple")

    assert ix.locate_vector_table(None).reason == "not_pgvector"


# --------------------------------------------------------------------------
# What the navigation tools do with it
# --------------------------------------------------------------------------
def test_navigation_tools_read_the_located_table(index):
    index(768, ["data_guaardvark_global_768"])

    assert knowledge_tools._table() == ("data_guaardvark_global_768", None)


@pytest.mark.parametrize("tool_class,arguments", [
    (knowledge_tools.ListDocumentsTool, {}),
    (knowledge_tools.DocumentOutlineTool, {"source_filename": "notes.md"}),
    (knowledge_tools.ReadDocumentSectionTool, {"source_filename": "notes.md"}),
    (knowledge_tools.CorpusSummaryTool, {}),
])
def test_navigation_tools_report_the_condition_and_run_no_query(index, monkeypatch, tool_class, arguments):
    index(None, ["data_guaardvark_global_2560", "data_guaardvark_global_768"])
    queries = []
    monkeypatch.setattr(knowledge_tools, "_query", lambda sql, params: queries.append(sql) or ([], None))

    result = tool_class().execute(**arguments)

    assert result.success is False
    assert "embedding model could not be reached" in result.error
    assert queries == []
