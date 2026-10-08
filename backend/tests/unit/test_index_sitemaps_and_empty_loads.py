"""A sitemap indexes as one document per URL, and a file that yields no text is
an indexing failure with its reason, not an indexed document holding nothing.

Files are built in a temporary folder. The index, the progress system and the
lazy LlamaIndex loader are stubbed; nothing touches the network, the database
or a live index."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from llama_index.core import Document
from llama_index.core.readers import SimpleDirectoryReader

from backend.services import indexing_service as ix

SITEMAP = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<url><loc>https://example.com/</loc></url>"
    b"<url><loc>https://example.com/about</loc><lastmod>2026-01-01</lastmod></url>"
    b"</urlset>"
)
SITEMAP_INDEX = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<sitemap><loc>https://example.com/sitemap-posts.xml</loc></sitemap>"
    b"</sitemapindex>"
)
BINARY = b"\x7fELF\x02\x01\x01\x00" + bytes(range(256)) * 8


@pytest.fixture
def loaders(monkeypatch):
    """The LlamaIndex classes get_documents_from_file reads, bound without the
    lazy loader's model configuration."""
    monkeypatch.setattr(ix, "LlamaDocument", Document)
    monkeypatch.setattr(ix, "SimpleDirectoryReader", SimpleDirectoryReader)


def test_a_sitemap_yields_one_document_per_url(tmp_path, loaders):
    (tmp_path / "sitemap.xml").write_bytes(SITEMAP)
    docs = ix.get_documents_from_file(str(tmp_path / "sitemap.xml"))
    assert [d.text for d in docs] == ["https://example.com/", "https://example.com/about"]
    assert {d.metadata["content_type"] for d in docs} == {"sitemap_url"}


def test_a_sitemap_index_yields_the_sitemaps_it_lists(tmp_path, loaders):
    (tmp_path / "sitemap.xml").write_bytes(SITEMAP_INDEX)
    docs = ix.get_documents_from_file(str(tmp_path / "sitemap.xml"))
    assert [d.text for d in docs] == ["https://example.com/sitemap-posts.xml"]
    assert docs[0].metadata["content_type"] == "sitemap_index_url"


def test_other_xml_is_not_read_as_a_sitemap(tmp_path, loaders):
    (tmp_path / "data.xml").write_bytes(b"<root><item>alpha</item><item>beta</item></root>")
    docs = ix.get_documents_from_file(str(tmp_path / "data.xml"))
    assert len(docs) == 1 and "alpha beta" in docs[0].text
    assert docs[0].metadata.get("content_type") != "sitemap_url"


def test_a_binary_file_of_an_unknown_type_loads_nothing(tmp_path, loaders):
    (tmp_path / "blob.bin").write_bytes(BINARY)
    assert ix.get_documents_from_file(str(tmp_path / "blob.bin")) == []


def test_text_in_an_unknown_type_still_loads(tmp_path, loaders):
    (tmp_path / "notes.foo").write_text("plain words in an unusual extension")
    docs = ix.get_documents_from_file(str(tmp_path / "notes.foo"))
    assert docs and "plain words" in docs[0].text


def test_a_file_with_nothing_to_index_fails_with_its_reason(tmp_path, loaders, monkeypatch):
    (tmp_path / "blob.bin").write_bytes(BINARY)
    stub_index = MagicMock()
    monkeypatch.setattr(ix, "_lazy_load_llamaindex", lambda: None)
    monkeypatch.setattr(ix, "_lazy_load_optional_components", lambda: None)
    monkeypatch.setattr(ix, "get_or_create_index", lambda *a, **k: None)
    monkeypatch.setattr(ix, "index", stub_index)
    monkeypatch.setattr(ix, "storage_context", MagicMock())
    monkeypatch.setattr(ix, "vector_store_fallback_reason", lambda: None)
    monkeypatch.setattr(ix, "get_unified_progress", lambda: MagicMock())
    doc = SimpleNamespace(id=4242, filename="blob.bin", project_id=None, project=None,
                          uploaded_at=None, tags=None, notes=None)

    assert ix.add_file_to_index(str(tmp_path / "blob.bin"), doc) is False
    assert ix.no_content_reason(4242) == "nothing to index: no text could be read from blob.bin"
    stub_index.insert_nodes.assert_not_called()
