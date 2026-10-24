"""Row-level security and tenant-consistency checks, exercised as the real app role."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from vendorwake.db.models import TENANT_TABLES
from vendorwake.db.session import tenant_session, upsert_user

pytestmark = pytest.mark.integration

INSUFFICIENT_PRIVILEGE = "42501"  # also raised for "new row violates row-level security policy"
FOREIGN_KEY_VIOLATION = "23503"


def _sqlstate(exc: pytest.ExceptionInfo[DBAPIError]) -> str | None:
    return getattr(exc.value.orig, "sqlstate", None)


def test_app_role_is_not_privileged(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT current_user AS name, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    assert row.name == "vendorwake_app"
    assert not (row.rolsuper or row.rolbypassrls or row.rolcreatedb or row.rolcreaterole)


@pytest.mark.parametrize("table", ["repositories", "investigations", "users"])
def test_app_role_cannot_disable_rls(app_engine: Engine, table: str) -> None:
    with app_engine.connect() as conn, pytest.raises(DBAPIError) as exc:
        conn.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    assert _sqlstate(exc) == INSUFFICIENT_PRIVILEGE  # must be owner of table


def test_tenant_sees_only_own_rows(two_tenants: dict[str, uuid.UUID]) -> None:
    with tenant_session(two_tenants["user_a"]) as s:
        assert set(s.execute(text("SELECT id FROM repositories")).scalars()) == {two_tenants["repo_a"]}
        assert set(s.execute(text("SELECT id FROM investigations")).scalars()) == {two_tenants["inv_a"]}
        assert set(s.execute(text("SELECT id FROM users")).scalars()) == {two_tenants["user_a"]}
        # Global data is shared.
        assert s.execute(
            text("SELECT count(*) FROM vendors WHERE id = :v"), {"v": two_tenants["vendor"]}
        ).scalar_one()
    with tenant_session(two_tenants["user_b"]) as s:
        assert set(s.execute(text("SELECT id FROM repositories")).scalars()) == {two_tenants["repo_b"]}


def test_cannot_touch_other_tenant_rows(two_tenants: dict[str, uuid.UUID]) -> None:
    with tenant_session(two_tenants["user_a"]) as s:
        updated = s.execute(
            text("UPDATE repositories SET repository_name = 'pwned' WHERE id = :r"),
            {"r": two_tenants["repo_b"]},
        ).rowcount
        deleted = s.execute(
            text("DELETE FROM investigations WHERE id = :i"), {"i": two_tenants["inv_b"]}
        ).rowcount
    assert (updated, deleted) == (0, 0)


def test_insert_with_other_tenant_user_id_is_rejected(two_tenants: dict[str, uuid.UUID]) -> None:
    with pytest.raises(DBAPIError) as exc, tenant_session(two_tenants["user_a"]) as s:
        s.execute(
            text(
                "INSERT INTO repositories (user_id, external_repository_id, repository_name) "
                "VALUES (:u, 'evil', 'org/evil')"
            ),
            {"u": two_tenants["user_b"]},
        )
    assert _sqlstate(exc) == INSUFFICIENT_PRIVILEGE
    assert "row-level security" in str(exc.value.orig)


def test_reassigning_own_row_to_other_tenant_is_rejected(two_tenants: dict[str, uuid.UUID]) -> None:
    with pytest.raises(DBAPIError) as exc, tenant_session(two_tenants["user_a"]) as s:
        s.execute(
            text("UPDATE repositories SET user_id = :b WHERE id = :r"),
            {"b": two_tenants["user_b"], "r": two_tenants["repo_a"]},
        )
    assert _sqlstate(exc) == INSUFFICIENT_PRIVILEGE


def test_unscoped_queries_return_nothing(app_engine: Engine, two_tenants: dict[str, uuid.UUID]) -> None:
    with app_engine.connect() as conn:
        # Never set on this connection: current_setting(..., true) is NULL.
        for table in TENANT_TABLES:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0, table  # noqa: S608
        conn.rollback()
        # After a transaction-local scope ends, the setting reads back as '' rather than NULL; the policies
        # must still fail closed instead of raising on ''::uuid.
        with conn.begin():
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, true)"), {"u": str(two_tenants["user_a"])}
            )
            assert conn.execute(text("SELECT count(*) FROM repositories")).scalar_one() == 1
        assert conn.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar_one() == ""
        assert conn.execute(text("SELECT count(*) FROM repositories")).scalar_one() == 0


def test_scope_survives_commit_inside_tenant_session(two_tenants: dict[str, uuid.UUID]) -> None:
    with tenant_session(two_tenants["user_a"]) as s:
        s.commit()
        assert set(s.execute(text("SELECT id FROM repositories")).scalars()) == {two_tenants["repo_a"]}


def test_app_role_cannot_write_global_tables(two_tenants: dict[str, uuid.UUID]) -> None:
    with pytest.raises(DBAPIError) as exc, tenant_session(two_tenants["user_a"]) as s:
        s.execute(text("INSERT INTO vendors (name) VALUES (:n)"), {"n": f"x-{uuid.uuid4().hex}"})
    assert _sqlstate(exc) == INSUFFICIENT_PRIVILEGE


# ---- parent/child user_id consistency (composite foreign keys) -------------------------------------


def test_child_with_mismatched_user_id_is_rejected_by_owner(
    owner_engine: Engine, two_tenants: dict[str, uuid.UUID]
) -> None:
    # The owner here is a superuser and bypasses RLS, so only the composite FK can stop this.
    with pytest.raises(DBAPIError) as exc, owner_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO investigations (user_id, change_id, repository_id) VALUES (:u, :c, :r)"),
            {"u": two_tenants["user_b"], "c": two_tenants["change"], "r": two_tenants["repo_a"]},
        )
    assert _sqlstate(exc) == FOREIGN_KEY_VIOLATION

    with pytest.raises(DBAPIError) as exc, owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO impact_evidence (user_id, investigation_id, evidence_type) "
                "VALUES (:u, :i, 'code_reference')"
            ),
            {"u": two_tenants["user_b"], "i": two_tenants["inv_a"]},
        )
    assert _sqlstate(exc) == FOREIGN_KEY_VIOLATION


def test_tenant_cannot_attach_child_to_other_tenants_parent(two_tenants: dict[str, uuid.UUID]) -> None:
    # Passes RLS (user_id is A) but the referenced investigation belongs to B.
    with pytest.raises(DBAPIError) as exc, tenant_session(two_tenants["user_a"]) as s:
        s.execute(
            text(
                "INSERT INTO recommendations (user_id, investigation_id, recommendation_type, description) "
                "VALUES (:u, :i, 'code_change', 'x')"
            ),
            {"u": two_tenants["user_a"], "i": two_tenants["inv_b"]},
        )
    assert _sqlstate(exc) == FOREIGN_KEY_VIOLATION


def test_tenant_can_write_consistent_children(two_tenants: dict[str, uuid.UUID]) -> None:
    with tenant_session(two_tenants["user_a"]) as s:
        s.execute(
            text(
                "INSERT INTO impact_evidence "
                "(user_id, investigation_id, evidence_type, file_path, confidence) "
                "VALUES (:u, :i, 'code_reference', 'src/app.py', 0.9)"
            ),
            {"u": two_tenants["user_a"], "i": two_tenants["inv_a"]},
        )
        assert s.execute(text("SELECT count(*) FROM impact_evidence")).scalar_one() == 1
    with tenant_session(two_tenants["user_b"]) as s:
        assert s.execute(text("SELECT count(*) FROM impact_evidence")).scalar_one() == 0


def test_check_constraints(two_tenants: dict[str, uuid.UUID]) -> None:
    with pytest.raises(DBAPIError) as exc, tenant_session(two_tenants["user_a"]) as s:
        s.execute(
            text("UPDATE investigations SET impact_status = 'maybe' WHERE id = :i"),
            {"i": two_tenants["inv_a"]},
        )
    assert _sqlstate(exc) == "23514"  # check_violation


# ---- first-login user resolution -------------------------------------------------------------------


def test_upsert_user_is_idempotent_and_scoped(owner_engine: Engine) -> None:
    subject = f"test|upsert-{uuid.uuid4().hex[:8]}"
    try:
        first = upsert_user(subject, "first@example.test")
        again = upsert_user(subject, None)
        assert first == again
        with tenant_session(first) as s:
            assert s.execute(text("SELECT email FROM users")).scalars().all() == ["first@example.test"]
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE auth_provider_id = :s"), {"s": subject})
