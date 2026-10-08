"""Pending-restart record and the restart fields /api/health reports.

Files only: every test works in tmp_path; no database, no Flask app.
"""

import hashlib
import json

import pytest

from backend.utils import update_state
from backend.utils.update_state import (
    PENDING_RESTART_RELPATH,
    classify_changed_paths,
    record_update_applied,
    restart_state,
)

BOOT = 1_000_000.0


def _pending(root):
    return json.loads((root / PENDING_RESTART_RELPATH).read_text())


@pytest.mark.parametrize(
    "path, expected",
    [
        ("backend/api/chat_api.py", (True, False, False)),
        ("backend/tests/test_x.py", (False, False, False)),
        ("frontend/src/App.jsx", (False, True, False)),
        ("backend/requirements.txt", (True, False, True)),
        ("backend/requirements-cv.txt", (True, False, True)),
        ("backend/constraints.txt", (True, False, True)),
        ("frontend/package-lock.json", (False, True, True)),
        ("plugins/comfyui/requirements.txt", (False, False, True)),
        ("scripts/check_portable.sh", (False, False, False)),
    ],
)
def test_classify_changed_paths(path, expected):
    flags = classify_changed_paths([path])
    assert (flags["backend_changed"], flags["frontend_changed"], flags["deps_changed"]) == expected


def test_record_writes_flags_and_merges_within_one_boot(tmp_path):
    record_update_applied(tmp_path, ["frontend/src/App.jsx"], now=BOOT + 10, boot_time=BOOT)
    first = _pending(tmp_path)
    assert first["files"] == 1
    assert first["frontend_changed"] and not first["backend_changed"]

    record_update_applied(tmp_path, ["backend/app.py", "frontend/src/App.jsx"], now=BOOT + 20, boot_time=BOOT)
    merged = _pending(tmp_path)
    assert merged["ts"] == BOOT + 20
    assert merged["files"] == 2
    assert merged["frontend_changed"] and merged["backend_changed"]
    assert not list(tmp_path.joinpath(PENDING_RESTART_RELPATH).parent.glob(".*.tmp"))


def test_record_from_before_the_restart_is_replaced(tmp_path):
    record_update_applied(tmp_path, ["backend/app.py"], now=BOOT - 5, boot_time=BOOT - 100)
    record_update_applied(tmp_path, ["frontend/src/App.jsx"], now=BOOT + 5, boot_time=BOOT)
    record = _pending(tmp_path)
    assert record["files"] == 1
    assert not record["backend_changed"]


def test_restart_state_with_nothing_pending(tmp_path):
    disk = update_state.read_disk_version()
    state = restart_state(tmp_path, disk, boot_time=BOOT)
    assert state["boot_id"] == update_state.BOOT_ID
    assert state["disk_version"] == disk
    assert state["restart_required"] is False
    assert state["restart_reason"] is None
    assert state["update"] is None


def test_frontend_only_update_needs_a_reload_not_a_restart(tmp_path):
    record_update_applied(tmp_path, ["frontend/src/App.jsx"], now=BOOT + 1, boot_time=BOOT)
    state = restart_state(tmp_path, update_state.read_disk_version(), boot_time=BOOT)
    assert state["restart_required"] is False
    assert state["update"]["frontend_changed"] is True
    assert state["update"]["files"] == 1


def test_backend_and_dependency_changes_need_a_restart(tmp_path):
    record_update_applied(
        tmp_path, ["backend/app.py", "frontend/package.json"], now=BOOT + 1, boot_time=BOOT
    )
    state = restart_state(tmp_path, update_state.read_disk_version(), boot_time=BOOT)
    assert state["restart_required"] is True
    assert "backend code changed" in state["restart_reason"]
    assert "dependencies changed" in state["restart_reason"]


def test_record_older_than_this_process_is_ignored(tmp_path):
    record_update_applied(tmp_path, ["backend/app.py"], now=BOOT - 1, boot_time=BOOT - 100)
    state = restart_state(tmp_path, update_state.read_disk_version(), boot_time=BOOT)
    assert state["restart_required"] is False
    assert state["update"] is None


def test_version_on_disk_differs_from_running(tmp_path):
    state = restart_state(tmp_path, "0.0.1", boot_time=BOOT)
    assert state["restart_required"] is True
    assert "0.0.1 is running" in state["restart_reason"]


def test_unreadable_record_is_treated_as_none(tmp_path):
    target = tmp_path / PENDING_RESTART_RELPATH
    target.parent.mkdir(parents=True)
    target.write_text("{not json")
    assert restart_state(tmp_path, update_state.read_disk_version(), boot_time=BOOT)["update"] is None


def test_atomic_apply_records_the_pending_restart(tmp_path, monkeypatch):
    from backend.services import interconnector_file_sync_service as svc_mod
    from backend import socketio_instance

    monkeypatch.setattr(svc_mod, "_SENTINEL_DIR", tmp_path / "sentinel")
    monkeypatch.setattr(svc_mod, "_SENTINEL_FILE", tmp_path / "sentinel" / ".sync_in_progress")
    emitted = []
    monkeypatch.setattr(socketio_instance.socketio, "emit", lambda event, data: emitted.append((event, data)))

    svc = svc_mod.InterconnectorFileSyncService()
    monkeypatch.setattr(svc, "get_project_root", lambda: tmp_path)

    content = "export default 1;\n"
    files = [{
        "path": "frontend/src/Thing.jsx",
        "content": content,
        "hash": hashlib.sha256(content.encode()).hexdigest(),
    }]
    ok, result = svc.apply_files_atomic(files, "remote_wins", create_backup=False)

    assert ok is True
    assert (tmp_path / "frontend/src/Thing.jsx").read_text() == content
    record = _pending(tmp_path)
    assert record["paths"] == ["frontend/src/Thing.jsx"]
    assert record["frontend_changed"] is True
    assert result["update_state"]["update"]["frontend_changed"] is True
    assert emitted and emitted[0][0] == "system:update_applied"


def test_apply_that_writes_nothing_records_nothing(tmp_path, monkeypatch):
    from backend.services import interconnector_file_sync_service as svc_mod

    monkeypatch.setattr(svc_mod, "_SENTINEL_DIR", tmp_path / "sentinel")
    monkeypatch.setattr(svc_mod, "_SENTINEL_FILE", tmp_path / "sentinel" / ".sync_in_progress")
    svc = svc_mod.InterconnectorFileSyncService()
    monkeypatch.setattr(svc, "get_project_root", lambda: tmp_path)

    content = "same\n"
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "same.txt").write_text(content)
    files = [{"path": "frontend/same.txt", "content": content,
              "hash": hashlib.sha256(content.encode()).hexdigest()}]
    ok, result = svc.apply_files_atomic(files, "remote_wins", create_backup=False)

    assert ok is True
    assert "update_state" not in result
    assert not (tmp_path / PENDING_RESTART_RELPATH).exists()
