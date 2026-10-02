"""process_file gives the same answer for an uploaded file whether it is named
by a path relative to uploads or by its absolute path, over MCP and in chat;
and a path that cannot be resolved is an error result, not an exception."""

import os

import pytest

from backend import config
from backend.tools.agent_tools.file_operation_tools import ProcessFileTool

INTERNAL = "files under .git, venv, node_modules, dist, logs and similar folders are not read"

REFUSED = [
    "somerepo/.git/notes.txt",
    "node_modules/pkg/readme.md",
    "venv/notes.txt",
    "logs/app.txt",
    "project/dist/bundle.txt",
]
# Names the install's own .gitignore or folder rules cover, but which are
# ordinary documents when they sit in uploads.
READ = [
    "server.log",
    "backups/q3.txt",
    "build/out.txt",
    "reports/q3.txt",
    "notes.txt",
]


@pytest.fixture
def folders(tmp_path, monkeypatch):
    uploads, outputs = tmp_path / "uploads", tmp_path / "outputs"
    for rel in REFUSED + READ:
        path = uploads / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content of {rel}\n")
    for rel in ("run1/result.txt", "run1/.git/config.txt"):
        path = outputs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content of {rel}\n")
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    return uploads, outputs


@pytest.fixture(params=["mcp", "chat"])
def tool(request):
    tool = ProcessFileTool()
    if request.param == "mcp":
        tool.set_context({"transport": "mcp"})
    return tool


@pytest.mark.parametrize("rel", REFUSED)
def test_a_repository_folder_in_uploads_is_refused_by_either_path(folders, tool, rel):
    uploads, _ = folders
    for path in (rel, str(uploads / rel)):
        result = tool.execute(file_path=path)
        assert not result.success and INTERNAL in result.error, path


@pytest.mark.parametrize("rel", READ)
def test_an_upload_is_read_by_either_path(folders, tool, rel):
    uploads, _ = folders
    for path in (rel, str(uploads / rel)):
        result = tool.execute(file_path=path)
        assert result.success and f"content of {rel}" in result.output, path


def test_outputs_follow_the_same_rule(folders, tool):
    _, outputs = folders
    assert tool.execute(file_path=str(outputs / "run1/result.txt")).success
    refused = tool.execute(file_path=str(outputs / "run1/.git/config.txt"))
    assert not refused.success and INTERNAL in refused.error


def test_a_refusal_does_not_depend_on_the_file_existing(folders, tool):
    uploads, _ = folders
    missing = tool.execute(file_path=str(uploads / "somerepo/.git/not-there.txt"))
    present = tool.execute(file_path=str(uploads / "somerepo/.git/notes.txt"))
    assert not missing.success and not present.success
    assert missing.error.replace("not-there.txt", "notes.txt") == present.error


def test_a_name_found_nowhere_is_not_found(folders, tool):
    result = tool.execute(file_path="reports/no-such-report.txt")
    assert not result.success and result.error.startswith("File not found")


def test_paths_that_cannot_be_resolved_are_error_results(folders, tool):
    uploads, _ = folders
    os.symlink(uploads / "loop_b.pdf", uploads / "loop_a.pdf")
    os.symlink(uploads / "loop_a.pdf", uploads / "loop_b.pdf")
    expected = {
        "~no_such_user_zz/x.pdf": "is not a valid path",
        "bad\x00name.pdf": "not a valid path",
        "": "empty or not a valid path",
        "../outside.txt": "may not leave the Guaardvark folder",
        str(uploads): "File not found",
    }
    for path, reason in expected.items():
        result = tool.execute(file_path=path)
        assert not result.success and reason in result.error, (path, result.error)
    # A symlink loop: Python up to 3.12 raises while resolving it, later
    # versions resolve it to a path that is not a file.
    for path in (str(uploads / "loop_a.pdf"), "loop_a.pdf"):
        result = tool.execute(file_path=path)
        assert not result.success, path
        assert "could not be resolved" in result.error or "File not found" in result.error, result.error
