from llx.utils import parse_file_mentions, parse_file_mentions_with_metadata


def test_parse_file_mentions_reads_at_path(tmp_path, monkeypatch):
    target = tmp_path / "example.txt"
    target.write_text("hello from file\n")
    monkeypatch.chdir(tmp_path)

    result = parse_file_mentions("summarize @example.txt")

    assert "hello from file" in result
    assert str(target) in result


def test_parse_file_mentions_reads_quoted_path_with_spaces(tmp_path, monkeypatch):
    target = tmp_path / "space file.txt"
    target.write_text("space content\n")
    monkeypatch.chdir(tmp_path)

    result = parse_file_mentions("summarize 'space file.txt'")

    assert "space content" in result


def test_parse_file_mentions_reads_at_quoted_path_with_spaces(tmp_path, monkeypatch):
    target = tmp_path / "space file.txt"
    target.write_text("quoted at content\n")
    monkeypatch.chdir(tmp_path)

    result = parse_file_mentions('summarize @"space file.txt"')

    assert "quoted at content" in result


def test_parse_file_mentions_reads_absolute_path(tmp_path):
    target = tmp_path / "external.txt"
    target.write_text("absolute content\n")

    result = parse_file_mentions(f"summarize {target}")

    assert "absolute content" in result


def test_parse_file_mentions_dedupes_same_file(tmp_path, monkeypatch):
    target = tmp_path / "dupe.txt"
    target.write_text("dedupe content\n")
    monkeypatch.chdir(tmp_path)

    result = parse_file_mentions("read @dupe.txt and ./dupe.txt")

    assert result.count("dedupe content") == 1


def test_parse_file_mentions_reports_missing_explicit_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = parse_file_mentions("read @missing.txt")

    assert "file not found" in result


def test_parse_file_mentions_ignores_ordinary_words_that_match_files(tmp_path, monkeypatch):
    (tmp_path / "read").write_text("should not attach\n")
    monkeypatch.chdir(tmp_path)

    result = parse_file_mentions("read this please")

    assert "should not attach" not in result


def test_parse_file_mentions_rejects_binary_file(tmp_path, monkeypatch):
    target = tmp_path / "binary.bin"
    target.write_bytes(b"abc\x00def")
    monkeypatch.chdir(tmp_path)

    result = parse_file_mentions("inspect @binary.bin")

    assert "appears to be binary" in result


def test_parse_file_mentions_returns_structured_metadata(tmp_path):
    target = tmp_path / "Containerfile.test"
    target.write_text("FROM ubuntu:24.04\n")

    result, attachments = parse_file_mentions_with_metadata(f"summarize '{target}'")

    assert "FROM ubuntu" in result
    assert len(attachments) == 1
    assert attachments[0]["path"] == str(target)
    assert attachments[0]["source"] == "quoted"
    assert attachments[0]["read_status"] == "ok"
    assert attachments[0]["sha256"]
