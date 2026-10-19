"""When chat could not search the person's documents, the failure is logged at
WARNING and recorded in the turn's provenance (rag_error), and, with the
chat_say_rag_failed setting on (off by default), the model is told to say so
instead of answering a question about the documents from general knowledge.
Finding nothing is not a failure and changes nothing."""
import logging

import pytest

from backend.services import unified_chat_engine as uce


@pytest.fixture
def engine(monkeypatch):
    e = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
    e._prov = {}
    e._project_id = None
    monkeypatch.setattr("backend.utils.reranker.drop_unrelated", lambda results: (results, 0))
    monkeypatch.setattr("backend.services.knowledge_sources.retrieve_from_sources", lambda q: [])
    monkeypatch.delenv("GUAARDVARK_CHAT_SAY_RAG_FAILED", raising=False)
    return e


def _search(monkeypatch, fn):
    monkeypatch.setattr("backend.services.indexing_service.search_with_llamaindex", fn)


def test_an_exception_is_a_warning_and_in_the_trace(engine, monkeypatch, caplog):
    def boom(*a, **k):
        raise ConnectionError("database is down")
    _search(monkeypatch, boom)
    with caplog.at_level(logging.WARNING, logger=uce.logger.name):
        assert engine._retrieve_rag_context("what is in my contract?") == ""
    assert "RAG retrieval failed" in caplog.text and "database is down" in caplog.text
    assert engine._prov["rag_error"] == "ConnectionError: database is down"


def test_a_search_that_could_not_run_is_a_failure(engine, monkeypatch):
    _search(monkeypatch, lambda q, project_id=None, with_trace=False: {
        "results": [], "trace": {"error": "embedding_model_mismatch", "degraded": True}})
    engine._retrieve_rag_context("q")
    assert engine._prov["rag_error"] == "embedding_model_mismatch"


def test_finding_nothing_is_not_a_failure(engine, monkeypatch):
    _search(monkeypatch, lambda q, project_id=None, with_trace=False: {"results": [], "trace": {"error": None}})
    engine._retrieve_rag_context("q")
    assert "rag_error" not in engine._prov and engine._rag_failure_note() == ""


def test_the_user_is_told_only_with_the_setting_on(engine, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("database is down")
    _search(monkeypatch, boom)
    engine._retrieve_rag_context("q")
    assert engine._rag_failure_note() == ""  # default: off
    monkeypatch.setenv("GUAARDVARK_CHAT_SAY_RAG_FAILED", "true")
    note = engine._rag_failure_note()
    assert "couldn't search their documents" in note and "ConnectionError: database is down" in note
