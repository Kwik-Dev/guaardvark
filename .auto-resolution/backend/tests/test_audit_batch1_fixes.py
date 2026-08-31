"""Regression tests for the Batch-1 audit fixes (2026-05-30).

Each test targets a specific confirmed bug and is written to FAIL on the
pre-fix code and PASS after. See plan validated-forging-rabbit.md.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest


def test_production_swarm_imports_requests():
    """run_casting_director calls requests.get; the module must import `requests`
    (pre-fix: no import → NameError, silently swallowed → voices never fetched)."""
    import backend.tasks.production_swarm_tasks as m
    assert hasattr(m, "requests"), "production_swarm_tasks must import `requests`"


def test_self_improvement_interval_zero_does_not_crash_beat():
    """GUAARDVARK_SELF_IMPROVEMENT_INTERVAL=0 must not produce crontab(hour='*/0')
    (which raises and crashes beat startup). max(1, …) clamps it to '*/1'."""
    from celery import Celery
    from backend.tasks.self_improvement_tasks import schedule_self_improvement_tasks

    app = Celery("test_self_improve")
    with patch.dict("os.environ", {"GUAARDVARK_SELF_IMPROVEMENT_INTERVAL": "0"}):
        schedule_self_improvement_tasks(app)  # pre-fix: raises ValueError here
    sched = app.conf.beat_schedule["self-improvement-check"]["schedule"]
    assert sched._orig_hour == "*/1"


def test_backup_cleanup_iterates_dict_entries():
    """list_backups() yields dicts; the cleanup must read entry['name'] and prune
    old auto backups (pre-fix: str method on a dict → AttributeError, swallowed,
    nothing pruned)."""
    old = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d_%H%M%S")
    new = datetime.now().strftime("%Y%m%d_%H%M%S")
    listing = [
        {"name": f"auto_daily_{old}.zip", "size": 1, "type": "auto"},
        {"name": f"auto_daily_{new}.zip", "size": 1, "type": "auto"},
        {"name": "code_release_20200101.zip", "size": 1, "type": "code"},  # not auto → skipped
    ]
    delete_spy = MagicMock()
    with patch("backend.services.backup_service.create_data_backup", return_value="/tmp/x.zip"), \
         patch("backend.services.backup_service.list_backups", return_value=listing), \
         patch("backend.services.backup_service.delete_backup", delete_spy):
        from backend.tasks.backup_tasks import daily_backup
        daily_backup.apply()  # eager, synchronous

    deleted = [c.args[0] for c in delete_spy.call_args_list]
    assert f"auto_daily_{old}.zip" in deleted          # old one pruned
    assert f"auto_daily_{new}.zip" not in deleted       # recent kept
    assert "code_release_20200101.zip" not in deleted   # non-auto untouched


def test_executor_success_emits_progress_before_completed_write():
    """The success path must call the final update_progress(100) BEFORE the terminal
    update_task_status('completed') — otherwise update_progress (which writes
    'in-progress') flips the just-completed task back. Regression guard on ordering."""
    from backend.tasks.unified_task_executor import execute_unified_task
    src = inspect.getsource(execute_unified_task)
    pos_progress = src.index('update_progress(100, f"Completed:')
    pos_completed = src.index("update_task_status(task_id, 'completed', progress=100)")
    assert pos_progress < pos_completed, (
        "final update_progress(100) must precede the 'completed' status write"
    )
