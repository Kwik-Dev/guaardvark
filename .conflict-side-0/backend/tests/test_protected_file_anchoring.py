"""Task A (P2-9b): anchored PROTECTED_FILES matching in guarded_code_service.

The old guard used a naive `protected_norm in normalized` substring test, which
over-blocked lookalikes (e.g. `quickstart.sh` matched protected `start.sh`) and
was the wrong kind of check. These tests pin the ANCHORED behaviour:

  * every real PROTECTED_FILES entry is still blocked (true-positives preserved),
  * benign lookalikes are NOT blocked,
  * the absolute-path form of a protected file is still blocked (the
    resolve_repo_path containment check normalizes it to a repo-relative path,
    so protected_file_reason fires through apply_exact_replacement).

Hermetic: per-test tmp repo, GUAARDVARK_ROOT + GUAARDVARK_MODE=test, no DB / app
context needed for the apply path (mirrors test_guarded_code_service.py).
"""
from pathlib import Path

import pytest


def _setup_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("GUAARDVARK_MODE", "test")
    return repo


# --- direct protected_file_reason() unit checks ---------------------------

def test_protected_reason_blocks_full_path_entry():
    from backend.services.guarded_code_service import protected_file_reason

    assert protected_file_reason("backend/config.py") is not None
    assert protected_file_reason("backend/services/guarded_code_service.py") is not None


def test_protected_reason_blocks_bare_basename_entry():
    """`start.sh` / `stop.sh` / `killswitch.sh` are bare-filename entries; they
    must block by basename whether at repo root or under a directory that legit-
    imately holds the real root script reference."""
    from backend.services.guarded_code_service import protected_file_reason

    assert protected_file_reason("start.sh") is not None
    assert protected_file_reason("stop.sh") is not None
    assert protected_file_reason("killswitch.sh") is not None


def test_protected_reason_all_config_entries_still_blocked():
    """Every TRUE positive currently configured must remain blocked."""
    from backend.config import PROTECTED_FILES
    from backend.services.guarded_code_service import protected_file_reason

    for entry in PROTECTED_FILES:
        rel = entry.replace("\\", "/").strip("/")
        assert protected_file_reason(rel) is not None, f"{entry} must stay protected"


def test_protected_reason_does_not_block_lookalikes():
    """The anchoring fix: substring lookalikes of `start.sh` must NOT be blocked.

    These FAIL under the old `protected_norm in normalized` substring check
    (which is exactly the over-block bug being fixed)."""
    from backend.services.guarded_code_service import protected_file_reason

    assert protected_file_reason("quickstart.sh") is None
    assert protected_file_reason("scripts/foo_start.sh") is None
    assert protected_file_reason("scripts/restart.sh") is None
    # A directory whose name merely contains a protected path fragment.
    assert protected_file_reason("backend/config.py.notes/readme.md") is None
    # A file named like the protected basename embedded in a longer name.
    assert protected_file_reason("scripts/stop.sh.bak") is None


# --- end-to-end through apply_exact_replacement ---------------------------

def test_apply_blocks_protected_file(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import (
        GuardedCodeError,
        apply_exact_replacement,
    )

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "config.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")

    with pytest.raises(GuardedCodeError) as exc:
        apply_exact_replacement("backend/config.py", "x = 1", "x = 2")

    assert exc.value.code == "PROTECTED_FILE"
    assert target.read_text() == "x = 1\n"
    assert not (repo / "backend" / "config.py.backup").exists()


def test_apply_blocks_protected_basename_at_root(tmp_path, monkeypatch):
    from backend.services.guarded_code_service import (
        GuardedCodeError,
        apply_exact_replacement,
    )

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "start.sh"
    target.write_text("echo hi\n")

    with pytest.raises(GuardedCodeError) as exc:
        apply_exact_replacement("start.sh", "echo hi", "echo bye")

    assert exc.value.code == "PROTECTED_FILE"
    assert target.read_text() == "echo hi\n"


def test_apply_allows_benign_lookalike(tmp_path, monkeypatch):
    """`quickstart.sh` is NOT protected — the edit must succeed. This is the
    over-block negative case: under the old substring check this would have been
    wrongly rejected with PROTECTED_FILE."""
    from backend.services.guarded_code_service import apply_exact_replacement

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "scripts" / "quickstart.sh"
    target.parent.mkdir(parents=True)
    target.write_text("echo hi\n")

    result = apply_exact_replacement("scripts/quickstart.sh", "echo hi", "echo bye")

    assert target.read_text() == "echo bye\n"
    assert result.verification["return_code"] == 0


def test_apply_blocks_protected_file_via_absolute_path(tmp_path, monkeypatch):
    """The absolute-path seam: an absolute path INTO the repo pointing at a
    protected file must still be blocked. resolve_repo_path normalizes in-repo
    absolute paths to a repo-relative path (via the containment check), so
    Path(relative_path).is_absolute() is False and protected_file_reason runs."""
    from backend.services.guarded_code_service import (
        GuardedCodeError,
        apply_exact_replacement,
    )

    repo = _setup_repo(tmp_path, monkeypatch)
    target = repo / "backend" / "config.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n")

    abs_path = str(target.resolve())
    with pytest.raises(GuardedCodeError) as exc:
        apply_exact_replacement(abs_path, "x = 1", "x = 2", allow_external=True)

    assert exc.value.code == "PROTECTED_FILE"
    assert target.read_text() == "x = 1\n"
