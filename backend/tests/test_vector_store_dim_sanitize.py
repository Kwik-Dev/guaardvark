"""Regression: the vector store must self-heal mixed-dimension contamination.

2026-06-03: a model switch (bge-m3 1024 -> qwen3-embedding 2560) left 4 stale
1024-dim vectors in a 2560-dim store. SimpleVectorStore.query() ->
np.array(embeddings) raised "inhomogeneous shape" and killed the whole vector
leg of hybrid search (RAG silently degraded to nothing). The dimension-lock
checks model NAME, not per-vector dims, so it missed this.
"""
from types import SimpleNamespace

from backend.services.indexing_service import _sanitize_vector_store_dimensions


def _ctx(embedding_dict):
    data = SimpleNamespace(
        embedding_dict=dict(embedding_dict),
        text_id_to_ref_doc_id={k: "ref" for k in embedding_dict},
        metadata_dict={k: {} for k in embedding_dict},
    )
    store = SimpleNamespace(data=data)
    return SimpleNamespace(vector_store=store), data


def test_prunes_minority_dimension_vectors():
    ctx, data = _ctx({
        "a": [0.1] * 2560,
        "b": [0.2] * 2560,
        "bad1": [0.3] * 1024,   # stale-model leftovers
        "bad2": [0.4] * 1024,
    })
    removed = _sanitize_vector_store_dimensions(ctx, persist_dir=None)
    assert removed == 2
    assert set(data.embedding_dict) == {"a", "b"}
    # pruned from ALL three parallel dicts, not just embeddings
    assert "bad1" not in data.text_id_to_ref_doc_id
    assert "bad2" not in data.metadata_dict


def test_noop_on_homogeneous_store():
    ctx, data = _ctx({"a": [0.1] * 2560, "b": [0.2] * 2560})
    assert _sanitize_vector_store_dimensions(ctx, persist_dir=None) == 0
    assert len(data.embedding_dict) == 2


def test_sanitizer_never_raises_on_garbage_input():
    # Must be non-fatal: a bad storage object can't be allowed to block index load.
    assert _sanitize_vector_store_dimensions(SimpleNamespace(), persist_dir=None) == 0
    assert _sanitize_vector_store_dimensions(None, persist_dir=None) == 0
