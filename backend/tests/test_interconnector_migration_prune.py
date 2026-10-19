"""A sync batch that carries a new migration keeps the migrations it builds on.

Files only: the project root is tmp_path; no database, no Flask app.
"""

import hashlib

import pytest

from backend.services import interconnector_file_sync_service as svc_mod
from backend.services.interconnector_file_sync_service import InterconnectorFileSyncService

BASELINE = 'revision = "v2_6_1_baseline"\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n'
NEXT = 'revision = "v3_3_0_next"\ndown_revision = "v2_6_1_baseline"\nbranch_labels = None\ndepends_on = None\n'


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(svc_mod, "_SENTINEL_DIR", tmp_path / "sentinel")
    monkeypatch.setattr(svc_mod, "_SENTINEL_FILE", tmp_path / "sentinel" / ".sync_in_progress")
    service = InterconnectorFileSyncService()
    monkeypatch.setattr(service, "get_project_root", lambda: tmp_path)
    migrations = tmp_path / "backend" / "migrations"
    (migrations / "versions").mkdir(parents=True)
    (migrations / "alembic.ini").write_text("[alembic]\nscript_location = .\n")
    (migrations / "env.py").write_text("# test env\n")
    (migrations / "versions" / "__init__.py").write_text("")
    (migrations / "versions" / "v2_6_1_baseline.py").write_text(BASELINE)
    return service


def test_a_batch_with_only_the_new_migration_keeps_the_baseline(svc, tmp_path):
    entry = {"path": "backend/migrations/versions/v3_3_0_next.py", "content": NEXT,
             "hash": hashlib.sha256(NEXT.encode()).hexdigest()}

    ok, result = svc.apply_files_atomic([entry], "remote_wins", create_backup=False)

    assert ok is True, result
    versions = tmp_path / "backend" / "migrations" / "versions"
    assert (versions / "v2_6_1_baseline.py").read_text() == BASELINE
    assert (versions / "v3_3_0_next.py").read_text() == NEXT
    assert "migration_prune" not in result
