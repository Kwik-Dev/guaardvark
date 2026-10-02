"""Passages with no heading and no page are readable through the label the
outline gives them, and a document can be read whole.

A stand-in for Postgres applies the WHERE clauses the tools send to a small
passage table, so the SQL is exercised rather than only inspected.
"""

import re

import pytest

from backend.tools import knowledge_tools
from backend.tools.knowledge_tools import (
    NO_SECTION,
    DocumentOutlineTool,
    ReadDocumentSectionTool,
)

# One row per indexed passage: (id, source_filename, heading_path, page_label, text).
# None stands for a key that is missing from the metadata.
PASSAGES = [
    (1, "bike_maintenance.txt", None, None, "Chain: clean and lube every 200 miles."),
    (2, "guide.md", None, None, "Preamble before any heading."),
    (3, "guide.md", "Setup", None, "Install the thing."),
    (4, "guide.md", "Setup > Install", None, "Run the installer."),
    (5, "manual.pdf", None, "1", "Cover page."),
    (6, "manual.pdf", "Safety", "2", "Wear gloves."),
    (7, "manual.pdf", "", "", "Colophon with empty metadata."),
]


def _matches(row, clauses, params):
    """Evaluate the handful of clause shapes knowledge_tools emits."""
    _, src, heading, page, _ = row
    values = iter(params)
    for clause in clauses:
        clause = clause.strip()
        if clause == "metadata_->>'source_filename' = %s":
            if src != next(values):
                return False
        elif clause == "coalesce(metadata_->>'heading_path', '') = ''":
            if heading:
                return False
        elif clause == "coalesce(metadata_->>'page_label', '') = ''":
            if page:
                return False
        elif clause == "metadata_->>'heading_path' ILIKE %s ESCAPE '\\'":
            needle = next(values).strip("%").replace("\\", "").lower()
            if heading is None or needle not in heading.lower():
                return False
        elif clause == "metadata_->>'page_label' = %s":
            if page != next(values):
                return False
        else:
            raise AssertionError(f"unexpected clause: {clause}")
    return True


@pytest.fixture
def db(monkeypatch):
    seen = []

    def fake_query(sql, params):
        seen.append((sql, params))
        where = re.search(r"WHERE (.*?)(?: ORDER BY| GROUP BY|$)", sql, re.S).group(1)
        clauses = where.split(" AND ")
        if "GROUP BY 1 ORDER BY 3 DESC" in sql:  # which files carry this name
            count = sum(1 for row in PASSAGES if _matches(row, clauses, params))
            return ([("", None, count)] if count else []), None
        if "GROUP BY 1, 2" in sql:  # the outline
            groups = {}
            for row in PASSAGES:
                if _matches(row, clauses, params):
                    key = (row[2] or "", row[3] or "")
                    groups.setdefault(key, [row[0], 0])
                    groups[key][1] += 1
            ordered = sorted(groups.items(), key=lambda kv: kv[1][0])
            return [(h, p, n) for (h, p), (_, n) in ordered], None
        n_filter = sql.count("%s") - (1 if "OFFSET %s" in sql else 0)
        rows = [r for r in PASSAGES if _matches(r, clauses, params[:n_filter])]
        if sql.startswith("SELECT count(*)"):
            return [(len(rows),)], None
        offset = params[-1]
        return [
            (text, {"heading_path": h, "page_label": p} if h is not None or p is not None else {})
            for _, _, h, p, text in rows[offset:offset + 25]
        ], None

    monkeypatch.setattr(knowledge_tools, "_table", lambda: ("data_vec_768", None))
    monkeypatch.setattr(knowledge_tools, "_query", fake_query)
    return seen


def test_outline_labels_headingless_pageless_passages_no_section(db):
    result = DocumentOutlineTool().execute(source_filename="bike_maintenance.txt")
    assert result.success
    assert f"  {NO_SECTION} — 1 passage(s)" in result.output


def test_no_section_label_from_the_outline_reads_those_passages(db):
    result = ReadDocumentSectionTool().execute(
        source_filename="bike_maintenance.txt", heading_path=NO_SECTION)
    assert result.success, result.error
    assert "Chain: clean and lube" in result.output
    assert "passages 1-1 of 1" in result.output
    assert f"[1] {NO_SECTION}" in result.output


def test_no_section_is_matched_whatever_the_case_and_spacing(db):
    result = ReadDocumentSectionTool().execute(
        source_filename="bike_maintenance.txt", heading_path="  (No Section) ")
    assert "Chain: clean and lube" in result.output


def test_no_section_reads_exactly_the_outline_group(db):
    """guide.md's outline lists one '(no section)' passage; reading it gives that one."""
    outline = DocumentOutlineTool().execute(source_filename="guide.md").output
    assert f"{NO_SECTION} — 1 passage(s)" in outline

    result = ReadDocumentSectionTool().execute(source_filename="guide.md", heading_path=NO_SECTION)
    assert "Preamble before any heading." in result.output
    assert "Install the thing." not in result.output
    assert "of 1" in result.output


def test_no_section_leaves_out_headingless_passages_that_have_a_page(db):
    """manual.pdf page 1 has no heading; the outline calls it 'page 1', not '(no section)'."""
    outline = DocumentOutlineTool().execute(source_filename="manual.pdf").output
    assert "page 1 — 1 passage(s)" in outline

    result = ReadDocumentSectionTool().execute(source_filename="manual.pdf", heading_path=NO_SECTION)
    assert "Colophon with empty metadata." in result.output
    assert "Cover page." not in result.output

    by_page = ReadDocumentSectionTool().execute(source_filename="manual.pdf", page_label="1")
    assert "Cover page." in by_page.output
    assert "[1] page 1" in by_page.output


def test_no_section_with_a_page_narrows_to_that_page(db):
    result = ReadDocumentSectionTool().execute(
        source_filename="manual.pdf", heading_path=NO_SECTION, page_label="1")
    assert "Cover page." in result.output
    assert "Wear gloves." not in result.output


def test_every_outline_line_reads_back(db):
    """Each label the outline prints selects at least one passage."""
    for src in ("bike_maintenance.txt", "guide.md", "manual.pdf"):
        outline = DocumentOutlineTool().execute(source_filename=src).output
        for line in outline.splitlines()[1:]:
            label = line.strip().rsplit(" — ", 1)[0]
            if label.startswith("page "):
                kwargs = {"page_label": label[len("page "):]}
            else:
                heading, _, page = label.partition(" p.")
                kwargs = {"heading_path": heading}
                if page:
                    kwargs["page_label"] = page
            result = ReadDocumentSectionTool().execute(source_filename=src, **kwargs)
            assert "No passages match" not in result.output, (src, label)


def test_headed_sections_still_match_by_substring(db):
    result = ReadDocumentSectionTool().execute(source_filename="guide.md", heading_path="setup")
    assert "Install the thing." in result.output
    assert "Run the installer." in result.output
    assert "Preamble" not in result.output
    assert "section ~ setup" in result.output


def test_no_selector_reads_the_whole_document_in_order(db):
    result = ReadDocumentSectionTool().execute(source_filename="guide.md")
    assert result.success, result.error
    assert "whole document" in result.output
    assert "passages 1-3 of 3" in result.output
    out = result.output
    assert out.index("Preamble") < out.index("Install the thing.") < out.index("Run the installer.")


def test_no_selector_on_an_unknown_file_says_so(db):
    result = ReadDocumentSectionTool().execute(source_filename="missing.txt")
    assert result.success
    assert "No indexed content for 'missing.txt'" in result.output


def test_no_section_sql_never_uses_ilike(db):
    ReadDocumentSectionTool().execute(source_filename="bike_maintenance.txt", heading_path=NO_SECTION)
    # db[0] asks which files carry the name; the read is the query after it.
    sql, params = db[1]
    assert "ILIKE" not in sql
    assert "coalesce(metadata_->>'heading_path', '') = ''" in sql
    assert params[0] == "bike_maintenance.txt"
