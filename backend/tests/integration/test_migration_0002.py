"""Migration 0002: hypertable, pgvector index, worker role, monitoring function, RLS on new tenant table."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from vendorwake.db.session import tenant_session

pytestmark = pytest.mark.integration

INSUFFICIENT_PRIVILEGE = "42501"


def _sqlstate(exc: pytest.ExceptionInfo[DBAPIError]) -> str | None:
    return getattr(exc.value.orig, "sqlstate", None)


def test_source_snapshots_is_a_hypertable_on_retrieved_at(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT column_name FROM timescaledb_information.dimensions "
                    "WHERE hypertable_name = 'source_snapshots'"
                )
            )
            .scalars()
            .all()
        )
    assert rows == ["retrieved_at"]


def test_change_embeddings_has_hnsw_cosine_index(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        indexdef = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_change_embeddings_embedding'")
        ).scalar_one()
        dims = conn.execute(
            text(
                "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'change_embeddings'::regclass "
                "AND attname = 'embedding'"
            )
        ).scalar_one()
    assert "USING hnsw" in indexdef and "vector_cosine_ops" in indexdef
    assert dims == 384


def test_worker_role_is_unprivileged_and_global_only(worker_engine: Engine) -> None:
    with worker_engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert not any(row)
    with worker_engine.begin() as conn:  # may write global data
        conn.execute(text("UPDATE vendors SET updated_at = updated_at WHERE false"))
        conn.execute(
            text("INSERT INTO discovery_queries (vendor_id, query) SELECT id, 'q' FROM vendors WHERE false")
        )
    # no DELETE on global tables, and no access to tenant tables at all
    for sql in ("DELETE FROM vendors WHERE name = 'nothing'", "SELECT count(*) FROM users"):
        with pytest.raises(DBAPIError) as exc, worker_engine.begin() as conn:
            conn.execute(text(sql))
        assert _sqlstate(exc) == INSUFFICIENT_PRIVILEGE, sql


@pytest.mark.parametrize("table", ["source_snapshots", "discovery_queries", "change_embeddings"])
def test_app_role_reads_but_cannot_write_new_global_tables(app_engine: Engine, table: str) -> None:
    with app_engine.connect() as conn:
        conn.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        with pytest.raises(DBAPIError) as exc:
            conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608
    assert _sqlstate(exc) == INSUFFICIENT_PRIVILEGE


def test_monitoring_function_is_the_only_vendor_write_for_app(
    owner_engine: Engine, user_id: uuid.UUID
) -> None:
    name = f"vendor-{uuid.uuid4().hex[:6]}"
    try:
        with tenant_session(user_id) as s:
            s.execute(
                text(
                    "SELECT vendorwake_set_vendor_monitoring("
                    ":n, 'active', 'monitor-x', 'cat', NULL, '[\"a.test\"]')"
                ),
                {"n": name},
            )
        with owner_engine.connect() as conn:
            row = conn.execute(
                text("SELECT monitoring_status, schedule_id, official_domains FROM vendors WHERE name = :n"),
                {"n": name},
            ).one()
        assert tuple(row) == ("active", "monitor-x", ["a.test"])
        with pytest.raises(DBAPIError) as exc, tenant_session(user_id) as s:
            s.execute(
                text("SELECT vendorwake_set_vendor_monitoring(:n, 'off', NULL, NULL, NULL, NULL)"),
                {"n": name},
            )
        assert _sqlstate(exc) == "22023"
        with pytest.raises(DBAPIError) as exc, tenant_session(user_id) as s:
            s.execute(text("UPDATE vendors SET monitoring_status = 'paused' WHERE name = :n"), {"n": name})
        assert _sqlstate(exc) == INSUFFICIENT_PRIVILEGE
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text("DELETE FROM vendors WHERE name = :n"), {"n": name})


def test_investigation_steps_are_tenant_isolated(two_tenants: dict[str, uuid.UUID]) -> None:
    with tenant_session(two_tenants["user_a"]) as s:
        s.execute(
            text(
                "INSERT INTO investigation_steps (user_id, investigation_id, step, status, output) "
                "VALUES (:u, :i, 'impact_assessment', 'completed', '{}')"
            ),
            {"u": two_tenants["user_a"], "i": two_tenants["inv_a"]},
        )
    with tenant_session(two_tenants["user_b"]) as s:
        assert s.execute(text("SELECT count(*) FROM investigation_steps")).scalar_one() == 0
        with pytest.raises(DBAPIError) as exc:
            s.execute(
                text(
                    "INSERT INTO investigation_steps (user_id, investigation_id, step, status) "
                    "VALUES (:u, :i, 'x', 'completed')"
                ),
                {"u": two_tenants["user_a"], "i": two_tenants["inv_a"]},
            )
        assert _sqlstate(exc) == INSUFFICIENT_PRIVILEGE
