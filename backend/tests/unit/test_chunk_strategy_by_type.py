"""Prose goes to the prose chunker and code to the code chunker, decided by the
file's type first. The text is examined only when the type is unknown, and then
only lines that are unmistakably code count: "let us", "return to" and a brace
in a sentence no longer send prose to the code chunker."""
from types import SimpleNamespace

import pytest

from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker, _looks_like_code

PROSE = """Let me explain where we are.
Let us return to the question of budgets {as discussed} before lunch.
We will return the draft on Friday; let the team know.
Class starts at nine, and the function of the meeting is to agree."""

PYTHON = """import os
from pathlib import Path


def load(path):
    data = Path(path).read_text()
    return data.strip()


class Loader:
    def __init__(self, root):
        self.root = root
"""

JS = """const fs = require('fs');
let count = 0;
function add(a, b) {
  return a + b;
}
if (count > 1) {
  console.log(count);
}
"""


def _doc(text, **metadata):
    return SimpleNamespace(metadata=metadata, get_content=lambda: text)


@pytest.fixture(scope="module")
def chunker():
    return EnhancedRAGChunker.__new__(EnhancedRAGChunker)


def test_prose_without_an_extension_is_prose(chunker):
    assert chunker._detect_best_chunking_strategy(_doc(PROSE, source_filename="NOTES")) == "adaptive"


@pytest.mark.parametrize("text", [PYTHON, JS, "#!/bin/sh\necho hi\n"])
def test_code_without_an_extension_is_code(chunker, text):
    assert chunker._detect_best_chunking_strategy(_doc(text, source_filename="script")) == "code"


def test_the_type_decides_before_the_text(chunker):
    # A markdown file full of code samples stays a markdown file.
    assert chunker._detect_best_chunking_strategy(_doc(PYTHON, file_extension=".md")) == "adaptive"
    # The extension is read from the file name when the loader did not record it.
    assert chunker._detect_best_chunking_strategy(_doc(PYTHON, file_path="/x/notes.txt")) == "adaptive"
    assert chunker._detect_best_chunking_strategy(_doc(PROSE, source_filename="a.py")) == "code"


def test_a_few_code_like_words_are_not_enough():
    assert _looks_like_code(PROSE) is False
    assert _looks_like_code("SELECT the best option.\nCREATE TABLE of contents.\n{note}") is False
