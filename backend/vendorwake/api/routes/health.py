"""Liveness/readiness: GET /api/health (unauthenticated).

Always answers 200 while the process is serving; ``status`` and ``checks`` say whether the database
and Temporal are reachable. Responses carry no hostnames or error text.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from vendorwake import __version__
from vendorwake.api.deps import SettingsDep
from vendorwake.core.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

CheckResult = Literal["ok", "error", "skipped"]

TEMPORAL_CONNECT_TIMEOUT_SECONDS = 1.0
DATABASE_CHECK_TIMEOUT_SECONDS = 5.0


class HealthChecks(BaseModel):
    database: CheckResult
    temporal: CheckResult


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: HealthChecks


def _database_ping(settings: Settings) -> CheckResult:
    try:
        from vendorwake.db.session import ping
    except ImportError:
        return "skipped"
    try:
        return "ok" if ping(settings.database_url.get_secret_value()) else "error"
    except Exception as exc:  # ping() should never raise; be defensive anyway
        logger.warning("database health check raised %s", type(exc).__name__)
        return "error"


async def check_database(settings: Settings) -> CheckResult:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_database_ping, settings), timeout=DATABASE_CHECK_TIMEOUT_SECONDS
        )
    except TimeoutError:
        logger.warning("database health check timed out")
        return "error"


def _split_host_port(address: str) -> tuple[str, int] | None:
    host, sep, port = address.strip().rpartition(":")
    if not sep or not host or not port.isdigit():
        return None
    return host.strip("[]"), int(port)


async def check_temporal(settings: Settings) -> CheckResult:
    """Plain TCP connect to the Temporal frontend; proves reachability, not namespace health."""
    if not settings.temporal_address.strip():
        return "skipped"
    target = _split_host_port(settings.temporal_address)
    if target is None:
        logger.warning("temporal health check: address is not host:port")
        return "error"
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(*target), TEMPORAL_CONNECT_TIMEOUT_SECONDS)
    except (OSError, TimeoutError) as exc:
        logger.info("temporal health check failed: %s", type(exc).__name__)
        return "error"
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return "ok"


@router.get("/health", response_model=HealthResponse, summary="Service health")
async def health(settings: SettingsDep) -> HealthResponse:
    database, temporal = await asyncio.gather(check_database(settings), check_temporal(settings))
    status = "degraded" if "error" in (database, temporal) else "ok"
    return HealthResponse(
        status=status, version=__version__, checks=HealthChecks(database=database, temporal=temporal)
    )
