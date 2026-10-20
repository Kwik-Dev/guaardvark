"""The vector leg is skipped under VRAM pressure only when the embedder would have to load."""

import os

os.environ.setdefault("GUAARDVARK_MODE", "test")

import pytest

from backend.services import indexing_service as isvc
from backend.utils import ollama_resource_manager as orm


@pytest.mark.parametrize("entry,expected", [({"name": "embeddinggemma-2:latest"}, True), (None, False)])
def test_resident_embedder_is_read_from_ollama(monkeypatch, entry, expected):
    monkeypatch.setattr("backend.config.get_active_embedding_model", lambda: "embeddinggemma-2:latest")
    monkeypatch.setattr(orm, "resident_entry", lambda name, tags=None: entry)
    assert isvc._query_embedder_resident() is expected


def test_an_unreadable_ollama_counts_as_not_resident(monkeypatch):
    def boom(name, tags=None):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(orm, "resident_entry", boom)
    assert isvc._query_embedder_resident() is False
