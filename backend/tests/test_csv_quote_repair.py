"""Quotes in model-written CSV rows: malformed wrapping is repaired, correctly
escaped CSV is left exactly as written. Pure parsing, no model call.
"""

import csv
import io

import pytest

from backend.tools.content_tools import _csv_row, _undoubled
from backend.utils.bulk_csv_generator import BulkCSVGenerator, GenerationTask

BODY = "<p>" + " ".join(["word"] * 60) + "</p>"
EXCERPT = "An excerpt that is long enough to pass the fifty character check."


def _bulk_row(title, category='"Home Care"', content=BODY):
    """Parse one bulk reply whose title and category are given as raw CSV text."""
    generator = object.__new__(BulkCSVGenerator)
    task = GenerationTask(item_id="site-1", topic="Support Planning", client="C",
                          project="P", website="w.example")
    reply = (f'"1",{title},"{content}","{EXCERPT}",{category},'
             '"tag one, tag two, tag three","support-planning"')
    return generator._parse_csv_response(reply, task)


# --------------------------------------------------------------------------
# Bulk job parser
# --------------------------------------------------------------------------
@pytest.mark.parametrize("raw_title,expected", [
    ('""Support Planning""', "Support Planning"),
    ('""Best "Tips" today""', 'Best "Tips" today'),
])
def test_a_raw_doubled_field_loses_its_stray_quotes(raw_title, expected):
    assert _bulk_row(raw_title).title == expected


def test_raw_doubled_category_is_cleaned_too():
    row = _bulk_row('""Support Planning""', category='""Home Care""')
    assert (row.title, row.category) == ("Support Planning", "Home Care")


def test_a_raw_doubled_title_does_not_disturb_escaped_html():
    content = '<p class=""lead"">' + " ".join(["word"] * 60) + "</p>"
    row = _bulk_row('""Support Planning""', content=content)
    assert row.title == "Support Planning"
    assert row.content.startswith('<p class="lead">')


@pytest.mark.parametrize("raw_title,expected", [
    ('"Support Planning"', "Support Planning"),
    ('"""Support Planning"""', "Support Planning"),
    ('"Best ""Tips"" today"', 'Best "Tips" today'),
    ('"Say ""hi"""', 'Say "hi"'),
    ('"Size 5"""""', 'Size 5""'),
    ('Gutter size 5"', 'Gutter size 5"'),
])
def test_correctly_escaped_fields_are_kept_as_written(raw_title, expected):
    assert _bulk_row(raw_title).title == expected


# --------------------------------------------------------------------------
# Single-page tools
# --------------------------------------------------------------------------
def _fields(reply):
    row = _csv_row(reply, 6, 7)
    return next(csv.reader(io.StringIO(row))) if row else None


@pytest.mark.parametrize("reply", [
    # Escaped HTML with a bare quote in the title, then in the meta description.
    '"1","The "Best" Lawn","<p class=""lead"">Mow high.</p>","Meta desc","lawn, mow","best-lawn"',
    '"1","Lawn","<p class=""lead"">Mow high.</p>","Say "hi" now","lawn, mow","best-lawn"',
    # One style throughout.
    '"1","The ""Best"" Lawn","<p class=""lead"">Mow high.</p>","Meta desc","lawn, mow","best-lawn"',
    '"1","The "Best" Lawn","<p class="lead">Mow high.</p>","Meta desc","lawn, mow","best-lawn"',
])
def test_html_attribute_quotes_come_out_single_whatever_the_mix(reply):
    assert _fields(reply)[2] == '<p class="lead">Mow high.</p>'


@pytest.mark.parametrize("html", [
    '<p><img alt="" src="a.png"> Mow high.</p>',
    '<p><img alt=""> Mow high.</p>',
])
def test_a_bare_empty_attribute_is_not_taken_for_an_escaped_quote(html):
    reply = f'"1","The "Best" Lawn","{html}","Meta desc","lawn, mow","best-lawn"'
    assert _fields(reply)[2] == html


@pytest.mark.parametrize("field,expected", [
    ('<a href=""/lawn"">Mow</a>', '<a href="/lawn">Mow</a>'),
    ('alt="""" src=""a.png""', 'alt="" src="a.png"'),
    ('He said ""hi""', 'He said "hi"'),
    ('<img alt="" />', '<img alt="" />'),
    ('<br class=""/>', '<br class=""/>'),
    ('He said "hi"', 'He said "hi"'),
    ("plain", "plain"),
])
def test_undoubled(field, expected):
    assert _undoubled(field) == expected
