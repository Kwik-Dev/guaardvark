"""Synced files are written atomically, frontend last.

Files only: every test works in tmp_path; no database, no Flask app.
"""

import hashlib
import os
import stat

import pytest

from backend.services import interconnector_file_sync_service as svc_mod
from backend.services.interconnector_file_sync_service import (
    SYNC_TEMP_SUFFIX,
    InterconnectorFileSyncService,
)


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(svc_mod, "_SENTINEL_DIR", tmp_path / "sentinel")
    monkeypatch.setattr(svc_mod, "_SENTINEL_FILE", tmp_path / "sentinel" / ".sync_in_progress")
    service = InterconnectorFileSyncService()
    monkeypatch.setattr(service, "get_project_root", lambda: tmp_path)
    return service


def _leftovers(directory):
    return [p.name for p in directory.iterdir() if p.name.endswith(SYNC_TEMP_SUFFIX)]


def test_new_file_gets_content_and_the_umask_default(svc, tmp_path):
    reference = tmp_path / "reference.txt"
    reference.write_text("x")
    target = tmp_path / "new.js"

    svc._write_file(target, "export const a = 1;\n")

    assert target.read_text() == "export const a = 1;\n"
    assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(reference.stat().st_mode)
    assert _leftovers(tmp_path) == []


def test_existing_file_is_replaced_and_keeps_its_mode(svc, tmp_path):
    target = tmp_path / "start.sh"
    target.write_text("#!/bin/sh\necho old\n")
    os.chmod(target, 0o755)
    old_inode = target.stat().st_ino

    svc._write_file(target, "#!/bin/sh\necho new\n")

    assert target.read_text() == "#!/bin/sh\necho new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert target.stat().st_ino != old_inode, "renamed into place, not rewritten in place"
    assert _leftovers(tmp_path) == []


def test_bytes_content(svc, tmp_path):
    target = tmp_path / "icon.png"
    svc._write_file(target, b"\x89PNG\r\n\x1a\n")
    assert target.read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_temp_file_is_a_dotfile_beside_the_target(svc, tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(svc_mod.os, "replace", spy)
    target = tmp_path / "src" / "App.jsx"
    target.parent.mkdir()
    svc._write_file(target, "x")

    (src, dst), = seen
    assert dst == str(target)
    assert os.path.dirname(src) == str(target.parent)
    assert os.path.basename(src).startswith(".App.jsx.")
    assert src.endswith(SYNC_TEMP_SUFFIX)


def test_failed_write_leaves_the_original_and_no_temp(svc, tmp_path):
    target = tmp_path / "keep.py"
    target.write_text("original\n")

    # A lone surrogate fails at encode time, after the temporary file exists.
    with pytest.raises(UnicodeEncodeError):
        svc._write_file(target, "half \ud800 written")

    assert target.read_text() == "original\n"
    assert _leftovers(tmp_path) == []


def test_temp_files_are_never_synced(svc):
    assert svc.should_exclude_file(f"frontend/src/.App.jsx.123.abcd1234{SYNC_TEMP_SUFFIX}")
    assert not svc.should_exclude_file("frontend/src/App.jsx")


def test_apply_writes_frontend_last_and_the_vite_config_at_the_end(svc, tmp_path, monkeypatch):
    order = []
    real_write = svc._write_file

    def record(path, content):
        order.append(path.relative_to(tmp_path).as_posix())
        real_write(path, content)

    monkeypatch.setattr(svc, "_write_file", record)

    def entry(path):
        content = f"// {path}\n"
        return {"path": path, "content": content, "hash": hashlib.sha256(content.encode()).hexdigest()}

    files = [
        entry("frontend/vite.config.js"),
        entry("frontend/src/App.jsx"),
        entry("backend/api/x_api.py"),
        entry("frontend/src/main.jsx"),
        entry("scripts/tool.sh"),
    ]
    ok, _ = svc.apply_files_atomic(files, "remote_wins", create_backup=False)

    assert ok is True
    assert order == [
        "backend/api/x_api.py",
        "scripts/tool.sh",
        "frontend/src/App.jsx",
        "frontend/src/main.jsx",
        "frontend/vite.config.js",
    ]
