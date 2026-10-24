"""Engine helpers: health ping and tenant_session argument handling."""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import text

from vendorwake.db.session import get_engine, ping, tenant_session

pytestmark = pytest.mark.integration


def test_ping_true_against_database(migrated: None) -> None:
    assert ping() is True


def test_ping_false_against_closed_port() -> None:
    started = time.monotonic()
    assert ping("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none") is False
    assert time.monotonic() - started < 5


def test_ping_false_against_unroutable_host_within_timeout() -> None:
    # TEST-NET-1 (RFC 5737) is never routed, so this exercises the connect timeout rather than a refusal.
    started = time.monotonic()
    assert ping("postgresql+psycopg://nobody:nothing@192.0.2.1:5432/none") is False
    assert time.monotonic() - started < 5


def test_engine_connects_as_app_role(migrated: None) -> None:
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT current_user")).scalar_one() == "vendorwake_app"


def test_tenant_session_requires_uuid() -> None:
    with pytest.raises(TypeError), tenant_session("not-a-uuid"):  # type: ignore[arg-type]
        pass


def test_tenant_session_sets_transaction_local_scope(migrated: None) -> None:
    user_id = uuid.uuid4()
    with tenant_session(user_id) as s:
        scope = s.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar_one()
    assert scope == str(user_id)
