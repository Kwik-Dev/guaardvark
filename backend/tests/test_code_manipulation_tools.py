from pathlib import Path
from unittest.mock import patch

import pytest


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


# ---- the self-improvement apply gate --------------------------------------------
#
# Under self-improvement, edit_code writes a file only when a person has set
# self_improvement_apply_enabled=true; otherwise, or when the setting cannot be
# read, it stages the change for review.

@pytest.fixture
def settings_db():
    """An in-memory database holding the system settings the gate reads."""
    from flask import Flask
    from backend.models import db
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield db
        db.session.remove()
        db.drop_all()


def _set_setting(db, key, value):
    from backend.models import SystemSetting
    db.session.add(SystemSetting(key=key, value=value))
    db.session.commit()


def _throwaway_checkout(tmp_path, monkeypatch):
    """A checkout root holding notes.txt, with the inbound guard off."""
    import backend.services.inbound_guard_service as guard_mod
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "notes.txt"
    target.write_text("x = 1\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")
    monkeypatch.setattr(guard_mod, "is_on", lambda: False)
    return target


def _gated_edit(tmp_path, monkeypatch, context=None, scheduled_sends=False, filepath=None):
    """edit_code under self-improvement on a text file in a throwaway checkout
    (or on ``filepath``).

    Returns (result, staged kwargs, reviews sent, target). stage_pending_fix
    is recorded rather than written, and the guardian review never answers.
    """
    import backend.services.claude_advisor_service as advisor_mod
    from backend.tools.agent_tools import code_manipulation_tools as cmt

    target = _throwaway_checkout(tmp_path, monkeypatch)
    reviews = []

    class Advisor:
        def review_change(self, **kwargs):
            reviews.append(kwargs)
            return advisor_mod._not_reviewed("Uncle Claude unavailable")

    monkeypatch.setattr(advisor_mod, "get_claude_advisor", lambda: Advisor())
    monkeypatch.setattr(advisor_mod, "scheduled_sends_allowed", lambda: scheduled_sends)

    staged = []

    def fake_stage(path, old_text, new_text, description, **kwargs):
        staged.append(kwargs)
        return 7

    monkeypatch.setattr(cmt, "stage_pending_fix", fake_stage)

    result = cmt.EditCodeTool().execute(
        filepath=filepath or str(target),
        old_text="x = 1",
        new_text="x = 2",
        _agent_context={"_self_improvement_context": True, "_reasoning": "fix it", **(context or {})},
    )
    return result, staged, reviews, target


def test_self_improvement_edit_is_staged_while_apply_is_not_enabled(tmp_path, monkeypatch, settings_db):
    result, staged, _, target = _gated_edit(tmp_path, monkeypatch)

    assert result.success is True
    assert result.metadata["staged"] is True
    assert len(staged) == 1
    assert target.read_text() == "x = 1\n"


def test_self_improvement_edit_is_staged_when_apply_is_set_false(tmp_path, monkeypatch, settings_db):
    _set_setting(settings_db, "self_improvement_apply_enabled", "false")
    result, _, _, target = _gated_edit(tmp_path, monkeypatch)

    assert result.metadata["staged"] is True
    assert target.read_text() == "x = 1\n"


def test_self_improvement_edit_lands_once_apply_is_enabled(tmp_path, monkeypatch, settings_db):
    _set_setting(settings_db, "self_improvement_apply_enabled", "true")
    result, staged, _, target = _gated_edit(tmp_path, monkeypatch)

    assert result.success is True
    assert staged == []
    assert target.read_text() == "x = 2\n"
    assert Path(result.metadata["backup_path"]).exists()


def test_a_scheduled_run_stages_even_with_apply_enabled(tmp_path, monkeypatch, settings_db):
    # Dean 2026-10-06 (T518): the scheduled self-check stages fixes for review.
    _set_setting(settings_db, "self_improvement_apply_enabled", "true")
    result, staged, _, target = _gated_edit(tmp_path, monkeypatch, context={"_trigger": "scheduled"},
                                            scheduled_sends=True)

    assert result.success is True
    assert len(staged) == 1
    assert target.read_text() == "x = 1\n"


def test_apply_gate_fails_closed_when_the_setting_cannot_be_read(settings_db):
    from backend.tools.agent_tools.code_manipulation_tools import _self_improvement_apply_blocked
    _set_setting(settings_db, "self_improvement_apply_enabled", "true")

    with patch.object(settings_db.session, "query", side_effect=RuntimeError("database gone")):
        assert _self_improvement_apply_blocked() is True
    assert _self_improvement_apply_blocked() is False


def test_self_improvement_never_edits_outside_the_checkout(tmp_path, monkeypatch, settings_db):
    _set_setting(settings_db, "self_improvement_apply_enabled", "true")
    outside = tmp_path / "outside.txt"
    outside.write_text("x = 1\n")

    result, staged, _, _ = _gated_edit(tmp_path, monkeypatch, filepath=str(outside))

    assert result.success is False
    assert staged == []
    assert result.metadata["blocked_by"] == "PATH_OUTSIDE_REPO"
    assert outside.read_text() == "x = 1\n"


def test_a_chat_edit_is_not_gated(tmp_path, monkeypatch):
    from backend.tools.agent_tools import code_manipulation_tools as cmt
    target = _throwaway_checkout(tmp_path, monkeypatch)

    def not_consulted():
        raise AssertionError("the apply gate is for self-improvement only")

    monkeypatch.setattr(cmt, "_self_improvement_apply_blocked", not_consulted)

    result = cmt.EditCodeTool().execute(filepath=str(target), old_text="x = 1", new_text="x = 2")

    assert result.success is True
    assert target.read_text() == "x = 2\n"


def test_a_scheduled_run_holds_the_review_while_scheduled_sends_are_off(tmp_path, monkeypatch):
    result, staged, reviews, _ = _gated_edit(
        tmp_path, monkeypatch, {"_trigger": "scheduled"}, scheduled_sends=False)

    assert reviews == []
    assert result.metadata["staged"] is True
    assert staged[0]["review_notes"] == "not reviewed: not sent: scheduled sends to Uncle Claude are off"


def test_a_scheduled_run_sends_the_review_when_scheduled_sends_are_on(tmp_path, monkeypatch):
    _, staged, reviews, _ = _gated_edit(
        tmp_path, monkeypatch, {"_trigger": "scheduled"}, scheduled_sends=True)

    assert len(reviews) == 1
    assert staged[0]["review_notes"] == "not reviewed: Uncle Claude unavailable"


def test_a_directed_run_sends_the_review_as_before(tmp_path, monkeypatch):
    _, _, reviews, _ = _gated_edit(
        tmp_path, monkeypatch, {"_trigger": "directed"}, scheduled_sends=False)

    assert len(reviews) == 1


def test_a_reactive_heal_holds_the_review_while_scheduled_sends_are_off(tmp_path, monkeypatch):
    # heal() runs on a repeated error with nobody watching, like a scheduled run.
    result, staged, reviews, _ = _gated_edit(
        tmp_path, monkeypatch, {"_trigger": "reactive"}, scheduled_sends=False)

    assert reviews == []
    assert result.metadata["staged"] is True
    assert staged[0]["review_notes"] == "not reviewed: not sent: scheduled sends to Uncle Claude are off"
