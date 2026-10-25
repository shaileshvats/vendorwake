"""Database engine and tenant-scoped sessions.

The API and workers connect as the non-owner app role (``VW_DATABASE_URL``). Row-level security policies
read the tenant from the transaction-local setting ``app.current_user_id``; :func:`tenant_session` sets it
(parameterised, via ``set_config(..., is_local => true)``) at the start of every transaction, so a pooled
connection never carries one tenant's scope into another request. Without the setting, tenant tables
return no rows (fail closed).
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Connection, Engine, TextClause, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker
from sqlalchemy.pool import NullPool

from vendorwake.core.config import get_settings

logger = logging.getLogger(__name__)

CURRENT_USER_SETTING = "app.current_user_id"
#: Auth0 subject used only to resolve or create the caller's own users row (see :func:`upsert_user`).
CURRENT_AUTH_SUBJECT_SETTING = "app.current_auth_subject"

_SET_USER_SQL = text("SELECT set_config('app.current_user_id', :uid, true)")
_SET_SUBJECT_SQL = text("SELECT set_config('app.current_auth_subject', :sub, true)")

PING_TIMEOUT_SECONDS = 2

#: Connection URL of the ``vendorwake_worker`` role (global writes: vendors, snapshots, changes, embeddings).
#: Read from the process environment only (not Settings / .env), so only the worker process ever has it.
WORKER_DATABASE_URL_ENV = "VW_WORKER_DATABASE_URL"


class WorkerDatabaseNotConfiguredError(RuntimeError):
    """VW_WORKER_DATABASE_URL is not set, so global (upstream) data cannot be written."""


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Process-wide engine for the app role (subject to row-level security)."""
    url = get_settings().database_url.get_secret_value()
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@lru_cache(maxsize=1)
def get_worker_engine() -> Engine:
    """Engine for the worker role. It may write the global tables and cannot see any tenant table."""
    url = os.environ.get(WORKER_DATABASE_URL_ENV, "").strip()
    if not url:
        raise WorkerDatabaseNotConfiguredError(f"{WORKER_DATABASE_URL_ENV} is not set")
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


@contextmanager
def worker_session() -> Iterator[Session]:
    """Session on the worker role for global (upstream) data. Commits on exit, rolls back on error."""
    session = Session(bind=get_worker_engine(), expire_on_commit=False)
    with session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


@lru_cache(maxsize=8)
def _ping_engine(url: str) -> Engine:
    # NullPool: a health check should open a fresh connection and never wait on the app's pool.
    return create_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": PING_TIMEOUT_SECONDS,
            "options": f"-c statement_timeout={PING_TIMEOUT_SECONDS * 1000}",
        },
    )


def ping(url: str | None = None) -> bool:
    """Return True if ``SELECT 1`` succeeds within ~2 s. Never raises (used by /api/health).

    ``url`` defaults to the app-role URL from settings.
    """
    try:
        target = url if url is not None else get_settings().database_url.get_secret_value()
        with _ping_engine(target).connect() as conn:
            return conn.execute(text("SELECT 1")).scalar_one() == 1
    except Exception as exc:  # health checks must not raise
        # Log only the exception type: driver messages can include host names, never needed here.
        logger.warning("database ping failed: %s", type(exc).__name__)
        return False


def _scoped_session(setting_sql: TextClause, params: dict[str, str]) -> Session:
    session = get_sessionmaker()()

    @event.listens_for(session, "after_begin")
    def _apply_scope(_session: Session, _tx: SessionTransaction, connection: Connection) -> None:
        # Runs at the start of *every* transaction of this session (including after a commit inside the
        # block), so the tenant scope can never silently fall off.
        connection.execute(setting_sql, params)

    return session


@contextmanager
def tenant_session(user_id: uuid.UUID) -> Iterator[Session]:
    """Yield a Session whose transactions are scoped to ``user_id`` by RLS.

    Commits on normal exit and rolls back on exception. Calling ``session.commit()`` inside the block is
    allowed: the next transaction is re-scoped automatically.
    """
    if not isinstance(user_id, uuid.UUID):
        raise TypeError("user_id must be a uuid.UUID")
    session = _scoped_session(_SET_USER_SQL, {"uid": str(user_id)})
    with session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def iter_tenant_session(user_id: uuid.UUID) -> Iterator[Session]:
    """Generator form of :func:`tenant_session`, for use as a FastAPI dependency body."""
    with tenant_session(user_id) as session:
        yield session


def upsert_user(auth_provider_id: str, email: str | None = None) -> uuid.UUID:
    """Return the users.id for an Auth0 subject, creating the row on first sight.

    The caller must pass a subject taken from a *verified* access token. RLS on ``users`` allows a row to be
    seen or written when it matches either ``app.current_user_id`` or ``app.current_auth_subject``; this
    function sets only the latter, inside its own transaction.
    """
    if not auth_provider_id:
        raise ValueError("auth_provider_id is required")
    session = _scoped_session(_SET_SUBJECT_SQL, {"sub": auth_provider_id})
    with session, session.begin():
        return session.execute(
            text(
                "INSERT INTO users (auth_provider_id, email) VALUES (:sub, :email) "
                "ON CONFLICT (auth_provider_id) DO UPDATE "
                "SET email = COALESCE(EXCLUDED.email, users.email), updated_at = now() "
                "RETURNING id"
            ),
            {"sub": auth_provider_id, "email": email},
        ).scalar_one()
