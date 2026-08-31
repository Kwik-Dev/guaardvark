"""Paths shown to people and MCP clients never carry the home directory or the
checkout folder."""
from pathlib import Path

from backend.utils import display_paths as dp


def _fake_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(dp.Path, "home", classmethod(lambda cls: home))


def test_paths_inside_the_checkout_become_relative_and_home_becomes_tilde(monkeypatch, tmp_path):
    home = tmp_path / "home" / "someone"
    root = home / "checkout"
    (root / "logs").mkdir(parents=True)
    monkeypatch.setenv("GUAARDVARK_ROOT", str(root))
    _fake_home(monkeypatch, home)

    assert dp.display_path(root / "logs" / "backend.log") == "logs/backend.log"
    assert dp.display_path(root) == "."
    assert dp.display_path(home / ".cursor" / "mcp.json") == "~/.cursor/mcp.json"
    assert dp.display_path("/etc/hosts") == "/etc/hosts"


def test_command_lines_lose_the_checkout_and_home(monkeypatch, tmp_path):
    home = tmp_path / "home" / "someone"
    root = home / "checkout"
    root.mkdir(parents=True)
    monkeypatch.setenv("GUAARDVARK_ROOT", str(root))
    _fake_home(monkeypatch, home)

    line = f"sh -c cd {root.resolve()} && exec {root.resolve()}/backend/venv/bin/python -m backend.mcp"
    assert dp.display_text(line) == "sh -c cd . && exec ./backend/venv/bin/python -m backend.mcp"
    assert dp.display_text(f"would run: claude mcp add --config {home.resolve()}/.claude.json") == \
        "would run: claude mcp add --config ~/.claude.json"


def test_a_symlink_inside_the_checkout_stays_relative(monkeypatch, tmp_path):
    # A venv's python links to the system interpreter; following the link would
    # print the interpreter's absolute path instead.
    home = tmp_path / "home" / "someone"
    root = home / "checkout"
    bin_dir = root / "backend" / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    interpreter = tmp_path / "usr" / "bin" / "python3.12"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("")
    (bin_dir / "python").symlink_to(interpreter)
    monkeypatch.setenv("GUAARDVARK_ROOT", str(root))
    _fake_home(monkeypatch, home)

    assert dp.display_path(bin_dir / "python") == "backend/venv/bin/python"
