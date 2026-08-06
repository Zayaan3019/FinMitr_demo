"""Alembic environment.

Migrations run as the *owner* role (``database_migration_url``), not as the
application role. That separation is what makes Row-Level Security effective:
the owner can create and alter tables, while the application role is an
ordinary, non-BYPASSRLS grantee that every policy applies to.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
import app.db.models  # noqa: F401,E402  (import registers all tables)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# psycopg (sync) driver for migrations; asyncpg is reserved for the app.
config.set_main_option("sqlalchemy.url", settings.sync_migration_url)


def include_object(obj, name, type_, reflected, compare_to):
    """Keep autogenerate away from partitions we manage explicitly."""
    if type_ == "table" and name.startswith("transactions_"):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_migration_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
