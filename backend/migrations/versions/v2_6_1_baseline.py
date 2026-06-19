"""v2.6.1 consolidated baseline — the schema floor of record.

This single revision replaces the entire pre-v2.6.1 chain (v2_5_2_full .. a1b2c3d4e5f6,
13 revisions). That chain could no longer build a database from empty — `003`
referenced ``interconnector_nodes`` before any revision created it, so a fresh
``alembic upgrade head`` died partway. The live database had been materialised
out-of-band and stamped, leaving the chain unable to reproduce it.

The baseline is the **exact** current production schema, captured from the live
database at squash time (``pg_dump --schema-only``) and verified byte-for-byte:
an empty database upgraded to this revision is identical to live (59 tables,
minus the alembic_version bookkeeping table which Alembic manages itself).

The DDL lives in the sibling ``v2_6_1_baseline.sql`` so this file stays readable;
it is executed verbatim against the bound connection.

NOTE (tracked follow-up): ``models.py`` has drifted from this schema — it is
missing the ``batch_job_columns`` / ``batch_job_rows`` tables, has ``json`` where
live uses ``jsonb`` (x3), and omits ~26 server defaults. The schema of record is
THIS baseline; the ORM models should be reconciled to it separately so that
future ``--autogenerate`` runs are clean.

Forward-only: ``downgrade`` is a hard stop. We do not move backward past the floor.

Revision ID: v2_6_1_baseline
Revises: (base)
Create Date: 2026-06-19
"""
import os

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_6_1_baseline'
down_revision = None
branch_labels = None
depends_on = None

_SQL_FILE = os.path.join(os.path.dirname(__file__), 'v2_6_1_baseline.sql')


def upgrade():
    with open(_SQL_FILE, 'r') as f:
        ddl = f.read()
    # exec_driver_sql sends the script straight to the DBAPI, which executes the
    # multi-statement DDL blob as one batch (same as running the .sql via psql).
    op.get_bind().exec_driver_sql(ddl)


def downgrade():
    # Forward-only. There is no supported path back past the consolidated
    # baseline; drop and re-create the database to rebuild from scratch.
    raise NotImplementedError(
        "v2_6_1_baseline is the consolidated schema floor — downgrade is not supported "
        "(forward-only). Drop and re-create the database to rebuild from scratch."
    )
