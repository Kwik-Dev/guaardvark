"""Diffs from a real git repository become the Changes the rules read."""
import os
import subprocess

import pytest

from scripts.inbound_guard import change_digest
from scripts.inbound_guard.sources import changes_from_diff, ignored_paths


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "keep.py").write_text("a = 1\n")
    (tmp_path / "old_name.py").write_text("".join(f"line_{n} = {n}\n" for n in range(20)))
    (tmp_path / "gone.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("private.md\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base", "--no-verify")
    return tmp_path


def test_added_lines_modes_and_statuses(repo):
    # An added line whose text starts with "++" must not be read as a header.
    (repo / "keep.py").write_text("a = 1\n++not_a_header = 2\n")
    _git(repo, "mv", "old_name.py", "new_name.py")
    (repo / "gone.py").unlink()
    os.symlink("/etc/passwd", repo / "link")
    (repo / "blob.bin").write_bytes(bytes(range(256)))
    (repo / "run.py").write_text("print(1)\n")
    os.chmod(repo / "run.py", 0o755)
    _git(repo, "add", "-A")

    changes, total = changes_from_diff(repo, ["--cached"])
    by_path = {c.path: c for c in changes}

    assert by_path["keep.py"].added == [(2, "++not_a_header = 2")]
    assert by_path["new_name.py"].status == "R" and by_path["new_name.py"].old_path == "old_name.py"
    assert by_path["gone.py"].status == "D"
    assert by_path["link"].is_symlink and by_path["link"].symlink_target == "/etc/passwd"
    assert by_path["blob.bin"].binary
    assert by_path["run.py"].new_mode == "100755"
    assert total == sum(len(c.added) for c in changes)


def test_cap_marks_truncation(repo):
    (repo / "big.py").write_text("".join(f"v{n} = {n}\n" for n in range(50)))
    _git(repo, "add", "-A")
    changes, total = changes_from_diff(repo, ["--cached"], max_added=10)
    assert total == 10
    assert any(c.truncated for c in changes)


def test_index_and_commit_give_the_same_digest(repo):
    """pre-merge-commit judges the index; the ref hook sees the commit. Same change, same id."""
    (repo / "keep.py").write_text("a = 2\n")
    _git(repo, "add", "-A")
    staged, _ = changes_from_diff(repo, ["--cached"])
    _git(repo, "commit", "-q", "-m", "change", "--no-verify")
    committed, _ = changes_from_diff(repo, ["HEAD~1", "HEAD"])
    assert change_digest(staged) == change_digest(committed)


def test_ignored_paths(repo):
    assert ignored_paths(repo, ["private.md", "keep.py"]) == {"private.md"}
