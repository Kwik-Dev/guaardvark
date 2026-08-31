"""Task B (P2-9a): negative-case tests that actually ARM the kill switch.

`_handle_uncle_directive` (backend/tools/agent_tools/code_manipulation_tools.py)
is the consumer of Uncle Claude's directive vocabulary. On `lock_codebase` /
`halt_family` it flips SystemSetting flags AND writes data/.codebase_lock; on
`halt_self_improvement` it disables self_improvement_enabled. It had ZERO test
coverage — an unproven brake. These tests make the brake FIRE end-to-end and
prove a subsequent guarded edit is refused with CODEBASE_LOCKED.

Hermetic:
  * in-memory sqlite + Flask app_context (mirrors test_audit_batch3_scheduler.py),
  * GUAARDVARK_ROOT pointed at a tmp dir so the real data/.codebase_lock is never
    touched and is_codebase_locked()'s file check reads the tmp marker,
  * halt_family's interconnector broadcast is monkeypatched so no real transport
    fires and no Anthropic/Uncle-Claude API is contacted.
"""
from __future__ import annotations

import os

import pytest
from flask import Flask

from backend.models import db, SystemSetting


@pytest.fixture
def killswitch_env(tmp_path, monkeypatch):
    """Tmp repo root + in-memory DB app context.

    GUAARDVARK_ROOT is redirected so neither the real DB nor the real
    data/.codebase_lock file are ever touched.
    """
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield repo
        db.session.remove()
        db.drop_all()


def _setting(key):
    return db.session.query(SystemSetting).filter_by(key=key).first()


# --- lock_codebase: flips DB flags + writes lock file + blocks edits -------

def test_lock_codebase_directive_arms_everything(killswitch_env):
    from backend.tools.agent_tools.code_manipulation_tools import _handle_uncle_directive
    from backend.services.guarded_code_service import (
        GuardedCodeError,
        apply_exact_replacement,
        is_codebase_locked,
    )

    repo = killswitch_env

    # Pre-condition: nothing armed yet.
    assert _setting("codebase_locked") is None
    assert _setting("self_improvement_enabled") is None
    assert not (repo / "data" / ".codebase_lock").exists()
    assert is_codebase_locked() is False

    # FIRE the brake.
    _handle_uncle_directive("lock_codebase", "unit-test triggered lockdown")

    # 1. DB flags flipped.
    locked = _setting("codebase_locked")
    si = _setting("self_improvement_enabled")
    assert locked is not None and locked.value == "true"
    assert si is not None and si.value == "false"

    # 2. data/.codebase_lock marker written with the directive metadata.
    lock_file = repo / "data" / ".codebase_lock"
    assert lock_file.exists()
    body = lock_file.read_text()
    assert "UNCLE_DIRECTIVE=lock_codebase" in body
    assert "unit-test triggered lockdown" in body

    # 3. The lock is observed by the guard.
    assert is_codebase_locked() is True

    # 4. A subsequent guarded edit is refused with CODEBASE_LOCKED.
    target = repo / "example.py"
    target.write_text("x = 1\n")
    with pytest.raises(GuardedCodeError) as exc:
        apply_exact_replacement("example.py", "x = 1", "x = 2", require_unlocked=True)
    assert exc.value.code == "CODEBASE_LOCKED"
    assert exc.value.status_code == 423
    # Edit was not applied.
    assert target.read_text() == "x = 1\n"


# --- halt_self_improvement: disables SI flag, does NOT lock the codebase ----

def test_halt_self_improvement_directive_disables_si_only(killswitch_env):
    from backend.tools.agent_tools.code_manipulation_tools import _handle_uncle_directive
    from backend.services.guarded_code_service import is_codebase_locked

    repo = killswitch_env

    _handle_uncle_directive("halt_self_improvement", "too many failing edits")

    si = _setting("self_improvement_enabled")
    assert si is not None and si.value == "false"

    # halt_self_improvement must NOT lock the codebase or write the marker.
    assert _setting("codebase_locked") is None
    assert not (repo / "data" / ".codebase_lock").exists()
    assert is_codebase_locked() is False


# --- halt_family: arms the lock AND broadcasts (broadcast mocked) -----------

def test_halt_family_directive_arms_and_broadcasts(killswitch_env, monkeypatch):
    import backend.tools.agent_tools.code_manipulation_tools as cmt
    from backend.services.guarded_code_service import is_codebase_locked

    repo = killswitch_env

    # Mock the interconnector so no real transport / network fires.
    broadcasts = []

    class _FakeSync:
        def broadcast_directive(self, directive, reason):
            broadcasts.append((directive, reason))

    import backend.services.interconnector_sync_service as iss
    monkeypatch.setattr(iss, "InterconnectorSyncService", lambda: _FakeSync())

    cmt._handle_uncle_directive("halt_family", "family-wide stop")

    # Same lock posture as lock_codebase.
    assert _setting("codebase_locked").value == "true"
    assert _setting("self_improvement_enabled").value == "false"
    assert (repo / "data" / ".codebase_lock").exists()
    assert is_codebase_locked() is True

    # And it broadcast exactly once with the directive + reason.
    assert broadcasts == [("halt_family", "family-wide stop")]


# --- existing setting is mutated in place (not duplicated) ------------------

def test_lock_codebase_updates_existing_setting(killswitch_env):
    from backend.tools.agent_tools.code_manipulation_tools import _handle_uncle_directive

    # Seed a pre-existing 'unlocked' row to prove the update path (not insert).
    db.session.add(SystemSetting(key="codebase_locked", value="false"))
    db.session.add(SystemSetting(key="self_improvement_enabled", value="true"))
    db.session.commit()

    _handle_uncle_directive("lock_codebase", "flip existing")

    assert _setting("codebase_locked").value == "true"
    assert _setting("self_improvement_enabled").value == "false"
    # No duplicate rows.
    assert db.session.query(SystemSetting).filter_by(key="codebase_locked").count() == 1
