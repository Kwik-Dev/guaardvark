"""Tests for master-authoritative Interconnector core-files registry."""
import json

import pytest

from backend.services.interconnector_sync_registry import (
    MasterCoreFilesRegistry,
    classify_against_master,
    ACTION_CREATE,
    ACTION_INSYNC,
    ACTION_UPDATE,
)


@pytest.fixture()
def registry(tmp_path):
    return MasterCoreFilesRegistry(tmp_path)


def test_classify_missing_local_is_create():
    master = [{"path": "backend/new.py", "hash": "bbb", "size": 1}]
    results = classify_against_master(master, {})
    assert results[0]["action"] == ACTION_CREATE


def test_classify_matching_hash_is_insync():
    master = [{"path": "backend/app.py", "hash": "aaa", "size": 1}]
    local = {"backend/app.py": {"path": "backend/app.py", "hash": "aaa"}}
    results = classify_against_master(master, local)
    assert results[0]["action"] == ACTION_INSYNC


def test_classify_differing_hash_is_update():
    master = [{"path": "backend/app.py", "hash": "zzz", "size": 1}]
    local = {"backend/app.py": {"path": "backend/app.py", "hash": "aaa"}}
    results = classify_against_master(master, local)
    assert results[0]["action"] == ACTION_UPDATE


def test_classify_resolve_local_finds_disk_file():
    master = [{"path": "backend/app.py", "hash": "aaa", "size": 1}]

    def resolve(path):
        if path == "backend/app.py":
            return {"path": path, "hash": "aaa", "modified_at": "2026-01-01T00:00:00"}
        return None

    results = classify_against_master(master, {}, resolve_local=resolve)
    assert results[0]["action"] == ACTION_INSYNC


def test_master_registry_refresh_and_persist(registry, tmp_path):
    scanned = [
        {"path": "scripts/start.sh", "hash": "deadbeef", "size": 100, "modified_at": "2026-01-01"},
        {"path": "backend/app.py", "hash": "cafebabe", "size": 200, "modified_at": "2026-01-02"},
    ]
    count = registry.refresh_from_scan(scanned, node_name="LLAMAX1", manifest_timestamp="2026-01-02")
    assert count == 2
    assert registry.path.exists()

    reloaded = MasterCoreFilesRegistry(tmp_path)
    summary = reloaded.summary()
    assert summary["tracked_files"] == 2
    data = json.loads(reloaded.path.read_text())
    assert data["files"]["scripts/start.sh"]["hash"] == "deadbeef"
