import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import DATABASE_URL as DEFAULT_DATABASE_URL
from models import db

config = context.config
fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    env_url = os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get(
        "DATABASE_URL"
    )
    if env_url:
        config.set_main_option("sqlalchemy.url", env_url)
    else:
        config.set_main_option("sqlalchemy.url", "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark")

# Ensure the database URL is provided for Alembic when invoked via Flask.
# Prefer an explicit DATABASE_URL environment variable, falling back to
# the project's configured default.
database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = db.metadata

# Tables that live in the database but are intentionally NOT ORM-modelled — they
# are created and owned at runtime by feature code (raw CREATE TABLE IF NOT EXISTS),
# not by models.py. Without this, --autogenerate would see them as "extra" and
# propose dropping them on every run. Keep this list in sync with the runtime
# table creators it names.
RUNTIME_MANAGED_TABLES = {
    "batch_job_columns",   # backend/utils/batch_job_db.py
    "batch_job_rows",      # backend/utils/batch_job_db.py
}


def include_object(object_, name, type_, reflected, compare_to):
    """Exclude runtime-managed tables from autogenerate so they aren't
    proposed for removal (they have no ORM model by design)."""
    if type_ == "table" and name in RUNTIME_MANAGED_TABLES:
        return False
    return True


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # render_as_batch=True kept for migration compatibility
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
