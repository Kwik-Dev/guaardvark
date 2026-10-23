"""Code-file vectors carry the same source id as prose, so re-index and delete
remove them.

PGVectorStore writes each row's ``metadata_->>'document_id'`` from the node's
source (LlamaIndex ``node_to_metadata_dict``: ``node.ref_doc_id or "None"``),
and purge_document_vectors deletes ``doc_<db id>_%``. Code chunks used to hang
off a throwaway document (AST) or none (line-based), so their rows read a random
id or "None" and were never removed.
"""
import re

import pytest

pytest.importorskip("llama_index.core")

from llama_index.core import Document
from llama_index.core.vector_stores.utils import node_to_metadata_dict

from backend.utils.code_chunker import CodeAwareChunker, bind_chunks_to_document

CODE = "\n".join(f"def f{i}(x):\n    return x + {i}\n" for i in range(80))
PURGE_LIKE = re.compile(r"^doc_42_.*")  # what `LIKE 'doc\_42\_%'` matches


def _document(text):
    import hashlib
    doc = Document(text=text, metadata={"document_id": "42"})
    doc.id_ = f"doc_42_{hashlib.sha256(text.encode()).hexdigest()[:16]}"
    return doc


@pytest.mark.parametrize("language", ["python", "kotlin"])  # AST, and the line-based fallback
def test_every_code_chunk_is_stored_under_its_document(language):
    doc = _document(CODE)
    nodes = bind_chunks_to_document(CodeAwareChunker(chunk_lines=20).chunk_code(CODE, language, "/x/a.py"), doc)
    assert len(nodes) > 1
    for node in nodes:
        stored = node_to_metadata_dict(node, remove_text=True)
        assert stored["document_id"] == doc.id_
        assert PURGE_LIKE.match(stored["document_id"])
        assert node.node_id.startswith(doc.id_ + "_")


def test_unbound_chunks_are_what_the_purge_missed():
    """The defect, kept visible: without binding, the stored id never matches."""
    nodes = CodeAwareChunker(chunk_lines=20).chunk_code(CODE, "kotlin", "/x/a.kt")
    assert all(not PURGE_LIKE.match(node_to_metadata_dict(n)["document_id"]) for n in nodes)


def test_reindexing_the_same_file_gives_the_same_ids():
    a = bind_chunks_to_document(CodeAwareChunker().chunk_code(CODE, "kotlin", "/x"), _document(CODE))
    b = bind_chunks_to_document(CodeAwareChunker().chunk_code(CODE, "kotlin", "/x"), _document(CODE))
    assert [n.node_id for n in a] == [n.node_id for n in b]
