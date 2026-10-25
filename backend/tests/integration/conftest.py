"""Fixtures for database integration tests.

Needs a database built from infra/postgres and two URLs:

* VW_MIGRATION_DATABASE_URL - schema owner (runs Alembic)
* VW_DATABASE_URL           - the RLS-restricted app role ``vendorwake_app``

Tests are skipped with a clear message when either is unset. See tests/integration/README.md.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import NullPool

BACKEND_DIR = Path(__file__).resolve().parents[2]

_MISSING = [name for name in ("VW_DATABASE_URL", "VW_MIGRATION_DATABASE_URL") if not os.environ.get(name)]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not _MISSING:
        return
    skip = pytest.mark.skip(
        reason=f"integration tests need {' and '.join(_MISSING)} (see backend/tests/integration/README.md)"
    )
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.path)).parents:
            item.add_marker(skip)


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _fresh_settings() -> Iterator[None]:
    # Settings are lru_cached; make sure they reflect the environment of this test run.
    from vendorwake.core.config import get_settings
    from vendorwake.db import session as db_session

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    yield
    db_session.get_engine().dispose()


@pytest.fixture(scope="session")
def alembic_cfg() -> Config:
    return alembic_config()


@pytest.fixture(scope="session")
def migrated(alembic_cfg: Config) -> None:
    """Bring the schema to head once per session (idempotent)."""
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(scope="session")
def owner_engine(migrated: None) -> Iterator[Engine]:
    engine = create_engine(os.environ["VW_MIGRATION_DATABASE_URL"], poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(migrated: None) -> Iterator[Engine]:
    engine = create_engine(os.environ["VW_DATABASE_URL"], poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture()
def two_tenants(owner_engine: Engine) -> Iterator[dict[str, uuid.UUID]]:
    """Seed user A and B, each with a repository and an investigation (as the owner/superuser)."""
    suffix = uuid.uuid4().hex[:8]
    with owner_engine.begin() as conn:
        vendor_id = conn.execute(
            text("INSERT INTO vendors (name) VALUES (:n) RETURNING id"), {"n": f"vendor-{suffix}"}
        ).scalar_one()
        change_id = conn.execute(
            text(
                "INSERT INTO vendor_changes (vendor_id, title) VALUES (:v, 'API v1 deprecated') RETURNING id"
            ),
            {"v": vendor_id},
        ).scalar_one()
        ids: dict[str, uuid.UUID] = {"vendor": vendor_id, "change": change_id}
        for label in ("a", "b"):
            user_id = conn.execute(
                text("INSERT INTO users (auth_provider_id, email) VALUES (:s, :e) RETURNING id"),
                {"s": f"test|{label}-{suffix}", "e": f"{label}@example.test"},
            ).scalar_one()
            repo_id = conn.execute(
                text(
                    "INSERT INTO repositories (user_id, external_repository_id, repository_name) "
                    "VALUES (:u, :x, :n) RETURNING id"
                ),
                {"u": user_id, "x": f"{label}-{suffix}", "n": f"org/{label}-{suffix}"},
            ).scalar_one()
            inv_id = conn.execute(
                text(
                    "INSERT INTO investigations (user_id, change_id, repository_id) "
                    "VALUES (:u, :c, :r) RETURNING id"
                ),
                {"u": user_id, "c": change_id, "r": repo_id},
            ).scalar_one()
            ids.update({f"user_{label}": user_id, f"repo_{label}": repo_id, f"inv_{label}": inv_id})
    yield ids
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": ids["user_a"], "b": ids["user_b"]})
        conn.execute(text("DELETE FROM vendor_changes WHERE id = :c"), {"c": change_id})
        conn.execute(text("DELETE FROM vendors WHERE id = :v"), {"v": vendor_id})


# ---- 0002: worker role, workflow activities, routes ------------------------------------------------


@pytest.fixture(scope="session")
def worker_engine(migrated: None) -> Iterator[Engine]:
    url = os.environ.get("VW_WORKER_DATABASE_URL")
    if not url:
        pytest.skip("needs VW_WORKER_DATABASE_URL (vendorwake_worker role; see migrations 0002)")
    from vendorwake.db import session as db_session

    db_session.get_worker_engine.cache_clear()
    engine = create_engine(url, poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture()
def user_id(owner_engine: Engine) -> Iterator[uuid.UUID]:
    """A fresh tenant; deleting it cascades to its repositories/investigations."""
    from vendorwake.db.session import upsert_user

    subject = f"test|wf-{uuid.uuid4().hex[:8]}"
    uid = upsert_user(subject, None)
    yield uid
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})


@pytest.fixture()
def clean_vendor(owner_engine: Engine) -> Iterator[str]:
    """Remove the fake vendor (and, by cascade, its snapshots/changes/embeddings) after a test."""
    from tests.integration.fakes import VENDOR

    def wipe() -> None:
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM investigations WHERE change_id IN (SELECT c.id FROM vendor_changes c "
                    "JOIN vendors v ON v.id = c.vendor_id WHERE v.name = :n)"
                ),
                {"n": VENDOR},
            )
            conn.execute(
                text("DELETE FROM integrations WHERE vendor_id IN (SELECT id FROM vendors WHERE name = :n)"),
                {"n": VENDOR},
            )
            conn.execute(text("DELETE FROM vendors WHERE name = :n"), {"n": VENDOR})

    wipe()
    yield VENDOR
    wipe()
