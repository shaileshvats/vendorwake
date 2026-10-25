"""Vendors, monitoring schedules and on-demand discovery."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from starlette.concurrency import run_in_threadpool

from vendorwake.core.exceptions import NotFoundError
from vendorwake.db.session import tenant_session
from vendorwake.domain.contracts import VendorSignature
from vendorwake.services import vendors as svc
from vendorwake.services.adapters import AdapterUnavailableError
from vendorwake.services.context import PortsDep, UserId, get_gateway
from vendorwake.services.schemas import (
    MonitoringRequest,
    MonitoringStarted,
    MonitoringStopped,
    VendorOut,
    WorkflowStarted,
)

router = APIRouter(tags=["vendors"])
Gateway = Annotated[Any, Depends(get_gateway)]


def _signatures(ports: PortsDep) -> list[VendorSignature]:
    try:
        return ports.signatures()
    except AdapterUnavailableError:
        return []


def _resolve(ports: PortsDep, user_id: Any, raw: str) -> tuple[str, VendorSignature | None]:
    name = svc.normalize_name(raw)
    if not svc.VENDOR_NAME.match(name):
        raise NotFoundError("Unknown vendor", code="unknown_vendor")
    try:
        signature = ports.signature(name)
    except AdapterUnavailableError:
        signature = None
    with tenant_session(user_id) as s:
        if not svc.known_vendor(s, name, signature):
            raise NotFoundError("Unknown vendor", code="unknown_vendor")
    return name, signature


def _set(user_id: Any, name: str, state: str, schedule_id: str, sig: VendorSignature | None) -> None:
    with tenant_session(user_id) as s:
        svc.set_monitoring(s, name, state, schedule_id, sig)


@router.get("/vendors", response_model=list[VendorOut])
def list_vendors(user_id: UserId, ports: PortsDep) -> list[VendorOut]:
    signatures = _signatures(ports)
    with tenant_session(user_id) as s:
        return svc.list_vendors(s, signatures)


@router.post("/monitoring/start", response_model=MonitoringStarted)
async def start_monitoring(
    body: MonitoringRequest, user_id: UserId, ports: PortsDep, gateway: Gateway
) -> MonitoringStarted:
    name, signature = await run_in_threadpool(_resolve, ports, user_id, body.vendor)
    schedule_id = await gateway.ensure_schedule(name)
    await run_in_threadpool(_set, user_id, name, "active", schedule_id, signature)
    return MonitoringStarted(vendor=name, schedule_id=schedule_id)


@router.post("/monitoring/stop", response_model=MonitoringStopped)
async def stop_monitoring(
    body: MonitoringRequest, user_id: UserId, ports: PortsDep, gateway: Gateway
) -> MonitoringStopped:
    name, signature = await run_in_threadpool(_resolve, ports, user_id, body.vendor)
    schedule_id = await gateway.pause_schedule(name)
    await run_in_threadpool(_set, user_id, name, "paused", schedule_id, signature)
    return MonitoringStopped(vendor=name)


@router.post(
    "/vendors/{vendor}/discover", status_code=status.HTTP_202_ACCEPTED, response_model=WorkflowStarted
)
async def discover_now(vendor: str, user_id: UserId, ports: PortsDep, gateway: Gateway) -> WorkflowStarted:
    name, _signature = await run_in_threadpool(_resolve, ports, user_id, vendor)
    return WorkflowStarted(workflow_id=await gateway.start_discovery(name))
