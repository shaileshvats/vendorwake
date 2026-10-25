"""FastAPI dependencies shared by the workflow-backed routes.

* :func:`current_user_id` resolves the caller's ``users.id`` from the *verified* token subject only.
* :func:`get_ports` returns the pipeline/sponsor adapter seam (``app.state.ports`` overrides it in tests).
* :func:`get_gateway` connects to Temporal lazily, once per app; failures become 503.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated

from fastapi import Depends, Request

from vendorwake.api.deps import get_app_settings
from vendorwake.core.config import Settings
from vendorwake.core.exceptions import ServiceUnavailableError
from vendorwake.core.security import CurrentUser
from vendorwake.db.session import upsert_user
from vendorwake.services.adapters import LazyPorts, Ports

logger = logging.getLogger(__name__)


def current_user_id(principal: CurrentUser) -> uuid.UUID:
    return upsert_user(principal.subject, principal.email)


UserId = Annotated[uuid.UUID, Depends(current_user_id)]


def get_ports(request: Request, settings: Annotated[Settings, Depends(get_app_settings)]) -> Ports:
    ports = getattr(request.app.state, "ports", None)
    if ports is None:
        ports = LazyPorts(settings)
        request.app.state.ports = ports
    return ports


PortsDep = Annotated[Ports, Depends(get_ports)]


class TemporalUnavailableError(ServiceUnavailableError):
    code = "temporal_unavailable"
    message = "The workflow engine is unavailable"


async def get_gateway(request: Request, settings: Annotated[Settings, Depends(get_app_settings)]):  # noqa: ANN201
    from vendorwake.workflows.client import connect_client
    from vendorwake.workflows.gateway import WorkflowGateway

    gateway = getattr(request.app.state, "workflow_gateway", None)
    if gateway is not None:
        return gateway
    lock = request.app.state.__dict__.setdefault("_gateway_lock", asyncio.Lock())
    async with lock:
        gateway = getattr(request.app.state, "workflow_gateway", None)
        if gateway is None:
            try:
                client = await asyncio.wait_for(connect_client(settings), timeout=5)
            except Exception as exc:
                logger.warning("temporal connect failed: %s", type(exc).__name__)
                raise TemporalUnavailableError() from None
            gateway = WorkflowGateway(client, settings)
            request.app.state.workflow_gateway = gateway
    return gateway


async def optional_gateway(request: Request, settings: Annotated[Settings, Depends(get_app_settings)]):  # noqa: ANN201
    """Like get_gateway, but None instead of 503 (for reads that only enrich DB state)."""
    try:
        return await get_gateway(request, settings)
    except TemporalUnavailableError:
        return None
