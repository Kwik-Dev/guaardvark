"""A project-scoped search reads the table the vector leg reads, keeps corpus
summaries that belong to no project, and its trace names only legs that ran.

In global mode (the default) every document is in one shared table. The
keyword leg used to look up a per-project table by the raw project id, which
global mode never creates: every query failed, the leg returned nothing, and
the trace still said vector+bm25. The retrievers here are stand-ins recording
what they were asked; the routing, filtering and trace in
search_with_llamaindex are real.
"""
from types import SimpleNamespace

import pytest

from backend.services import indexing_service as ix


class _Node(SimpleNamespace):
    def get_content(self):
        return self.text


def _hit(text, **metadata):
    return SimpleNamespace(node=_Node(text=text, metadata=dict(metadata), node_id=text), score=0.5)


@pytest.mark.parametrize("mode,expected_scope,expected_filter", [
    ("global", None, 5), ("per_project", 5, None)])
def test_the_keyword_leg_reads_the_vector_legs_table(monkeypatch, mode, expected_scope, expected_filter):
    asked = []
    monkeypatch.setenv("GUAARDVARK_PROJECT_INDEX_MODE", mode)
    monkeypatch.setattr(ix, "resolve_existing_vector_table",
                        lambda scope, profile=None: asked.append(scope) or "guaardvark_t_768")
    leg = ix._get_sparse_retriever(None, 10, project_id=5)
    assert asked == [expected_scope]
    assert isinstance(leg, ix.PostgresSparseRetriever) and leg.project_id == expected_filter


def test_a_keyword_leg_that_fails_says_so(monkeypatch):
    leg = ix.PostgresSparseRetriever("t", top_k=5)
    monkeypatch.setattr(leg, "_rows", lambda text: (_ for _ in ()).throw(RuntimeError('relation "data_x" does not exist')))
    assert leg.retrieve("anything") == []
    assert leg.last_error.startswith('RuntimeError: relation "data_x"')


def test_project_scope_keeps_corpus_summaries_without_a_project():
    assert ix.in_project_scope({"project_id": "5"}, 5)
    assert not ix.in_project_scope({"project_id": "6"}, 5)
    assert ix.in_project_scope({"content_type": "raptor_summary"}, 5)
    assert not ix.in_project_scope({"content_type": "raptor_summary", "project_id": "6"}, 5)
    assert not ix.in_project_scope({}, 5)
    sql, params = ix.project_scope_sql(5)
    assert params == ["5"] and "raptor_summary" in sql and "IS NULL" in sql


@pytest.fixture
def search(monkeypatch):
    """search_with_llamaindex with both legs replaced by recorders."""
    import llama_index.core.retrievers as li_retrievers
    from backend.utils import reranker, experiment_context as ec

    state = {"vector": [], "sparse": [], "sparse_error": None}

    class Vector:
        def retrieve(self, q):
            return list(state["vector"])

    class Sparse:
        last_error, last_count = None, 0

        def retrieve(self, q):
            self.last_error = state["sparse_error"]
            hits = [] if self.last_error else list(state["sparse"])
            self.last_count = len(hits)
            return hits

    class Fusion:
        def __init__(self, retrievers, **kw):
            self.retrievers = retrievers

        def retrieve(self, q):
            return [h for r in self.retrievers for h in r.retrieve(q)]

    index = SimpleNamespace(as_retriever=lambda **kw: Vector())
    monkeypatch.setattr(li_retrievers, "QueryFusionRetriever", Fusion, raising=False)
    monkeypatch.setattr(ix, "index", index)
    monkeypatch.setattr(ix, "storage_context", object())
    monkeypatch.setattr(ix, "_get_sparse_retriever", lambda *a, **k: Sparse())
    monkeypatch.setattr(ix, "vector_store_fallback_reason", lambda: None)
    monkeypatch.setattr(ix, "_check_index_embedding_model", lambda project_id=None: True)
    monkeypatch.setattr(ix, "_get_cached_query_embedding", lambda q: None)
    monkeypatch.setattr(ix, "_under_resource_pressure", lambda: False)
    monkeypatch.setattr(ix, "deduplicate_chunks", lambda results: results)
    monkeypatch.setattr(ix, "_vector_backend", lambda: "simple")
    monkeypatch.setattr("backend.utils.context_expander.expand_with_dependencies",
                        lambda results, project_id=None: results)
    monkeypatch.setattr(reranker, "rerank", lambda q, results: (results, {"applied": False, "reason": "disabled"}))
    monkeypatch.setenv("GUAARDVARK_RERANK_ENABLED", "false")
    ec.clear_experiment_config()
    ec.invalidate_active_params_cache()
    yield state
    ec.clear_experiment_config()


def test_the_trace_drops_a_keyword_leg_that_failed(search):
    search["vector"] = [_hit("v", project_id="5")]
    search["sparse_error"] = 'ProgrammingError: relation "data_guaardvark_5_768" does not exist'
    out = ix.search_with_llamaindex("q", project_id=5, with_trace=True)
    trace = out["trace"]
    assert trace["legs"] == ["vector"] and trace["fusion"] == "vector_only"
    assert trace["degraded"] and "keyword leg failed" in trace["degraded_reason"]


def test_a_keyword_leg_that_ran_stays_in_the_trace(search):
    search["sparse"] = [_hit("k", project_id="5")]
    trace = ix.search_with_llamaindex("q", project_id=5, with_trace=True)["trace"]
    assert trace["legs"] == ["vector", "bm25"] and trace["sparse_hits"] == 1


def test_a_project_search_keeps_global_corpus_summaries(search):
    search["vector"] = [_hit("mine", project_id="5"), _hit("theirs", project_id="6"),
                        _hit("corpus summary", content_type="raptor_summary"),
                        _hit("their summary", content_type="raptor_summary", project_id="6")]
    out = ix.search_with_llamaindex("q", project_id=5, max_chunks=10, with_trace=True)
    assert [r["text"] for r in out["results"]] == ["mine", "corpus summary"]


@pytest.mark.parametrize("mode,project,table_scope,extra", [
    ("global", 5, None, " AND metadata_->>'project_id' = %s"),
    ("global", None, None, ""),
    ("per_project", 5, 5, ""),
])
def test_raptor_reads_the_table_the_index_writes(monkeypatch, mode, project, table_scope, extra):
    from backend.services import raptor_service as rs
    monkeypatch.setenv("GUAARDVARK_PROJECT_INDEX_MODE", mode)
    monkeypatch.setattr(ix, "_pg_table_name", lambda scope=None, profile=None: f"t_{scope}")
    table, sql, params = rs._scoped_table(project)
    assert table == f"t_{table_scope}" and sql == extra
    assert params == ([str(project)] if extra else [])
