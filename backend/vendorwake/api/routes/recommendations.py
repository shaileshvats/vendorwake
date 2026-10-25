"""Recommendation decisions (persisted, then signalled to the workflow) and re-verification."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from starlette.concurrency import run_in_threadpool

from vendorwake.db.session import tenant_session
from vendorwake.services import investigations as inv_svc
from vendorwake.services import recommendations as svc
from vendorwake.services.context import UserId, get_gateway, optional_gateway
from vendorwake.services.schemas import RecommendationOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/recommendations", tags=["recommendations"])
Gateway = Annotated[Any, Depends(get_gateway)]
MaybeGateway = Annotated[Any, Depends(optional_gateway)]


def _decide(
    user_id: uuid.UUID, recommendation_id: uuid.UUID, decision: str
) -> tuple[RecommendationOut, str | None]:
    with tenant_session(user_id) as s:
        row = svc.decide(s, recommendation_id, decision)
        inv = inv_svc.get(s, row.investigation_id)
        return svc.to_out(row), inv.temporal_workflow_id


async def _decision(recommendation_id: uuid.UUID, user_id: uuid.UUID, gateway: Any, decision: str) -> Any:
    out, workflow_id = await run_in_threadpool(_decide, user_id, recommendation_id, decision)
    delivered = False
    if gateway is not None and workflow_id:
        try:
            delivered = await gateway.signal_decision(workflow_id, recommendation_id, decision)
        except Exception as exc:  # the decision is persisted; the workflow will time out on its own
            logger.warning("decision signal failed: %s", type(exc).__name__)
    if not delivered:
        logger.info("decision for %s persisted; workflow %s not signalled", recommendation_id, workflow_id)
    return out


@router.post("/{recommendation_id}/approve", response_model=RecommendationOut)
async def approve(recommendation_id: uuid.UUID, user_id: UserId, gateway: MaybeGateway) -> RecommendationOut:
    return await _decision(recommendation_id, user_id, gateway, "approve")


@router.post("/{recommendation_id}/dismiss", response_model=RecommendationOut)
async def dismiss(recommendation_id: uuid.UUID, user_id: UserId, gateway: MaybeGateway) -> RecommendationOut:
    return await _decision(recommendation_id, user_id, gateway, "dismiss")


def _pending(user_id: uuid.UUID, recommendation_id: uuid.UUID) -> RecommendationOut:
    with tenant_session(user_id) as s:
        return svc.to_out(svc.mark_verification_pending(s, recommendation_id))


@router.post(
    "/{recommendation_id}/verify", status_code=status.HTTP_202_ACCEPTED, response_model=RecommendationOut
)
async def verify(recommendation_id: uuid.UUID, user_id: UserId, gateway: Gateway) -> RecommendationOut:
    out = await run_in_threadpool(_pending, user_id, recommendation_id)
    await gateway.start_verification(recommendation_id, user_id)
    return out
