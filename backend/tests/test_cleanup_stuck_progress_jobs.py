"""The progress-store cleanup script the Celery task and system API shell out to."""

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "cleanup_stuck_progress_jobs.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("cleanup_stuck_progress_jobs", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script():
    return _load_script()


def _write_job(progress_dir: Path, job_id: str, status: str, age_hours: float, is_complete: bool):
    job_dir = progress_dir / job_id
    job_dir.mkdir(parents=True)
    stamp = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    (job_dir / "metadata.json").write_text(
        json.dumps({
            "job_id": job_id,
            "process_type": "indexing",
            "status": status,
            "progress": 40,
            "message": "working",
            "start_time_utc": stamp,
            "last_update_utc": stamp,
            "is_complete": is_complete,
        }),
        encoding="utf-8",
    )
    return job_dir


@pytest.fixture
def store(tmp_path):
    progress_dir = tmp_path / ".progress_jobs"
    progress_dir.mkdir()
    _write_job(progress_dir, "indexing_stuck", "processing", age_hours=48, is_complete=False)
    _write_job(progress_dir, "indexing_live", "processing", age_hours=0.5, is_complete=False)
    _write_job(progress_dir, "indexing_done_old", "complete", age_hours=24 * 30, is_complete=True)
    _write_job(progress_dir, "indexing_done_recent", "complete", age_hours=2, is_complete=True)
    (progress_dir / "indexing_no_metadata").mkdir()
    return progress_dir


def test_classify_separates_stuck_from_live(script, store):
    removable, kept = script.classify_jobs(store)

    assert {j["job_id"] for j in removable} == {"indexing_stuck", "indexing_no_metadata"}
    assert {j["job_id"] for j in kept} == {
        "indexing_live",
        "indexing_done_old",
        "indexing_done_recent",
    }


def test_clean_completed_adds_only_old_finished_jobs(script, store):
    removable, _ = script.classify_jobs(store, clean_completed=True, max_age_days=7)

    assert "indexing_done_old" in {j["job_id"] for j in removable}
    assert "indexing_done_recent" not in {j["job_id"] for j in removable}


def test_dry_run_removes_nothing_and_reports_zero(store, tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "Cleaned up 0 orphaned jobs" in result.stdout
    assert "2 orphaned job(s) would be removed" in result.stdout
    assert sorted(p.name for p in store.iterdir()) == [
        "indexing_done_old",
        "indexing_done_recent",
        "indexing_live",
        "indexing_no_metadata",
        "indexing_stuck",
    ]


def test_execute_removes_and_prints_the_parsed_summary_line(store, tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path), "--execute"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "Cleaned up 2 orphaned jobs" in result.stdout
    assert not (store / "indexing_stuck").exists()
    assert (store / "indexing_live").exists()


def test_summary_line_matches_the_caller_parser(store, tmp_path):
    """backend/api/system_api.py and backend/tasks/cleanup_tasks.py parse this shape."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path), "--execute"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
    )

    cleaned_count = None
    for line in result.stdout.split("\n"):
        if "Cleaned up" in line and "orphaned jobs" in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if part.isdigit() and i + 1 < len(parts) and "orphaned" in parts[i + 1]:
                    cleaned_count = int(part)
                    break

    assert cleaned_count == 2


def test_missing_store_is_not_an_error(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--output-dir", str(tmp_path / "nothing-here")],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "Cleaned up 0 orphaned jobs" in result.stdout


def test_system_maintenance_handler_runs_cleanup_script_and_parses_count(monkeypatch):
    from backend.services.task_handlers.system_maintenance_handler import SystemMaintenanceHandler
    from backend.services.task_handlers.base_handler import TaskResultStatus

    handler = SystemMaintenanceHandler()
    called_cmds = []

    def mock_run(cmd, capture_output=True, text=True, timeout=60):
        called_cmds.append(cmd)
        class DummyResult:
            returncode = 0
            stdout = "Kept 1 job(s)\nCleaned up 3 orphaned jobs\n"
            stderr = ""
        return DummyResult()

    monkeypatch.setattr(subprocess, "run", mock_run)

    progress = []
    start_time = datetime.now(timezone.utc)
    result = handler._cleanup_progress_jobs(
        task=None,
        config={"max_age_days": 2, "clean_completed": True, "dry_run": False},
        progress_callback=lambda pct, msg, data: progress.append((pct, msg)),
        started_at=start_time,
    )

    assert result.status == TaskResultStatus.SUCCESS
    assert result.message == "Cleaned 3 stuck progress jobs"
    assert result.output_data["cleaned_count"] == 3
    assert result.output_data["dry_run"] is False
    assert result.output_data["max_age_hours"] == 48

    cmd = called_cmds[0]
    assert "--execute" in cmd
    assert "--clean-completed" in cmd
    assert "--max-age-hours" in cmd
    assert "48" in cmd


def test_system_maintenance_handler_dry_run_omits_execute(monkeypatch):
    from backend.services.task_handlers.system_maintenance_handler import SystemMaintenanceHandler
    from backend.services.task_handlers.base_handler import TaskResultStatus

    handler = SystemMaintenanceHandler()
    called_cmds = []

    def mock_run(cmd, capture_output=True, text=True, timeout=60):
        called_cmds.append(cmd)
        class DummyResult:
            returncode = 0
            stdout = "Dry run: 2 orphaned job(s) would be removed\nCleaned up 0 orphaned jobs\n"
            stderr = ""
        return DummyResult()

    monkeypatch.setattr(subprocess, "run", mock_run)

    start_time = datetime.now(timezone.utc)
    result = handler._cleanup_progress_jobs(
        task=None,
        config={"dry_run": True},
        progress_callback=lambda pct, msg, data: None,
        started_at=start_time,
    )

    assert result.status == TaskResultStatus.SUCCESS
    assert result.output_data["cleaned_count"] == 0
    assert result.output_data["dry_run"] is True

    cmd = called_cmds[0]
    assert "--execute" not in cmd


def test_system_maintenance_handler_handles_script_failure(monkeypatch):
    from backend.services.task_handlers.system_maintenance_handler import SystemMaintenanceHandler
    from backend.services.task_handlers.base_handler import TaskResultStatus

    handler = SystemMaintenanceHandler()

    def mock_run(cmd, capture_output=True, text=True, timeout=60):
        class DummyResult:
            returncode = 1
            stdout = ""
            stderr = "Permission denied"
        return DummyResult()

    monkeypatch.setattr(subprocess, "run", mock_run)

    start_time = datetime.now(timezone.utc)
    result = handler._cleanup_progress_jobs(
        task=None,
        config={"dry_run": False},
        progress_callback=lambda pct, msg, data: None,
        started_at=start_time,
    )

    assert result.status == TaskResultStatus.FAILED
    assert "Permission denied" in result.error_message
