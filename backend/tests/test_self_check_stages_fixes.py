"""The scheduled self-check reaches the agent and tests what it stages.

A failing test sends the code agent after a fix; each fix it stages is run
against its failing test file in a scratch copy of the checkout, and the
result is written on the pending fix. Nothing is applied to the checkout.

Stubbed seams: the agent (_attempt_fix) and the pytest subprocess. git runs
for real against a throwaway repository; the database is in-memory SQLite.
"""
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from backend.services.self_improvement_service import _UNREACHABLE_DATABASE_URL

SVC = "backend.services.self_improvement_service"
REAL_RUN = subprocess.run

ONE_FAILURE = "FAILED backend/tests/test_a.py::test_one - AssertionError: no\n1 failed\n"
TWO_FAILURES = (
    "FAILED backend/tests/test_a.py::test_one - AssertionError: no\n"
    "FAILED backend/tests/test_a.py::test_two - AssertionError: no\n2 failed\n"
)
LIVE_SOURCE = "def value():\n    return 1  # uncommitted\n"


@pytest.fixture
def app():
    from backend.models import db
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _git(repo, *args):
    return REAL_RUN(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                     "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A throwaway git checkout as GUAARDVARK_ROOT, with an uncommitted change
    the scratch copy has to carry, and a DATABASE_URL the tests must not see."""
    repo = tmp_path / "checkout"
    (repo / "backend" / "tests").mkdir(parents=True)
    (repo / "backend" / "a.py").write_text("def value():\n    return 1\n")
    (repo / "backend" / "tests" / "test_a.py").write_text(
        "from backend.a import value\n\n\ndef test_one():\n    assert value() == 2\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "start")
    (repo / "backend" / "a.py").write_text(LIVE_SOURCE)
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:secret@db.invalid:5432/live")
    return repo


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    """Where the scratch copies are made, so a test can see they are gone."""
    root = tmp_path / "temp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture
def gates_open():
    with patch(f"{SVC}._is_codebase_locked", return_value=False), \
         patch(f"{SVC}._is_self_improvement_enabled", return_value=True):
        yield


def _service():
    """A service that is not the process singleton, with its real run guard."""
    from backend.services.self_improvement_service import SelfImprovementService
    svc = object.__new__(SelfImprovementService)
    svc._initialized = False
    svc.__init__()
    SelfImprovementService._cancel_requested_ids.clear()
    return svc


def _pytest_runs(checkout, verify_runs, verify_stdout="1 passed\n", verify_rc=0, check_output=ONE_FAILURE):
    """subprocess.run as the service calls it: pytest answered here, git and the rest for real.

    The self-check's own run (in the checkout) fails; each verification run (in
    a scratch copy) is recorded with the source it saw and answered as given.
    """
    def run(cmd, *args, **kwargs):
        if "pytest" not in cmd:
            return REAL_RUN(cmd, *args, **kwargs)
        cwd = Path(kwargs["cwd"])
        if cwd.resolve() == checkout.resolve():
            return MagicMock(returncode=1, stdout=check_output, stderr="")
        verify_runs.append({"cmd": cmd, "cwd": cwd, "env": kwargs["env"],
                            "source": (cwd / "backend" / "a.py").read_text()})
        return MagicMock(returncode=verify_rc, stdout=verify_stdout, stderr="")
    return run


def _stages_a_fix(svc, checkout, old_text="    return 1  # uncommitted\n", new_text="    return 2\n"):
    """An agent that stages one exact replacement, as edit_code does under self-improvement."""
    def attempt(failure, message=None):
        from backend.models import db, PendingFix
        db.session.add(PendingFix(
            run_id=svc._current_run_id, file_path=str(checkout / "backend" / "a.py"),
            original_content=old_text, proposed_new_content=new_text,
            proposed_diff="-    return 1\n+    return 2\n", fix_description="Fix this failing test.",
            severity="medium", status="proposed"))
        db.session.commit()
        return {"fix_description": "Made value() return 2.", "iterations": 3}
    return attempt


def _worktrees(repo):
    return [line for line in _git(repo, "worktree", "list", "--porcelain").stdout.splitlines()
            if line.startswith("worktree ")]


def _latest_run():
    from backend.models import db, SelfImprovementRun
    db.session.expire_all()
    return SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first()


# ---- the scheduled run reaches the agent ----------------------------------------

def test_the_scheduled_check_runs_the_agent_on_a_failing_test(app, checkout, gates_open):
    svc = _service()
    attempts = []

    def attempt(failure, message=None):
        attempts.append(failure["test_name"])
        return None

    with patch(f"{SVC}.subprocess.run", side_effect=_pytest_runs(checkout, [])), \
         patch.object(svc, "_attempt_fix", side_effect=attempt):
        result = svc.run_self_check()

    assert attempts == ["test_one"]
    assert result["message"] == "No fix staged for 1 failure(s)"
    assert svc._running is False and svc._current_trigger is None


def test_locking_the_codebase_stops_the_check_at_the_next_failure(app, checkout):
    svc = _service()
    locked = {"now": False}
    attempts = []

    def attempt_then_lock(failure, message=None):
        attempts.append(failure["test_name"])
        locked["now"] = True
        return None

    with patch(f"{SVC}._is_codebase_locked", side_effect=lambda: locked["now"]), \
         patch(f"{SVC}._is_self_improvement_enabled", return_value=True), \
         patch(f"{SVC}.subprocess.run", side_effect=_pytest_runs(checkout, [], check_output=TWO_FAILURES)), \
         patch.object(svc, "_attempt_fix", side_effect=attempt_then_lock):
        svc.run_self_check()

    assert attempts == ["test_one"]


def test_the_scheduled_check_tells_edit_code_it_is_scheduled(app, checkout, gates_open):
    """edit_code holds the Uncle Claude review of a scheduled run for Scheduled sends."""
    svc = _service()
    contexts = []

    class FakeExecutor:
        def __init__(self, **kwargs):
            pass

        def set_tool_context(self, **kwargs):
            contexts.append(kwargs)

        def execute(self, message, session_context=""):
            return MagicMock(final_answer="nothing found", iterations=1)

    agent = MagicMock(max_iterations=3, system_prompt="")
    with patch(f"{SVC}.subprocess.run", side_effect=_pytest_runs(checkout, [])), \
         patch("backend.services.agent_executor.AgentExecutor", FakeExecutor), \
         patch("backend.services.agent_config.AgentConfigManager") as manager, \
         patch("backend.services.agent_tools.get_tool_registry", return_value=MagicMock()):
        manager.return_value.get_agent.return_value = agent
        svc.run_self_check()

    assert contexts[0]["_self_improvement_context"] is True
    assert contexts[0]["_trigger"] == "scheduled"
    assert contexts[0]["_run_id"] == _latest_run().id


# ---- each staged fix is tested in a scratch copy --------------------------------

def test_a_scheduled_run_stages_one_fix_with_a_verification_result(app, checkout, temp_root, gates_open):
    from backend.models import PendingFix
    svc = _service()
    verify_runs = []

    with patch(f"{SVC}.subprocess.run", side_effect=_pytest_runs(checkout, verify_runs)), \
         patch.object(svc, "_attempt_fix", side_effect=_stages_a_fix(svc, checkout)):
        result = svc.run_self_check()

    assert result["success"] is True
    assert result["fixes_staged"] == 1 and result["fixes_verified"] == 1
    change = result["changes"][0]
    assert change["fix_description"] == "Made value() return 2."
    assert change["verification"]["all_passed"] is True
    assert change["verification"]["tests"] == ["backend/tests/test_a.py"]

    # One run, of the failing file only, in the copy, off the app's database.
    assert len(verify_runs) == 1
    run = verify_runs[0]
    assert run["cmd"][:4] == ["python3", "-m", "pytest", "backend/tests/test_a.py"]
    assert run["cwd"].resolve() != checkout.resolve()
    assert run["env"]["DATABASE_URL"] == _UNREACHABLE_DATABASE_URL
    assert run["env"]["GUAARDVARK_MODE"] == "test"
    assert run["env"]["GUAARDVARK_ROOT"] == str(run["cwd"])
    # The copy had the uncommitted change, with the fix made on top of it.
    assert run["source"] == "def value():\n    return 2\n"

    # The checkout is untouched and the copy is gone.
    assert (checkout / "backend" / "a.py").read_text() == LIVE_SOURCE
    assert not run["cwd"].exists()
    assert list(temp_root.iterdir()) == []
    assert len(_worktrees(checkout)) == 1

    fix = PendingFix.query.one()
    assert fix.status == "proposed" and fix.applied_at is None
    assert fix.fix_description.endswith(
        "Scratch-copy test run: backend/tests/test_a.py passed with this fix applied.")
    run_row = _latest_run()
    assert run_row.status == "success"
    after = json.loads(run_row.test_results_after)
    assert after[0]["pending_fix_ids"] == [fix.id] and after[0]["all_passed"] is True


def test_a_fix_that_does_not_pass_says_so_on_the_pending_fix(app, checkout, temp_root, gates_open):
    from backend.models import PendingFix
    svc = _service()

    with patch(f"{SVC}.subprocess.run",
               side_effect=_pytest_runs(checkout, [], verify_stdout=ONE_FAILURE, verify_rc=1)), \
         patch.object(svc, "_attempt_fix", side_effect=_stages_a_fix(svc, checkout)):
        result = svc.run_self_check()

    assert result["fixes_staged"] == 1 and result["fixes_verified"] == 0
    assert result["changes"][0]["verification"]["total_failures"] == 1
    fix = PendingFix.query.one()
    assert fix.status == "proposed"
    assert fix.fix_description.endswith(
        "Scratch-copy test run: backend/tests/test_a.py fails with this fix applied (test_one).")
    assert list(temp_root.iterdir()) == []


def test_a_fix_that_no_longer_matches_runs_no_tests(app, checkout, temp_root, gates_open):
    from backend.models import PendingFix
    svc = _service()
    verify_runs = []

    with patch(f"{SVC}.subprocess.run", side_effect=_pytest_runs(checkout, verify_runs)), \
         patch.object(svc, "_attempt_fix",
                      side_effect=_stages_a_fix(svc, checkout, old_text="    return 7\n")):
        result = svc.run_self_check()

    assert verify_runs == []
    assert result["fixes_verified"] == 0
    fix = PendingFix.query.one()
    assert "not run (pending fix #" in fix.fix_description
    assert "no longer matches backend/a.py" in fix.fix_description
    assert list(temp_root.iterdir()) == []
    assert len(_worktrees(checkout)) == 1


def test_a_failure_with_no_test_file_is_not_verified(app, checkout):
    from backend.models import PendingFix
    svc = _service()
    fix = PendingFix(file_path=str(checkout / "backend" / "a.py"), original_content="x",
                     proposed_new_content="y", proposed_diff="", status="proposed")
    with patch(f"{SVC}.subprocess.run", side_effect=AssertionError("nothing is run")):
        result = svc._verify_fix(["unknown"], [fix])
    assert result["all_passed"] is False
    assert result["error"] == "no failing test file to run"


def test_test_files_outside_the_checkout_are_not_run():
    from backend.services.self_improvement_service import SelfImprovementService
    runnable = SelfImprovementService._runnable_test_files(
        ["backend/tests/test_a.py", "/elsewhere/test_b.py", "../test_c.py",
         "backend/tests/test_a.py", "unknown", ""])
    assert runnable == ["backend/tests/test_a.py"]


# ---- a run that raises is closed ------------------------------------------------

def test_a_self_check_that_breaks_the_session_still_closes_its_run(app, checkout, gates_open):
    from backend.models import db, PendingFix
    svc = _service()

    def broken_write(failure, message=None):
        db.session.add(PendingFix(run_id=svc._current_run_id, file_path=None, proposed_diff="x"))
        db.session.flush()  # file_path is NOT NULL: IntegrityError, the session needs a rollback

    with patch(f"{SVC}.subprocess.run", side_effect=_pytest_runs(checkout, [])), \
         patch.object(svc, "_attempt_fix", side_effect=broken_write):
        result = svc.run_self_check()

    assert result["success"] is False
    run = _latest_run()
    assert run.status == "failed"
    assert run.error_message.startswith("IntegrityError")
    assert run.duration_seconds is not None
    assert PendingFix.query.count() == 0
    assert svc._running is False


def test_a_directed_run_that_raises_closes_its_run_as_failed(app, gates_open):
    svc = _service()
    with patch.object(svc, "_attempt_fix", side_effect=RuntimeError("agent crashed")):
        result = svc.submit_directed_task("tidy x", ["x.py"])

    assert result == {"success": False, "reason": "agent crashed"}
    run = _latest_run()
    assert run.trigger == "directed"
    assert run.status == "failed"
    assert run.error_message == "RuntimeError: agent crashed"
    assert svc._running is False and svc._current_run_id is None


def test_a_failure_that_passes_on_a_re_run_is_left_alone(app, checkout):
    """Model-driven tests fail now and then; only a failure that repeats reaches the agent."""
    svc = _service()
    attempts = []
    checkout_runs = []

    def run(cmd, *args, **kwargs):
        if "pytest" not in cmd:
            return REAL_RUN(cmd, *args, **kwargs)
        checkout_runs.append(cmd)
        if len(checkout_runs) == 1:
            return MagicMock(returncode=1, stdout=ONE_FAILURE, stderr="")
        return MagicMock(returncode=0, stdout="1 passed\n", stderr="")

    with patch(f"{SVC}.subprocess.run", side_effect=run), \
         patch.object(svc, "_attempt_fix", side_effect=lambda f, message=None: attempts.append(f) or None):
        svc.run_self_check()

    assert attempts == []
    assert checkout_runs[1][3:4] == ["backend/tests/test_a.py::test_one"]
    from backend.models import SelfImprovementRun
    run_row = SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first()
    before = json.loads(run_row.test_results_before)
    assert before["total_failures"] == 0
    assert [f["test_name"] for f in before["flaky"]] == ["test_one"]
