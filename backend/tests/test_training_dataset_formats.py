"""One dataset reader for the inspect panel, the job checks and the trainer:
folders are searched recursively, blank and broken lines are skipped and
named, every supported row shape becomes a conversation ending on an answer,
and an inspect says what the trainer will train on without quoting the file.

Pure file reading on tmp_path; no database, no network."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.training.scripts import dataset_formats as df


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n")
    return path


ALPACA_ROW = {"instruction": "Name a colour.", "output": "Blue."}
MESSAGES = {"messages": [{"role": "system", "content": "Be brief."},
                         {"role": "user", "content": "Hi?"},
                         {"role": "assistant", "content": "Hello."}]}
SHAREGPT = {"conversations": [{"from": "human", "value": "Two plus two?"}, {"from": "gpt", "value": "Four."}]}
PROMPT_COMPLETION = {"prompt": "Capital of France?", "completion": "Paris."}
TEXT = {"text": "A plain paragraph of text."}


# ---- which files ---------------------------------------------------------------

def test_a_folder_is_searched_recursively_and_hidden_entries_are_skipped(tmp_path):
    _jsonl(tmp_path / "set" / "b.jsonl", [ALPACA_ROW])
    _jsonl(tmp_path / "set" / "a" / "deep.json", [])
    _jsonl(tmp_path / "set" / ".hidden.jsonl", [ALPACA_ROW])
    _jsonl(tmp_path / "set" / ".cache" / "x.jsonl", [ALPACA_ROW])
    (tmp_path / "set" / "notes.txt").write_text("not data")

    files, reason = df.list_files(str(tmp_path / "set"))

    assert reason is None
    assert files == [str(tmp_path / "set" / "a" / "deep.json"), str(tmp_path / "set" / "b.jsonl")]


@pytest.mark.parametrize("path, says", [
    ("https://example.com/data.jsonl", "is a URL"),
    ("", "has no path"),
    (None, "must be text"),
])
def test_paths_the_trainer_cannot_read_are_refused_with_a_reason(path, says):
    files, reason = df.list_files(path)
    assert files == [] and says in reason


def test_a_missing_path_an_empty_folder_and_a_wrong_file_type_say_why(tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "notes.csv").write_text("a,b")
    assert "does not exist" in df.list_files(str(tmp_path / "nope"))[1]
    assert "holds no .jsonl or .json file" in df.list_files(str(tmp_path / "empty"))[1]
    assert "is not a .jsonl or .json file" in df.list_files(str(tmp_path / "notes.csv"))[1]


def test_a_home_relative_path_is_followed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    data = _jsonl(tmp_path / "sets" / "d.jsonl", [ALPACA_ROW])
    assert df.list_files("~/sets/d.jsonl") == ([str(data)], None)


# ---- reading rows --------------------------------------------------------------

def test_blank_lines_are_skipped_and_broken_lines_are_named_not_quoted(tmp_path):
    path = _jsonl(tmp_path / "d.jsonl", [ALPACA_ROW, "", "   ", '{"instruction": "cut', '["a list"]', ALPACA_ROW])
    errors = []

    rows = list(df.iter_rows(str(path), errors))

    assert rows == [ALPACA_ROW, ALPACA_ROW]
    assert errors == ["d.jsonl line 4: not valid JSON", "d.jsonl line 5: not a JSON object"]
    assert not any("cut" in e for e in errors)


def test_a_json_file_must_hold_a_list(tmp_path):
    single = tmp_path / "one.json"
    single.write_text(json.dumps(MESSAGES))
    listed = tmp_path / "many.json"
    listed.write_text(json.dumps([MESSAGES, 3]))
    errors = []

    assert list(df.iter_rows(str(single), errors)) == []
    assert list(df.iter_rows(str(listed), errors)) == [MESSAGES]
    assert errors == ["one.json is not a list of rows (a .json dataset holds one JSON list)",
                      "many.json row 2: not a JSON object"]


# ---- row shapes ----------------------------------------------------------------

@pytest.mark.parametrize("row, fmt", [
    (MESSAGES, "messages"), (SHAREGPT, "sharegpt"), (PROMPT_COMPLETION, "prompt_completion"),
    (ALPACA_ROW, "alpaca"), (TEXT, "text"), ({"question": "?"}, None), ([1], None),
])
def test_each_shape_is_detected(row, fmt):
    assert df.detect_format(row) == fmt


def test_every_conversation_shape_becomes_messages_ending_on_the_answer():
    assert df.to_messages(MESSAGES) == (MESSAGES["messages"], None)
    assert df.to_messages(SHAREGPT) == ([{"role": "user", "content": "Two plus two?"},
                                         {"role": "assistant", "content": "Four."}], None)
    assert df.to_messages(PROMPT_COMPLETION) == ([{"role": "user", "content": "Capital of France?"},
                                                  {"role": "assistant", "content": "Paris."}], None)
    with_input = {"instruction": "Translate.", "input": "Bonjour", "output": "Hello", "system": "Be exact."}
    assert df.to_messages(with_input) == ([{"role": "system", "content": "Be exact."},
                                           {"role": "user", "content": "Translate.\n\nBonjour"},
                                           {"role": "assistant", "content": "Hello"}], None)


def test_a_trailing_question_is_dropped_and_a_row_without_an_answer_is_refused():
    trailing = {"messages": MESSAGES["messages"] + [{"role": "user", "content": "And then?"}]}
    assert df.to_messages(trailing)[0] == MESSAGES["messages"]
    assert df.to_messages({"messages": [{"role": "user", "content": "Hi"}]}) == (None, "no assistant turn")
    assert df.to_messages({"instruction": "Hi", "output": "  "}) == (None, "the assistant turn is empty")
    assert "unknown role" in df.to_messages({"messages": [{"role": "tool", "content": "x"}]})[1]


def test_content_given_as_text_parts_is_joined():
    row = {"messages": [{"role": "user", "content": [{"type": "text", "text": "Hi "}, {"type": "text", "text": "there"}]},
                        {"role": "assistant", "content": "Hello."}]}
    assert df.to_messages(row)[0][0]["content"] == "Hi there"


# ---- inspect -------------------------------------------------------------------

def test_inspect_counts_rows_formats_and_problems_across_a_folder(tmp_path):
    _jsonl(tmp_path / "set" / "a.jsonl", [ALPACA_ROW, MESSAGES, "", "{broken"])
    _jsonl(tmp_path / "set" / "sub" / "b.jsonl", [SHAREGPT, {"instruction": "x", "output": ""}, TEXT])

    report = df.inspect(str(tmp_path / "set"), samples=2)

    assert report["kind"] == "folder"
    assert report["path"] == str((tmp_path / "set").resolve())
    assert report["rows"] == 5 and report["usable"] == 4
    assert report["formats"] == {"alpaca": 1, "messages": 1, "sharegpt": 1, "text": 1}
    assert report["error_count"] == 2
    assert report["errors"] == ["a.jsonl line 4: not valid JSON", "b.jsonl line 2: the assistant turn is empty"]
    assert [s["format"] for s in report["samples"]] == ["alpaca", "messages"]
    assert report["trainable"] is True and report["reason"] is None
    assert [(f["rows"], f["usable"]) for f in report["files"]] == [(2, 2), (3, 2)]


def test_inspect_samples_are_clipped(tmp_path):
    long = {"instruction": "Say a lot.", "output": "x" * 5000}
    report = df.inspect(str(_jsonl(tmp_path / "d.jsonl", [long])))
    answer = report["samples"][0]["messages"][-1]["content"]
    assert len(answer) == df.SAMPLE_CHARS + 1 and answer.endswith("…")


def test_inspect_of_a_dataset_with_nothing_usable_is_not_trainable(tmp_path):
    report = df.inspect(str(_jsonl(tmp_path / "chat.jsonl", [{"messages": [{"role": "user", "content": "Hi"}]}])))
    assert report["trainable"] is False
    assert report["usable"] == 0
    assert "none of its 1 rows can be trained on" in report["reason"]
    assert report["samples"] == []


def test_inspect_of_a_url_says_why_and_reads_nothing():
    report = df.inspect("http://example.com/x.jsonl")
    assert report["trainable"] is False and "is a URL" in report["reason"]
    assert report["files"] == []
