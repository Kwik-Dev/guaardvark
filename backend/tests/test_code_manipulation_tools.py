from pathlib import Path


def test_read_code_tool_reads_explicit_external_file(tmp_path, monkeypatch):
    from backend.tools.agent_tools.code_manipulation_tools import ReadCodeTool

    external = tmp_path / "outside.txt"
    external.write_text("hello external\n")
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    result = ReadCodeTool().execute(filepath=str(external))

    assert result.success is True
    assert "hello external" in result.output


def test_edit_code_tool_edits_explicit_external_file(tmp_path, monkeypatch):
    from backend.tools.agent_tools.code_manipulation_tools import EditCodeTool

    repo = tmp_path / "repo"
    external = tmp_path / "outside.txt"
    repo.mkdir()
    external.write_text("color = 'red'\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    result = EditCodeTool().execute(
        filepath=str(external),
        old_text="color = 'red'",
        new_text="color = 'blue'",
    )

    assert result.success is True
    assert external.read_text() == "color = 'blue'\n"
    assert Path(result.metadata["backup_path"]).exists()
    assert "Diff:" in result.output


def test_edit_code_tool_blocks_sensitive_external_file(tmp_path, monkeypatch):
    from backend.tools.agent_tools.code_manipulation_tools import EditCodeTool

    repo = tmp_path / "repo"
    external = tmp_path / ".env"
    repo.mkdir()
    external.write_text("SECRET=old\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    result = EditCodeTool().execute(
        filepath=str(external),
        old_text="old",
        new_text="new",
    )

    assert result.success is False
    assert result.metadata["blocked_by"] in {"FORBIDDEN_PATH", "FORBIDDEN_EXTERNAL_PATH"}
    assert external.read_text() == "SECRET=old\n"


def test_main_tool_registry_exposes_code_manipulation_tools():
    from backend.tools.tool_registry_init import initialize_all_tools

    registry = initialize_all_tools()

    assert registry.get_tool("read_code") is not None
    assert registry.get_tool("edit_code") is not None
