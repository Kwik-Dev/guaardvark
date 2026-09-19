"""Guards for the RAG audit quick wins (2.9.1 group A4).

Each section names the defect it pins down. None of these tests need a database,
an index, a model or the network: the storage and vector layers are stand-ins
that record what they were handed.
"""

import os

import pytest


# --------------------------------------------------------------------------
# R2. The unified index manager must load onto the configured vector store
# --------------------------------------------------------------------------
class PGVectorStore:
    """Named like the real class: the manager checks the type name, not the type."""


class _NotPGVectorStore:
    """What the factory substitutes when Postgres is unreachable: an empty store."""


def _manager(tmp_path):
    from backend.utils.unified_index_manager import UnifiedIndexManager

    return UnifiedIndexManager(str(tmp_path / "index"), max_cached_indexes=2)


def _write_persisted_index(persist_dir):
    persist_dir.mkdir(parents=True, exist_ok=True)
    (persist_dir / "docstore.json").write_text('{"docstore/data": {"n1": {}}}')
    (persist_dir / "index_store.json").write_text('{"index_store/data": {"i1": {}}}')


def _stub_storage(monkeypatch, calls):
    import backend.utils.unified_index_manager as uim

    class _Ctx:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def persist(self, persist_dir=None):
            calls.append(("persist", persist_dir))

    class _StorageContext:
        @staticmethod
        def from_defaults(**kwargs):
            calls.append(("from_defaults", kwargs))
            return _Ctx(**kwargs)

    monkeypatch.setattr(uim, "StorageContext", _StorageContext)
    monkeypatch.setattr(uim, "load_index_from_storage", lambda ctx: ("index", ctx))


def test_manager_load_attaches_the_pgvector_store(monkeypatch, tmp_path):
    """Loading with persist_dir alone reads a SimpleVectorStore out of the JSON
    directory while the vectors live in Postgres: an index that answers every
    query from an empty store and reports itself healthy."""
    import backend.services.indexing_service as isvc

    calls = []
    _stub_storage(monkeypatch, calls)
    monkeypatch.setenv("GUAARDVARK_VECTOR_STORE", "pgvector")
    seen = {}
    store = PGVectorStore()

    def _factory(project_id=None, profile=None):
        seen["project_id"] = project_id
        return store

    monkeypatch.setattr(isvc, "_make_vector_store", _factory)

    mgr = _manager(tmp_path)
    _write_persisted_index(mgr._get_persist_dir("7"))

    index, ctx = mgr.get_index("7", create_if_missing=False)

    load = [c for c in calls if c[0] == "from_defaults"]
    assert len(load) == 1
    assert load[0][1]["vector_store"] is store
    assert load[0][1]["persist_dir"] == str(mgr._get_persist_dir("7"))
    # The per-project table, not the global one: the persist dir is per project too.
    assert seen["project_id"] == "7"
    assert ctx.kwargs["vector_store"] is store


def test_manager_refuses_to_load_without_pgvector_and_does_not_persist(monkeypatch, tmp_path):
    """When the factory falls back to an empty in-memory store, the manager must
    refuse -- and must not treat the refusal as "start a fresh index", which would
    persist an empty docstore over the one on disk."""
    import backend.services.indexing_service as isvc
    from backend.utils.unified_index_manager import VectorStoreUnavailable

    calls = []
    _stub_storage(monkeypatch, calls)
    monkeypatch.setenv("GUAARDVARK_VECTOR_STORE", "pgvector")
    monkeypatch.setattr(isvc, "_make_vector_store", lambda project_id=None, profile=None: _NotPGVectorStore())
    monkeypatch.setattr(isvc, "vector_store_fallback_reason", lambda: "it is unavailable (test)")

    mgr = _manager(tmp_path)
    persist_dir = mgr._get_persist_dir("7")
    _write_persisted_index(persist_dir)
    before = {p.name: p.read_text() for p in persist_dir.iterdir()}

    with pytest.raises(VectorStoreUnavailable) as exc:
        mgr.get_index("7", create_if_missing=True)

    assert "it is unavailable (test)" in str(exc.value)
    assert not any(c[0] == "persist" for c in calls), "the refusal persisted something"
    assert not any(c[0] == "from_defaults" for c in calls)
    assert {p.name: p.read_text() for p in persist_dir.iterdir()} == before
    assert "project_7" not in mgr.cached_indexes


def test_manager_file_backend_loads_the_persisted_vectors(monkeypatch, tmp_path):
    """With a file-backed backend the vectors are in persist_dir; passing a fresh
    SimpleVectorStore on load would shadow them the same way."""
    import backend.services.indexing_service as isvc

    calls = []
    _stub_storage(monkeypatch, calls)
    monkeypatch.setenv("GUAARDVARK_VECTOR_STORE", "simple")
    monkeypatch.setattr(
        isvc, "_make_vector_store",
        lambda project_id=None, profile=None: pytest.fail("factory must not run for a file-backed load"),
    )

    mgr = _manager(tmp_path)
    _write_persisted_index(mgr._get_persist_dir(None))

    mgr.get_index(None, create_if_missing=False)

    load = [c for c in calls if c[0] == "from_defaults"]
    assert len(load) == 1
    assert "vector_store" not in load[0][1]


# --------------------------------------------------------------------------
# R3. A repository summary is the LLM's answer or it is not indexed at all
# --------------------------------------------------------------------------
def _summary_args():
    return ("repo", ["Flask"], {".py": 3}, "backend/app.py\nbackend/models.py", {})


def _llm(monkeypatch, behaviour):
    import backend.utils.llm_service as llm

    def _generate(prompt=None, llm=None, is_json_response=False):
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour

    monkeypatch.setattr(llm, "generate_text_basic", _generate)


def test_summary_is_none_with_a_reason_when_the_llm_raises(monkeypatch):
    from backend.services.repository_analysis_service import RepositoryAnalysisService as S

    _llm(monkeypatch, ConnectionError("ollama down"))
    summary, reason = S._generate_llm_summary(*_summary_args())
    assert summary is None
    assert "ConnectionError" in reason and "ollama down" in reason


@pytest.mark.parametrize("answer, expected", [
    (None, "no LLM available"),
    ("", "empty or too-short"),
    ("too short", "empty or too-short"),
])
def test_summary_is_none_when_the_llm_does_not_answer(monkeypatch, answer, expected):
    from backend.services.repository_analysis_service import RepositoryAnalysisService as S

    _llm(monkeypatch, answer)
    summary, reason = S._generate_llm_summary(*_summary_args())
    assert summary is None
    assert expected in reason


def test_summary_is_the_llm_answer_when_it_answers(monkeypatch):
    from backend.services.repository_analysis_service import RepositoryAnalysisService as S

    _llm(monkeypatch, "  " + "A Flask monolith with a Celery worker. " * 3 + "  ")
    summary, reason = S._generate_llm_summary(*_summary_args())
    assert summary.startswith("A Flask monolith") and not summary.endswith(" ")
    assert reason is None


def test_no_template_summary_remains(monkeypatch):
    """The template ("Repository: <name> / Frameworks: ... / Top Directories: ...")
    was indexed as repository_summary whenever Ollama was down, and nothing in the
    index distinguished it from a real one."""
    import inspect
    from backend.services import repository_analysis_service as ras

    src = inspect.getsource(ras.RepositoryAnalysisService._generate_llm_summary)
    assert "Top Directories" not in src
    assert "backend.services.llm_service" not in src, "that module is empty; the import always failed"


class _FakeFolder:
    def __init__(self):
        self.id = 4
        self.name = "repo"
        self.path = "repos/repo"
        self.description = "the real summary from last week"
        self.repo_metadata = None
        self.is_repository = False


def _analyze(monkeypatch, llm_behaviour):
    import json
    from types import SimpleNamespace
    from backend.services import repository_analysis_service as ras

    folder = _FakeFolder()
    indexed = []
    _llm(monkeypatch, llm_behaviour)
    monkeypatch.setattr(ras, "db", SimpleNamespace(session=SimpleNamespace(
        get=lambda model, fid: folder if fid == 4 else None,
        commit=lambda: None,
    )))
    monkeypatch.setattr(ras, "add_text_to_index", lambda **kw: indexed.append(kw) or True)
    S = ras.RepositoryAnalysisService
    monkeypatch.setattr(S, "_get_all_files_recursive", staticmethod(lambda folder: []))
    monkeypatch.setattr(S, "_read_key_files", staticmethod(lambda docs: {}))
    monkeypatch.setattr(S, "build_dependency_graph", staticmethod(lambda fid: {}))
    monkeypatch.setattr(S, "generate_repository_map", staticmethod(lambda fid, budget=4096: ""))

    metadata = S.analyze_repository(4)
    return folder, indexed, json.loads(folder.repo_metadata), metadata


def test_analyze_repository_records_pending_and_indexes_nothing_when_the_llm_is_down(monkeypatch):
    folder, indexed, stored, returned = _analyze(monkeypatch, RuntimeError("ollama down"))

    assert [kw["metadata"]["type"] for kw in indexed] == [], "a summary was indexed without an LLM"
    assert stored["summary_status"] == "pending"
    assert "ollama down" in stored["summary_error"]
    assert returned["summary_status"] == "pending"
    # The last real summary is still the best description on record.
    assert folder.description == "the real summary from last week"
    assert folder.is_repository is True


def test_analyze_repository_indexes_the_llm_summary_when_it_answers(monkeypatch):
    text = "A Flask monolith with a Celery worker. " * 3
    folder, indexed, stored, _ = _analyze(monkeypatch, text)

    summaries = [kw for kw in indexed if kw["metadata"]["type"] == "repository_summary"]
    assert len(summaries) == 1
    assert summaries[0]["text"] == text.strip()
    assert summaries[0]["metadata"]["folder_id"] == 4
    assert summaries[0]["replace_where"] == ["type", "folder_id"]
    assert stored["summary_status"] == "ok" and "summary_error" not in stored
    assert folder.description == text.strip()


# --------------------------------------------------------------------------
# R6. purge_document_vectors says why it removed nothing
# --------------------------------------------------------------------------
class _Cursor:
    def __init__(self, rowcount, fail=None):
        self.rowcount = rowcount
        self.fail = fail
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if self.fail:
            raise self.fail
        self.executed.append((sql, params))


class _Conn:
    def __init__(self, rowcount=0, fail=None):
        self.cur = _Cursor(rowcount, fail)
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _pgvector(monkeypatch, table="t_384", conn=None):
    import backend.services.indexing_service as isvc

    monkeypatch.setattr(isvc, "_vector_backend", lambda: "pgvector")
    monkeypatch.setattr(isvc, "_pg_table_name", lambda project_id=None, profile=None: table)
    if conn is not None:
        monkeypatch.setattr(isvc, "_pg_connect", lambda: conn)


def test_purge_reports_each_way_it_could_not_run(monkeypatch):
    import backend.services.indexing_service as isvc

    monkeypatch.setattr(isvc, "_vector_backend", lambda: "simple")
    r = isvc.purge_document_vectors(5)
    assert r == 0 and not r.ok and r.reason == "not_pgvector"

    _pgvector(monkeypatch)
    r = isvc.purge_document_vectors(None)
    assert r == 0 and not r.ok and r.reason == "no_document_id"

    _pgvector(monkeypatch, table=None)
    r = isvc.purge_document_vectors(5)
    assert r == 0 and not r.ok and r.reason == "no_table"

    _pgvector(monkeypatch, conn=_Conn(fail=RuntimeError("connection refused")))
    r = isvc.purge_document_vectors(5)
    assert r == 0 and not r.ok
    assert r.reason.startswith("error: RuntimeError: connection refused")


def test_purge_distinguishes_nothing_to_purge_from_could_not_purge(monkeypatch):
    import backend.services.indexing_service as isvc

    conn = _Conn(rowcount=0)
    _pgvector(monkeypatch, conn=conn)
    r = isvc.purge_document_vectors(5)
    assert r == 0 and r.ok and r.reason is None
    assert conn.committed and conn.closed

    conn = _Conn(rowcount=3)
    _pgvector(monkeypatch, conn=conn)
    r = isvc.purge_document_vectors(5)
    assert r == 3 and r.ok
    # The existing callers sum it and truth-test it; both must keep working.
    assert isinstance(r, int) and (0 + r) == 3 and bool(r) is True
    assert bool(isvc.PurgeResult(0, "no_table")) is False


def test_reindex_warns_when_the_purge_did_not_run():
    """The caller in add_file_to_index inserts regardless; it must say when the
    previous copy may still be there, and stay quiet on a file-backed store,
    where there is nothing to purge."""
    import inspect
    import backend.services.indexing_service as isvc

    src = inspect.getsource(isvc.add_file_to_index)
    assert "_purged = purge_document_vectors(" in src
    assert 'if not _purged.ok and _purged.reason != "not_pgvector"' in src


def _list_documents(monkeypatch, count_result, rows, limit=None):
    from backend.tools import knowledge_tools as kt

    monkeypatch.setattr(kt, "_table", lambda: ("data_t", None))
    answers = iter([(rows, None), count_result])
    monkeypatch.setattr(kt, "_query", lambda sql, params: next(answers))
    return kt.ListDocumentsTool().execute(limit=limit)


def test_list_documents_reports_an_unavailable_count_instead_of_the_page_length(monkeypatch):
    rows = [("a.md", 4, 1, "docling"), ("b.md", 2, 1, "docling")]
    res = _list_documents(monkeypatch, (None, "canceling statement due to timeout"), rows, limit=2)

    assert res.success
    assert "document count unavailable (canceling statement due to timeout)" in res.output
    assert "2 document(s) indexed" not in res.output
    assert "a.md — 4 passages" in res.output
    # A full page with no total: the caller is told there may be more, not that
    # the corpus is exactly this big.
    assert "there may be more — call again with offset=2" in res.output


def test_list_documents_reports_the_real_total_when_the_count_works(monkeypatch):
    rows = [("a.md", 4, 1, "docling")]
    res = _list_documents(monkeypatch, ([(37,)], None), rows, limit=1)

    assert "37 document(s) indexed" in res.output
    assert "(36 more — call again with offset=1)" in res.output
