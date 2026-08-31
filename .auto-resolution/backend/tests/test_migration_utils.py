"""Tests for migration_utils — stale version prune after interconnector sync."""
from pathlib import Path

import pytest

from backend.utils.migration_utils import (
    PREFERRED_BASELINE_REVISION,
    prune_stale_migration_versions,
)


def _write_revision(path: Path, revision: str, down_revision) -> None:
    down = repr(down_revision)
    path.write_text(
        f'revision = "{revision}"\n'
        f"down_revision = {down}\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        "\n"
        "def upgrade():\n"
        "    pass\n"
        "\n"
        "def downgrade():\n"
        "    pass\n"
    )


@pytest.fixture
def migrations_tree(tmp_path):
    migrations = tmp_path / "backend" / "migrations"
    versions = migrations / "versions"
    versions.mkdir(parents=True)
    (versions / "__init__.py").write_text("")
    (migrations / "alembic.ini").write_text(
        "[alembic]\nscript_location = .\n"
    )
    (migrations / "env.py").write_text("# test env\n")
    return migrations


def test_prune_removes_orphan_heads_prefers_baseline(migrations_tree):
    versions = migrations_tree / "versions"
    _write_revision(versions / "002_full_schema.py", "002_full_schema", None)
    _write_revision(
        versions / "v2_6_1_baseline.py",
        PREFERRED_BASELINE_REVISION,
        None,
    )

    result = prune_stale_migration_versions(str(migrations_tree))

    assert "002_full_schema.py" in result["removed"]
    assert "v2_6_1_baseline.py" in result["kept"]
    assert result["heads_after"] == [PREFERRED_BASELINE_REVISION]


def test_prune_authoritative_paths_from_master_manifest(migrations_tree):
    versions = migrations_tree / "versions"
    _write_revision(versions / "002_full_schema.py", "002_full_schema", None)
    _write_revision(
        versions / "v2_6_1_baseline.py",
        PREFERRED_BASELINE_REVISION,
        None,
    )

    result = prune_stale_migration_versions(
        str(migrations_tree),
        authoritative_paths=["backend/migrations/versions/v2_6_1_baseline.py"],
    )

    assert result["removed"] == ["002_full_schema.py"]
    assert (versions / "002_full_schema.py").exists() is False
    assert (versions / "v2_6_1_baseline.py").exists() is True


def test_prune_keeps_chained_revisions(migrations_tree):
    versions = migrations_tree / "versions"
    _write_revision(versions / "v2_6_1_baseline.py", "base_rev", None)
    _write_revision(versions / "v2_6_2_next.py", "next_rev", "base_rev")
    _write_revision(versions / "002_full_schema.py", "002_full_schema", None)

    result = prune_stale_migration_versions(str(migrations_tree))

    assert "002_full_schema.py" in result["removed"]
    assert "v2_6_1_baseline.py" in result["kept"]
    assert "v2_6_2_next.py" in result["kept"]
    assert result["heads_after"] == ["next_rev"]
