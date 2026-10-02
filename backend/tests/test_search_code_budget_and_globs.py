"""search_code: caller patterns run under a time budget without holding the
GIL, keep stdlib re's dialect and case-insensitivity, and file_glob follows the
ripgrep convention (a glob without '/' matches at any depth, a folder name
searches the folder).

Each test searches a small tree under tmp_path; it is not a git checkout, so
search_code lists files with its directory walk."""
import threading
import time
from types import SimpleNamespace

import pytest

import backend.tools.llama_code_tools as lct

# Backtracks exponentially in the regex engine as well as in re: 40 a's and a
# b take far longer than any budget used below.
CATASTROPHIC = r"(a|aa)+$"
CATASTROPHIC_LINE = "a" * 40 + "b\n"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(lct, "PROJECT_ROOT", tmp_path)
    (tmp_path / "top.py").write_text("needle = 1\n")
    (tmp_path / "nested" / "dir").mkdir(parents=True)
    (tmp_path / "nested" / "dir" / "deep.py").write_text("def find_needle():\n    return 'NEEDLE'\n")
    (tmp_path / "nested" / "dir" / "notes.txt").write_text("a needle in text\n")
    (tmp_path / "nested" / "other.js").write_text("const needle = 2;\n")
    return tmp_path


def _hit_paths(out):
    return {line.split(". ", 1)[1].rsplit(":", 1)[0] for line in out.splitlines() if line[:1].isdigit()}


def test_backtracking_pattern_stops_with_a_clear_error(tree, monkeypatch):
    monkeypatch.setattr(lct, "LINE_MATCH_TIMEOUT_S", 0.2)
    (tree / "slow.py").write_text(CATASTROPHIC_LINE)
    t0 = time.monotonic()
    out = lct.search_code(CATASTROPHIC, "slow.py")
    assert time.monotonic() - t0 < 5
    assert out.startswith("ERROR")
    assert "too expensive" in out and "slow.py:1" in out
    assert "again" not in out.lower()  # not an invitation to retry the same call


def test_whole_search_deadline(tree, monkeypatch):
    clock = {"now": 0.0}

    def monotonic():
        clock["now"] += 7.0
        return clock["now"]

    monkeypatch.setattr(lct, "time", SimpleNamespace(monotonic=monotonic))
    (tree / "many.py").write_text("x = 1\n" * 50)
    out = lct.search_code("never_there", "many.py")
    assert out.startswith("ERROR")
    assert f"had not finished after {lct.SEARCH_DEADLINE_S:g} s" in out


def test_other_threads_run_while_a_line_is_matched(tree, monkeypatch):
    """The regex engine releases the GIL while matching, so the MCP server's
    event loop (and its call timeout) keeps running during a slow match."""
    monkeypatch.setattr(lct, "LINE_MATCH_TIMEOUT_S", 1.5)
    (tree / "slow.py").write_text(CATASTROPHIC_LINE)
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("out", lct.search_code(CATASTROPHIC, "slow.py")))
    worker.start()
    time.sleep(0.1)
    gaps, last = [], time.monotonic()
    while worker.is_alive() and len(gaps) < 200:
        time.sleep(0.005)
        now = time.monotonic()
        gaps.append(now - last)
        last = now
    worker.join()
    assert result["out"].startswith("ERROR")
    assert max(gaps) < 0.5


def test_case_insensitive_and_re_dialect(tree):
    out = lct.search_code("NEEDLE", "nested/dir/*.py")
    assert _hit_paths(out) == {"nested/dir/deep.py"}
    assert "Found 2 matches" in out
    # re decides what is valid: syntax only the regex engine knows is refused.
    assert lct.search_code(r"\p{L}+", "**/*.py").startswith("ERROR: '\\p{L}+' is not a valid regular expression")
    assert lct.search_code("(", "**/*.py").startswith("ERROR: '(' is not a valid regular expression")


def test_basename_glob_matches_at_any_depth(tree):
    out = lct.search_code("needle", "*.py")
    assert _hit_paths(out) == {"top.py", "nested/dir/deep.py"}
    out = lct.search_code("needle", "*.{py,js}")
    assert _hit_paths(out) == {"top.py", "nested/dir/deep.py", "nested/other.js"}


def test_glob_with_a_folder_stays_anchored(tree):
    assert _hit_paths(lct.search_code("needle", "nested/*.js")) == {"nested/other.js"}
    assert lct.search_code("needle", "dir/*.py").startswith("No matches found")


@pytest.mark.parametrize("folder", ["nested/dir", "nested/dir/", "./nested/dir"])
def test_folder_name_searches_every_file_under_it(tree, folder):
    out = lct.search_code("needle", folder)
    assert _hit_paths(out) == {"nested/dir/deep.py", "nested/dir/notes.txt"}


def test_no_match_says_how_the_glob_was_read(tree):
    assert lct.search_code("absent_symbol", "*.py").endswith("(file names in any folder)")
    assert lct.search_code("absent_symbol", "nested/dir").endswith("(every file under nested/dir)")
    assert lct.search_code("absent_symbol", "nested/**/*.py").endswith("in nested/**/*.py")


def test_max_hits_caps_the_listing_not_the_total(tree):
    (tree / "hits.py").write_text("needle\n" * 5)
    out = lct.search_code("needle", "hits.py", max_hits=2)
    assert "Found 5 matches" in out
    assert out.count("hits.py:") == 2
    assert "showing first 2" in out
