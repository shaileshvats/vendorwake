"""Vendor changes: list, detail, related (pgvector) and starting an investigation."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from starlette.concurrency import run_in_threadpool

from vendorwake.db.session import tenant_session
from vendorwake.services import changes as svc
from vendorwake.services import investigations as inv_svc
from vendorwake.services.context import UserId, get_gateway
from vendorwake.services.schemas import (
    ChangeDetail,
    ChangeSummary,
    InvestigateRequest,
    InvestigationStarted,
    RelatedChange,
)

router = APIRouter(prefix="/changes", tags=["changes"])
Gateway = Annotated[Any, Depends(get_gateway)]


@router.get("", response_model=list[ChangeSummary])
def list_changes(
    user_id: UserId, vendor: Annotated[str | None, Query(max_length=64)] = None
) -> list[ChangeSummary]:
    with tenant_session(user_id) as s:
        return svc.list_changes(s, vendor)


@router.get("/{change_id}", response_model=ChangeDetail)
def get_change(change_id: uuid.UUID, user_id: UserId) -> ChangeDetail:
    with tenant_session(user_id) as s:
        return svc.change_detail(s, change_id)


@router.get("/{change_id}/related", response_model=list[RelatedChange])
def related_changes(change_id: uuid.UUID, user_id: UserId) -> list[RelatedChange]:
    with tenant_session(user_id) as s:
        return svc.related_changes(s, change_id)


def _create(user_id: uuid.UUID, change_id: uuid.UUID, repository_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    with tenant_session(user_id) as s:
        inv = inv_svc.create(s, user_id, change_id, repository_id)
        return inv.id, inv.temporal_workflow_id or ""


@router.post(
    "/{change_id}/investigate", status_code=status.HTTP_202_ACCEPTED, response_model=InvestigationStarted
)
async def investigate(
    change_id: uuid.UUID, body: InvestigateRequest, user_id: UserId, gateway: Gateway
) -> InvestigationStarted:
    investigation_id, _ = await run_in_threadpool(_create, user_id, change_id, body.repository_id)
    workflow_id = await gateway.start_investigation(investigation_id, user_id)
    return InvestigationStarted(investigation_id=investigation_id, workflow_id=workflow_id)
