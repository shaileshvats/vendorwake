"""Migrations apply, roll back and re-apply; the schema matches the ORM models; extensions are present."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text

from vendorwake.db.base import Base
from vendorwake.db.models import GLOBAL_TABLES, TENANT_TABLES

pytestmark = pytest.mark.integration


def test_upgrade_downgrade_upgrade(owner_engine: Engine, alembic_cfg: Config) -> None:
    cfg = alembic_cfg
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    with owner_engine.connect() as conn:
        remaining = conn.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(:t)"),
            {"t": list(TENANT_TABLES + GLOBAL_TABLES)},
        ).scalar_one()
    assert remaining == 0
    command.upgrade(cfg, "head")
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002"


def test_schema_matches_models(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diffs == []


def test_extensions_installed(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        names = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
        assert {"timescaledb", "vector"} <= names
        assert conn.execute(text("SELECT '[1,2,3]'::vector(3) <-> '[1,2,4]'::vector(3)")).scalar_one() == 1.0
        assert conn.execute(text("SELECT vector_dims('[1,2,3]'::vector(3))")).scalar_one() == 3


def test_rls_enabled_and_forced_on_tenant_tables(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace AND relname = ANY(:t)"
            ),
            {"t": list(TENANT_TABLES)},
        ).all()
    assert sorted(r.relname for r in rows) == sorted(TENANT_TABLES)
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
