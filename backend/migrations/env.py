"""Alembic environment.

The URL comes from settings (VW_MIGRATION_DATABASE_URL: the schema-owner role), never from alembic.ini.
Callers (e.g. tests) may pass an open connection via ``config.attributes["connection"]``.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine
from sqlalchemy.pool import NullPool

import vendorwake.db.models  # noqa: F401  (registers every table on Base.metadata)
from vendorwake.core.config import get_settings
from vendorwake.db.base import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return get_settings().migration_database_url.get_secret_value()


def _configure(**kwargs: object) -> None:
    context.configure(target_metadata=target_metadata, compare_type=True, **kwargs)


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_with(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_with(connection)
        return
    engine = create_engine(_url(), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            _run_with(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
