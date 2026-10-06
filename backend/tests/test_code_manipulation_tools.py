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


def _self_improvement_edit(tmp_path, monkeypatch, review):
    """Run edit_code under self-improvement with a stand-in guardian review.

    Returns (result, staged kwargs, target file). stage_pending_fix is
    replaced, so nothing reaches the database.
    """
    import backend.services.claude_advisor_service as advisor_mod
    from backend.tools.agent_tools import code_manipulation_tools as cmt

    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "thing.py"
    target.write_text("x = 1\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    class Advisor:
        def is_available(self):
            return True

        def review_change(self, **kwargs):
            if isinstance(review, Exception):
                raise review
            return review

    monkeypatch.setattr(advisor_mod, "get_claude_advisor", lambda: Advisor())

    staged = []

    def fake_stage(path, old_text, new_text, description, **kwargs):
        staged.append(kwargs)
        return 7

    monkeypatch.setattr(cmt, "stage_pending_fix", fake_stage)

    result = cmt.EditCodeTool().execute(
        filepath=str(target),
        old_text="x = 1",
        new_text="x = 2",
        _agent_context={"_self_improvement_context": True, "_reasoning": "fix it"},
    )
    return result, staged, target


def test_not_reviewed_change_is_staged_as_not_reviewed(tmp_path, monkeypatch):
    from backend.services.claude_advisor_service import _not_reviewed

    result, staged, target = _self_improvement_edit(
        tmp_path, monkeypatch, _not_reviewed("Uncle Claude unavailable"))

    assert result.success is True
    assert result.metadata["staged"] is True
    assert len(staged) == 1
    assert staged[0]["reviewed_by"] is None
    assert staged[0]["review_notes"] == "not reviewed: Uncle Claude unavailable"
    assert target.read_text() == "x = 1\n"


def test_review_without_a_verdict_is_not_an_approval(tmp_path, monkeypatch):
    result, staged, _ = _self_improvement_edit(tmp_path, monkeypatch, {"reason": "hmm"})

    assert result.success is True
    assert staged[0]["reviewed_by"] is None
    assert staged[0]["review_notes"].startswith("not reviewed")


def test_review_that_raises_is_staged_as_not_reviewed(tmp_path, monkeypatch):
    result, staged, _ = _self_improvement_edit(tmp_path, monkeypatch, RuntimeError("socket closed"))

    assert result.success is True
    assert staged[0]["reviewed_by"] is None
    assert staged[0]["review_notes"] == "not reviewed: socket closed"


def test_approved_review_is_recorded(tmp_path, monkeypatch):
    result, staged, _ = _self_improvement_edit(
        tmp_path, monkeypatch, {"approved": True, "reviewed": True, "reason": "safe"})

    assert result.success is True
    assert staged[0]["reviewed_by"] == "uncle_claude"
    assert staged[0]["review_notes"] == "safe"


def test_rejected_review_stages_nothing(tmp_path, monkeypatch):
    result, staged, target = _self_improvement_edit(
        tmp_path, monkeypatch,
        {"approved": False, "reviewed": True, "directive": "reject", "reason": "unsafe", "suggestions": []})

    assert result.success is False
    assert "rejected" in result.error
    assert staged == []
    assert target.read_text() == "x = 1\n"
