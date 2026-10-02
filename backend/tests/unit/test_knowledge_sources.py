import pytest

from backend.services import knowledge_sources as ks


@pytest.fixture(autouse=True)
def restore_registry():
    """Leave the process-wide registry exactly as the test found it."""
    snapshot = dict(ks._sources)
    yield
    ks._sources.clear()
    ks._sources.update(snapshot)


def hit(title, score=1.0, snippet=None):
    return {"title": title, "snippet": snippet or f"{title} body", "score": score}


def test_registry_starts_empty():
    assert ks.list_knowledge_sources() == []
    assert ks.retrieve_from_sources("anything") == []


def test_register_and_unregister():
    ks.register_knowledge_source("catalogue", lambda q, k: [hit("Install Guide")])

    assert ks.list_knowledge_sources() == ["catalogue"]
    assert ks.retrieve_from_sources("install")[0]["title"] == "Install Guide"

    assert ks.unregister_knowledge_source("catalogue") is True
    assert ks.list_knowledge_sources() == []
    assert ks.unregister_knowledge_source("catalogue") is False


def test_register_rejects_bad_arguments():
    with pytest.raises(ValueError):
        ks.register_knowledge_source("", lambda q, k: [])
    with pytest.raises(ValueError):
        ks.register_knowledge_source("nope", "not callable")


def test_sources_are_queried_in_registration_order():
    ks.register_knowledge_source("first", lambda q, k: [hit("A")])
    ks.register_knowledge_source("second", lambda q, k: [hit("B"), hit("C")])

    assert [h["title"] for h in ks.retrieve_from_sources("q")] == ["A", "B", "C"]


def test_query_and_top_k_reach_the_retriever():
    seen = {}

    def spy(query, top_k):
        seen["query"] = query
        seen["top_k"] = top_k
        return []

    ks.register_knowledge_source("spy", spy)
    ks.retrieve_from_sources("render presets", top_k=3)

    assert seen == {"query": "render presets", "top_k": 3}


def test_min_score_drops_weak_hits():
    ks.register_knowledge_source(
        "scored",
        lambda q, k: [hit("Strong", 0.9), hit("Weak", 0.4), hit("Edge", 0.6)],
        min_score=0.6,
    )

    assert [h["title"] for h in ks.retrieve_from_sources("q")] == ["Strong", "Edge"]


def test_without_min_score_every_hit_survives():
    ks.register_knowledge_source("scored", lambda q, k: [hit("Strong", 0.9), hit("Weak", 0.0)])

    assert [h["title"] for h in ks.retrieve_from_sources("q")] == ["Strong", "Weak"]


def test_a_failing_source_does_not_break_the_others():
    def boom(query, top_k):
        raise RuntimeError("source exploded")

    ks.register_knowledge_source("before", lambda q, k: [hit("A")])
    ks.register_knowledge_source("boom", boom)
    ks.register_knowledge_source("after", lambda q, k: [hit("B")])

    assert [h["title"] for h in ks.retrieve_from_sources("q")] == ["A", "B"]


def test_malformed_hits_are_dropped_and_missing_fields_defaulted():
    ks.register_knowledge_source(
        "sloppy",
        lambda q, k: [
            "not a dict",
            {"title": "No snippet"},
            {"snippet": "   "},
            {"snippet": "  untitled body  "},
            {"title": "Bad score", "snippet": "body", "score": "high"},
        ],
    )

    hits = ks.retrieve_from_sources("q")
    assert hits == [
        {"title": "sloppy", "snippet": "untitled body", "score": 0.0},
        {"title": "Bad score", "snippet": "body", "score": 0.0},
    ]


def test_a_source_returning_nothing_is_harmless():
    ks.register_knowledge_source("empty_list", lambda q, k: [])
    ks.register_knowledge_source("none", lambda q, k: None)

    assert ks.retrieve_from_sources("q") == []


@pytest.fixture
def engine():
    from backend.services.unified_chat_engine import UnifiedChatEngine

    return UnifiedChatEngine(tool_registry=None, llm_instance=None)


@pytest.fixture
def pgvector_results(monkeypatch):
    """Stub the pgvector search so no index or database is touched."""
    from backend.services import indexing_service

    results = []
    monkeypatch.setattr(
        indexing_service, "search_with_llamaindex", lambda query, **kw: list(results)
    )
    return results


def test_rag_output_is_unchanged_when_no_sources_are_registered(engine, pgvector_results):
    pgvector_results.append({"text": "quickstart spec", "metadata": {"source_filename": "spec.pdf"}})

    assert engine._retrieve_rag_context("q") == "[Source: spec.pdf]\nquickstart spec"


def test_rag_returns_empty_string_when_nothing_matches(engine, pgvector_results):
    assert engine._retrieve_rag_context("q") == ""


def test_rag_appends_source_hits_after_pgvector_chunks(engine, pgvector_results):
    pgvector_results.append({"text": "quickstart spec", "metadata": {"source_filename": "spec.pdf"}})
    ks.register_knowledge_source("catalogue", lambda q, k: [hit("Release Notes")])

    assert engine._retrieve_rag_context("q") == (
        "[Source: spec.pdf]\nquickstart spec\n\n"
        "[Source: Release Notes]\nRelease Notes body"
    )


def test_rag_returns_source_hits_even_when_pgvector_is_empty(engine, pgvector_results):
    ks.register_knowledge_source("catalogue", lambda q, k: [hit("Release Notes")])

    assert engine._retrieve_rag_context("q") == "[Source: Release Notes]\nRelease Notes body"


def test_rag_clips_source_snippets_like_pgvector_chunks(engine, pgvector_results):
    ks.register_knowledge_source("verbose", lambda q, k: [hit("Long", snippet="x" * 900)])

    assert engine._retrieve_rag_context("q") == "[Source: Long]\n" + "x" * 500


def _scored(src, text, score):
    return {"text": text, "metadata": {"source_filename": src}, "rerank_score": score}


def test_passages_below_the_rerank_floor_are_not_attached(engine, pgvector_results, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_RAG_MIN_RERANK_SCORE", "0.3")
    pgvector_results.extend([
        _scored("garden_notes.txt", "water tomatoes twice a week", 0.00002),
        _scored("bike_maintenance.txt", "lube the chain", 0.00001),
    ])

    assert engine._retrieve_rag_context("What is the capital of Australia?") == ""


def test_related_passages_survive_the_floor(engine, pgvector_results, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_RAG_MIN_RERANK_SCORE", "0.3")
    pgvector_results.extend([
        _scored("garden_notes.txt", "water tomatoes twice a week", 0.84),
        _scored("bike_maintenance.txt", "lube the chain", 0.00001),
        {"text": "stake by week three", "metadata": {"source_filename": "garden_notes.txt"}, "score": 0.0},
        {"text": "tyre pressure", "metadata": {"source_filename": "bike_maintenance.txt"}, "score": 0.0},
    ])

    assert engine._retrieve_rag_context("How often should I water the tomatoes?") == (
        "[Source: garden_notes.txt]\nwater tomatoes twice a week\n\n"
        "[Source: garden_notes.txt]\nstake by week three"
    )


def test_no_floor_without_reranker_scores(engine, pgvector_results, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_RAG_MIN_RERANK_SCORE", "0.3")
    pgvector_results.append({"text": "quickstart spec", "metadata": {"source_filename": "spec.pdf"}})

    assert engine._retrieve_rag_context("q") == "[Source: spec.pdf]\nquickstart spec"


def test_floor_zero_turns_the_filter_off(engine, pgvector_results, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_RAG_MIN_RERANK_SCORE", "0")
    pgvector_results.append(_scored("garden_notes.txt", "water tomatoes", 0.00002))

    assert engine._retrieve_rag_context("q") == "[Source: garden_notes.txt]\nwater tomatoes"


def test_measured_floor_applies_to_the_default_reranker(monkeypatch):
    from backend.utils import reranker

    monkeypatch.delenv("GUAARDVARK_RAG_MIN_RERANK_SCORE", raising=False)
    monkeypatch.delenv("GUAARDVARK_RERANK_MODEL", raising=False)
    assert reranker.relevance_floor() == reranker.RELEVANCE_FLOOR[reranker.DEFAULT_MODEL]
    monkeypatch.setenv("GUAARDVARK_RERANK_MODEL", "some/unmeasured-reranker")
    assert reranker.relevance_floor() is None
