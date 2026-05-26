from pathlib import Path

import pytest


def test_apply_exact_replacement_rejects_path_outside_repo(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import GuardedCodeError, apply_exact_replacement

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    with pytest.raises(GuardedCodeError) as exc:
        apply_exact_replacement(str(outside), "x = 1", "x = 2")

    assert exc.value.code == "PATH_OUTSIDE_REPO"


def test_apply_exact_replacement_blocks_locked_codebase(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import GuardedCodeError, apply_exact_replacement

    repo = tmp_path / "repo"
    target = repo / "example.py"
    lock = repo / "data" / ".codebase_lock"
    target.parent.mkdir(parents=True)
    lock.parent.mkdir(parents=True)
    target.write_text("x = 1\n")
    lock.write_text("locked\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    with pytest.raises(GuardedCodeError) as exc:
        apply_exact_replacement("example.py", "x = 1", "x = 2")

    assert exc.value.code == "CODEBASE_LOCKED"
    assert target.read_text() == "x = 1\n"


def test_apply_exact_replacement_creates_backup_and_verifies(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import apply_exact_replacement

    repo = tmp_path / "repo"
    target = repo / "example.py"
    repo.mkdir()
    target.write_text("x = 1\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    result = apply_exact_replacement("example.py", "x = 1", "x = 2")

    assert target.read_text() == "x = 2\n"
    assert Path(result.backup_path).exists()
    assert result.verification["return_code"] == 0


def test_browse_repo_path_filters_excluded_directories(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import browse_repo_path

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "backend").mkdir()
    (repo / "backend" / "app.py").write_text("print('ok')\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    listing = browse_repo_path("")

    assert [folder["name"] for folder in listing["folders"]] == ["backend"]
    assert listing["documents"] == []


def test_apply_exact_replacement_dry_run_does_not_modify_file(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import apply_exact_replacement
    repo = tmp_path / "repo"
    target = repo / "example.py"
    repo.mkdir()
    target.write_text("x = 1\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    result = apply_exact_replacement("example.py", "x = 1", "x = 2", dry_run=True)

    assert target.read_text() == "x = 1\n"
    assert result.backup_path == ""
    assert "Dry run" in result.verification["output_summary"]


def test_apply_exact_replacement_validates_syntax_python(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import GuardedCodeError, apply_exact_replacement
    repo = tmp_path / "repo"
    target = repo / "example.py"
    repo.mkdir()
    target.write_text("def run():\n    return True\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    # This edit introduces a python syntax error (invalid python code)
    with pytest.raises(GuardedCodeError) as exc:
        apply_exact_replacement("example.py", "return True", "return True\n  invalid_syntax ( ) [")

    assert exc.value.code == "SYNTAX_CHECK_FAILED"
    assert "def run():\n    return True\n" in target.read_text()


def test_apply_exact_replacement_normalizes_line_endings(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import apply_exact_replacement
    repo = tmp_path / "repo"
    target = repo / "example.py"
    repo.mkdir()
    # Write a file with Windows line endings
    target.write_bytes(b"x = 1\r\ny = 2\r\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    # Try replacement using Unix line endings in old_text
    result = apply_exact_replacement("example.py", "x = 1\ny = 2", "x = 9\ny = 9")

    assert "x = 9\ny = 9" in target.read_text().replace("\r\n", "\n")
    assert result.verification["return_code"] == 0


def test_read_repo_file_allows_explicit_external_file(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import read_repo_file

    repo = tmp_path / "repo"
    external = tmp_path / "notes.txt"
    repo.mkdir()
    external.write_text("outside but explicit\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    result = read_repo_file(str(external), allow_external=True)

    assert result["scope"] == "external"
    assert result["content"] == "outside but explicit\n"


def test_external_file_requires_explicit_absolute_path(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import GuardedCodeError, read_repo_file

    repo = tmp_path / "repo"
    external = tmp_path / "notes.txt"
    repo.mkdir()
    external.write_text("outside\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    with pytest.raises(GuardedCodeError) as exc:
        read_repo_file("../notes.txt", allow_external=True)

    assert exc.value.code == "EXTERNAL_PATH_NOT_EXPLICIT"


def test_external_file_is_blocked_without_external_mode(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import GuardedCodeError, read_repo_file

    repo = tmp_path / "repo"
    external = tmp_path / "notes.txt"
    repo.mkdir()
    external.write_text("outside\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    with pytest.raises(GuardedCodeError) as exc:
        read_repo_file(str(external))

    assert exc.value.code == "PATH_OUTSIDE_REPO"


def test_apply_exact_replacement_allows_explicit_external_file(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import apply_exact_replacement

    repo = tmp_path / "repo"
    external = tmp_path / "notes.txt"
    repo.mkdir()
    external.write_text("old value\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    result = apply_exact_replacement(str(external), "old value", "new value", allow_external=True)

    assert external.read_text() == "new value\n"
    assert Path(result.backup_path).exists()


def test_external_sensitive_file_is_blocked(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import GuardedCodeError, read_repo_file

    repo = tmp_path / "repo"
    external = tmp_path / ".env"
    repo.mkdir()
    external.write_text("SECRET=yes\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    with pytest.raises(GuardedCodeError) as exc:
        read_repo_file(str(external), allow_external=True)

    assert exc.value.code == "FORBIDDEN_EXTERNAL_PATH"


def test_read_repo_file_rejects_too_large_file(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import GuardedCodeError, read_repo_file

    repo = tmp_path / "repo"
    target = repo / "big.txt"
    repo.mkdir()
    target.write_text("abcdef")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    with pytest.raises(GuardedCodeError) as exc:
        read_repo_file("big.txt", max_bytes=3)

    assert exc.value.code == "FILE_TOO_LARGE"
