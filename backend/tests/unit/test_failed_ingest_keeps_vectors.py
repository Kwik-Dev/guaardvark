"""A document that yields no text, or no chunks, or a part that will not chunk,
fails with its reason; and a re-index removes the previous vectors only once
the new ones are in, so a failed one leaves the document searchable as before.

The loaders, the chunker, the index and the vector table are stand-ins (plain
objects recording what was asked of them); the order and the decisions in
add_file_to_index are real.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.services import indexing_service as ix
from backend.utils import enhanced_rag_chunking as erc


class Node(SimpleNamespace):
    @property
    def node_id(self):
        return self.id_


class Doc(SimpleNamespace):
    def __init__(self, text, **metadata):
        super().__init__(text=text, metadata=dict(metadata), id_=None)


@pytest.fixture
def ingest(tmp_path, monkeypatch):
    """add_file_to_index with every outside part recorded in ``calls``."""
    calls = []
    state = {"docs": [Doc("A paragraph of real text.")], "nodes": None, "chunk_error": None,
             "insert_error": None, "watermark": 100}

    class Index:
        def insert_nodes(self, nodes):
            calls.append(("insert", [n.id_ for n in nodes]))
            if state["insert_error"]:
                raise state["insert_error"]

    class Chunker:
        def chunk_documents(self, documents, strategy_name="auto", errors=None):
            if state["chunk_error"] and errors is not None:
                errors.append(state["chunk_error"])
            if state["nodes"] is not None:
                return state["nodes"]
            return [Node(id_=f"{d.id_}_0001", text=d.text, metadata={}) for d in documents]

        def get_chunking_stats(self):
            return {}

    monkeypatch.setattr(ix, "_lazy_load_llamaindex", lambda: None)
    monkeypatch.setattr(ix, "_lazy_load_optional_components", lambda: None)
    monkeypatch.setattr(ix, "get_or_create_index", lambda *a, **k: None)
    monkeypatch.setattr(ix, "index", Index())
    monkeypatch.setattr(ix, "storage_context", MagicMock(persist_dir="/tmp/idx"))
    monkeypatch.setattr(ix, "_safe_persist", lambda *a, **k: None)
    monkeypatch.setattr(ix, "vector_store_fallback_reason", lambda: None)
    monkeypatch.setattr(ix, "get_unified_progress", lambda: MagicMock())
    monkeypatch.setattr(ix, "_record_index_embedding_model", lambda *a, **k: None)
    monkeypatch.setattr(ix, "_vector_backend", lambda: "pgvector")
    monkeypatch.setattr(ix, "get_documents_from_file", lambda **kw: list(state["docs"]))
    monkeypatch.setattr(erc, "get_shared_chunker", lambda: Chunker())
    monkeypatch.setattr("backend.utils.contextual_prepender.prepend_context_to_document_nodes",
                        lambda nodes: 0, raising=False)
    monkeypatch.setattr(ix, "vector_row_watermark",
                        lambda *a, **k: calls.append(("watermark",)) or state["watermark"])
    monkeypatch.setattr(ix, "purge_document_vectors",
                        lambda document_id, project_id=None, profile=None, up_to_row=None:
                        calls.append(("purge", document_id, up_to_row)) or ix.PurgeResult(2))
    path = tmp_path / "notes.txt"
    path.write_text("A paragraph of real text.")
    doc = SimpleNamespace(id=77, filename="notes.txt", project_id=None, project=None, folder=None,
                          uploaded_at=None, tags=None, notes=None, file_metadata=None)
    return SimpleNamespace(run=lambda: ix.add_file_to_index(str(path), doc), calls=calls, state=state)


def test_a_reindex_removes_the_previous_copy_after_the_new_one_is_in(ingest):
    assert ingest.run() is True
    kinds = [c[0] for c in ingest.calls]
    assert kinds == ["watermark", "insert", "purge"]
    assert ingest.calls[-1] == ("purge", 77, 100)  # rows up to the watermark: the old copy only


def test_a_failed_insert_keeps_the_previous_copy(ingest):
    ingest.state["insert_error"] = RuntimeError("embedding server went away")
    assert ingest.run() is False
    assert "purge" not in [c[0] for c in ingest.calls]


def test_without_a_watermark_the_old_copy_goes_first_as_before(ingest):
    ingest.state["watermark"] = None
    assert ingest.run() is True
    assert [c[0] for c in ingest.calls] == ["watermark", "purge", "insert"]
    assert ingest.calls[1] == ("purge", 77, None)


def test_a_loader_placeholder_is_not_indexed_and_the_failure_says_why(ingest):
    ingest.state["docs"] = [Doc("Image file: scan.png (no text content extracted)",
                                extraction_error="vision model not installed")]
    assert ingest.run() is False
    assert ix.no_content_reason(77) == (
        "nothing to index: no text could be read from notes.txt (vision model not installed)")
    assert ingest.calls == []  # no insert, no purge: the previous vectors stay


def test_placeholders_beside_real_text_are_dropped(ingest):
    ingest.state["docs"] = [Doc("File: x.bin (no text content extracted)"), Doc("Real words.")]
    assert ingest.run() is True
    inserted = next(c[1] for c in ingest.calls if c[0] == "insert")
    assert len(inserted) == 1


def test_no_chunks_is_a_failure_not_an_empty_success(ingest):
    ingest.state["nodes"] = []
    assert ingest.run() is False
    assert ix.no_content_reason(77) == "nothing to index: notes.txt produced no chunks"
    assert "purge" not in [c[0] for c in ingest.calls]


def test_a_part_that_will_not_chunk_fails_the_ingest(ingest):
    ingest.state["chunk_error"] = "ValueError: metadata longer than chunk size"
    assert ingest.run() is False
    assert ix.no_content_reason(77).startswith("chunking failed for 1 of 1 part(s) of notes.txt")
    assert ingest.calls == []


@pytest.mark.parametrize("text,placeholder", [
    ("Image file: a.png (no text content extracted)", True),
    ("Excel file: b.xlsx (no text content extracted)", True),
    ("Code file: c.py (no text content extracted)", True),
    ("File: d.bin (no text content extracted)", True),
    ("Image file: e.png (no text content detected through OCR)", True),
    ("Notes about files (no text content extracted) from the scanner.", False),
])
def test_placeholder_text_is_recognised(text, placeholder):
    assert ix.is_placeholder_document(Doc(text)) is placeholder


def test_the_fallback_indexer_never_reports_a_non_code_file_indexed(tmp_path):
    from backend import celery_tasks_isolated as cti
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4")
    assert cti.simple_index_document(str(path), 1) is False
