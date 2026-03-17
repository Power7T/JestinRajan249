"""Alembic migration environment for HostAI."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import the app's Base so Alembic can detect model changes for --autogenerate.
from web.db import Base  # noqa: F401 — registers all models via import side-effects
import web.models  # noqa: F401 — ensure all mapped classes are loaded

config = context.config

# Override sqlalchemy.url from environment (DATABASE_DIRECT_URL bypasses PgBouncer
# which doesn't support DDL statements required for migrations).
_db_url = os.getenv("DATABASE_DIRECT_URL") or os.getenv("DATABASE_URL", "")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in offline mode (generates SQL scripts without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
