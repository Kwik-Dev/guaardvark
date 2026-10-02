"""generate_csv and generate_file check the model's reply before reporting success.

A stand-in LLM returns a fixed reply, so nothing here calls a model. Output goes
to a temporary folder.
"""

import sys
import types
from types import SimpleNamespace

import pytest

from backend.tools.generation_tools import (
    CSVGeneratorTool,
    FileGeneratorTool,
    _csv_table,
    _file_body,
)

TABLE = [["name", "price"], ["apple", "1"], ["pear", "2"], ["plum", "3"]]
CLEAN = "name,price\napple,1\npear,2\nplum,3"


class _LLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return SimpleNamespace(message=SimpleNamespace(content=self.reply))


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    """A temporary outputs folder, and message types that need no LLM library."""
    monkeypatch.setattr("backend.config.OUTPUT_DIR", str(tmp_path))
    fake = types.ModuleType("backend.utils.llm_service")
    fake.ChatMessage = lambda role, content: SimpleNamespace(role=role, content=content)
    fake.MessageRole = SimpleNamespace(USER="user")
    monkeypatch.setitem(sys.modules, "backend.utils.llm_service", fake)
    return tmp_path


def _csv(reply, **arguments):
    tool = CSVGeneratorTool()
    tool._llm = _LLM(reply)
    call = {"filename": "fruit.csv", "data_description": "three fruits with a price", "row_count": 3}
    call.update(arguments)
    return tool, tool.execute(**call)


def _file(reply, **arguments):
    tool = FileGeneratorTool()
    tool._llm = _LLM(reply)
    call = {"filename": "notes.md", "content_description": "A meeting notes template"}
    call.update(arguments)
    return tool, tool.execute(**call)


# --------------------------------------------------------------------------
# The table inside a reply
# --------------------------------------------------------------------------
@pytest.mark.parametrize("reply", [
    CLEAN,
    "```csv\n" + CLEAN + "\n```",
    "Here is your CSV:\n```csv\n" + CLEAN + "\n```\nLet me know if you need more!",
    "Sure, here is your CSV:\n\n" + CLEAN + "\n\nLet me know if you need more!",
    "Here is your CSV:\n" + CLEAN,
    CLEAN + "\n```",
    '"name", "price"\n"apple", "1"\n"pear", "2"\n"plum", "3"',
])
def test_the_table_is_found_without_the_text_and_fences_around_it(reply):
    rows, width, problem = _csv_table(reply)
    assert problem is None
    assert (rows, width) == (TABLE, 2)


def test_a_quoted_field_with_a_line_break_is_one_row():
    rows, width, problem = _csv_table('name,notes\napple,"crisp\nand red"\npear,soft')
    assert problem is None
    assert rows == [["name", "notes"], ["apple", "crisp\nand red"], ["pear", "soft"]]


@pytest.mark.parametrize("reply,expected", [
    ("name,price\napple,1\npear,2,extra\nplum,3", "row 3 has 3 column(s) where most rows have 2"),
    ('name,notes\napple,"He said "hi""\npear,"x"', "not valid CSV"),
    ('Here it is:\n\nname,notes\napple,"He said "hi""', "not valid CSV"),
    ("```csv\n```", "holds no rows"),
])
def test_a_reply_that_is_not_one_table_names_the_problem(reply, expected):
    _rows, _width, problem = _csv_table(reply)
    assert problem and expected in problem


# --------------------------------------------------------------------------
# generate_csv
# --------------------------------------------------------------------------
def test_generate_csv_writes_the_parsed_table_and_counts_data_rows(outputs):
    _tool, result = _csv("Here is your CSV:\n```csv\n" + CLEAN + "\n```\nLet me know!")

    assert result.success, result.error
    assert (outputs / "csv" / "fruit.csv").read_text() == CLEAN + "\n"
    assert result.output["row_count"] == 3
    assert result.output["rows_requested"] == 3
    assert result.output["column_count"] == 2
    assert result.output["columns"] == ["name", "price"]
    assert "note" not in result.output


def test_generate_csv_reports_a_short_table(outputs):
    _tool, result = _csv("name,price\napple,1", row_count=10)

    assert result.success, result.error
    assert result.output["row_count"] == 1
    assert "1 data row(s), not the 10 asked for" in result.output["note"]


def test_generate_csv_without_headers_counts_every_row(outputs):
    _tool, result = _csv("apple,1\npear,2\nplum,3", include_headers=False)

    assert result.success, result.error
    assert result.output["row_count"] == 3
    assert "columns" not in result.output


@pytest.mark.parametrize("reply,expected", [
    ("", "empty reply"),
    ("I'm sorry, but I can't help with that.", "single row"),
    ("name,price\napple,1\npear,2,extra\nplum,3", "row 3 has 3 column(s)"),
    ("```csv\n```", "holds no rows"),
])
def test_generate_csv_fails_and_writes_nothing_on_an_unusable_reply(outputs, reply, expected):
    _tool, result = _csv(reply)

    assert result.success is False
    assert expected in result.error
    assert not (outputs / "csv" / "fruit.csv").exists()


@pytest.mark.parametrize("arguments,expected", [
    ({"filename": ""}, "filename is required"),
    ({"filename": "../../x.csv"}, "relative path inside the csv outputs folder"),
    ({"filename": "/etc/x.csv"}, "relative path inside the csv outputs folder"),
    ({"filename": "sub/"}, "relative path inside the csv outputs folder"),
    ({"filename": "x.xlsx"}, "ending in .csv"),
    ({"filename": ".hidden.csv"}, "must not start with a dot"),
    ({"data_description": " "}, "data_description is required"),
    ({"row_count": 0}, "row_count must be 1 or more"),
    ({"row_count": "many"}, "row_count must be a whole number"),
])
def test_generate_csv_refuses_bad_arguments_before_the_model_runs(outputs, arguments, expected):
    tool, result = _csv(CLEAN, **arguments)

    assert result.success is False
    assert expected in result.error
    assert tool._llm.calls == 0
    assert not (outputs / "csv").exists()


def test_generate_csv_creates_folders_and_adds_the_extension(outputs):
    _tool, nested = _csv(CLEAN, filename="reports/q3/fruit.csv")
    _tool, bare = _csv(CLEAN, filename="fruit")

    assert nested.success, nested.error
    assert nested.output["filename"] == "reports/q3/fruit.csv"
    assert (outputs / "csv" / "reports" / "q3" / "fruit.csv").is_file()
    assert bare.success, bare.error
    assert bare.output["filename"] == "fruit.csv"
    assert (outputs / "csv" / "fruit.csv").is_file()


# --------------------------------------------------------------------------
# generate_file
# --------------------------------------------------------------------------
@pytest.mark.parametrize("filename,reply,expected", [
    # Markdown and text: a wrapper is removed, the file's own code blocks stay.
    ("notes.md", "# Notes", "# Notes"),
    ("notes.md", "```\n# Notes\n```", "# Notes"),
    ("notes.md", "```\n# Notes", "# Notes"),
    ("notes.md", "Sure! Here is the file:\n```markdown\n# Notes\n```", "# Notes"),
    ("notes.md", "```markdown\n# Notes\n\n```bash\nls\n```\n```\nLet me know!", "# Notes\n\n```bash\nls\n```"),
    ("notes.md", "# Notes\n\nRun:\n```bash\nls\n```\nDone.", "# Notes\n\nRun:\n```bash\nls\n```\nDone."),
    ("notes.md", "```bash\nls\n```\nThat lists files.", "```bash\nls\n```\nThat lists files."),
    ("steps.txt", "Steps:\n```\nls\n```", "Steps:\n```\nls\n```"),
    # Code and data: the first fenced block is the file.
    ("hi.py", "print('hi')", "print('hi')"),
    ("hi.py", "```python\nprint('hi')\n```", "print('hi')"),
    ("hi.py", "Here you go:\n```python\nprint('hi')\n```\nThis prints hi.", "print('hi')"),
    ("hi.py", "```python\nprint('hi')\n```\nThis prints hi.", "print('hi')"),
    ("hi.py", "```python\nprint('hi')", "print('hi')"),
    ("hi.py", "print('hi')\n```", "print('hi')"),
    ("hi.py", "```python\nprint('hi')\n```\nUsage:\n```bash\npython hi.py\n```", "print('hi')"),
    ("a.json", 'Here is the JSON:\n```json\n{"a": 1}\n```', '{"a": 1}'),
])
def test_file_body_drops_a_wrapping_fence_and_the_text_around_it(filename, reply, expected):
    assert _file_body(reply, filename) == expected


@pytest.mark.parametrize("reply,expected", [
    ("", "empty reply"),
    ("```markdown\n```", "no file content"),
])
def test_generate_file_fails_and_writes_nothing_on_an_empty_reply(outputs, reply, expected):
    _tool, result = _file(reply)

    assert result.success is False
    assert expected in result.error
    assert not (outputs / "files" / "notes.md").exists()


def test_generate_file_saves_the_content_without_the_wrapper(outputs):
    _tool, result = _file("Sure! Here is the file:\n```markdown\n# Notes\n```")

    assert result.success, result.error
    assert result.output["content"] == "# Notes"
    assert (outputs / "files" / "notes.md").read_text() == "# Notes"
