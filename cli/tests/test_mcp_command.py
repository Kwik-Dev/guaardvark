"""Test guaardvark mcp commands (serve, etc.)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure worktree's cli/ package takes precedence over any site-packages editable install
CLI_DIR = Path(__file__).resolve().parents[1]
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from typer.testing import CliRunner

from llx.main import app

runner = CliRunner()


def test_serve_stdio_runs_expected_argv(tmp_path, monkeypatch):
    fake_root = tmp_path / "fake_repo"
    fake_root.mkdir()
    (fake_root / "start.sh").touch()
    fake_py = fake_root / "backend" / "venv" / "bin" / "python"
    fake_py.parent.mkdir(parents=True)
    fake_py.touch()

    monkeypatch.setenv("GUAARDVARK_ROOT", str(fake_root))

    executed = {}

    def mock_execv(py, argv):
        executed["py"] = py
        executed["argv"] = argv
        executed["cwd"] = os.getcwd()
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", mock_execv)

    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code == 0
    assert executed["py"] == str(fake_py)
    assert executed["argv"] == [str(fake_py), "-m", "backend.mcp"]
    assert executed["cwd"] == str(fake_root)


def test_serve_http_runs_expected_argv(tmp_path, monkeypatch):
    fake_root = tmp_path / "fake_repo"
    fake_root.mkdir()
    (fake_root / "start.sh").touch()
    fake_py = fake_root / "backend" / "venv" / "bin" / "python"
    fake_py.parent.mkdir(parents=True)
    fake_py.touch()

    monkeypatch.setenv("GUAARDVARK_ROOT", str(fake_root))

    executed = {}

    def mock_execv(py, argv):
        executed["py"] = py
        executed["argv"] = argv
        executed["cwd"] = os.getcwd()
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", mock_execv)

    result = runner.invoke(app, ["mcp", "serve", "--http"])

    assert result.exit_code == 0
    assert executed["py"] == str(fake_py)
    assert executed["argv"] == [str(fake_py), "-m", "backend.mcp", "http"]
    assert executed["cwd"] == str(fake_root)


def test_serve_without_root_exits_nonzero_with_message(tmp_path, monkeypatch):
    # Ensure no GUAARDVARK_ROOT is set and cwd is isolated from any checkout
    monkeypatch.delenv("GUAARDVARK_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code != 0

    # Ensure ONE clear line was printed to stderr
    err = result.stderr if hasattr(result, "stderr") and result.stderr else result.output
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1, f"Expected 1 line in stderr, got {lines}"

    msg = lines[0]
    assert "start.sh" in msg
    assert "GUAARDVARK_ROOT" in msg or "cwd" in msg
    assert "checkout is required" in msg
    assert "git clone https://github.com/guaardvark/guaardvark" in msg
    assert "./start.sh" in msg
