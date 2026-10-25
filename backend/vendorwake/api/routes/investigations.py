"""Investigations: list, detail (from the DB, enriched by Temporal when available), evidence, follow-ups."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from vendorwake.api.deps import SettingsDep
from vendorwake.core.exceptions import ConflictError, ServiceUnavailableError
from vendorwake.db.session import tenant_session
from vendorwake.domain.contracts import EvidenceItem
from vendorwake.services import investigations as svc
from vendorwake.services import recommendations as rec_svc
from vendorwake.services.adapters import AdapterUnavailableError
from vendorwake.services.context import PortsDep, UserId, optional_gateway
from vendorwake.services.schemas import (
    AskRequest,
    AskResponse,
    InvestigationDetail,
    InvestigationSummary,
    RecommendationOut,
    RetrievedMemory,
)

router = APIRouter(prefix="/investigations", tags=["investigations"])
MaybeGateway = Annotated[Any, Depends(optional_gateway)]


@router.get("", response_model=list[InvestigationSummary])
def list_investigations(user_id: UserId) -> list[InvestigationSummary]:
    with tenant_session(user_id) as s:
        return svc.list_summaries(s)


def _workflow_ids(user_id: uuid.UUID, investigation_id: uuid.UUID) -> tuple[str | None, str | None]:
    with tenant_session(user_id) as s:
        inv = svc.get(s, investigation_id)
        return inv.temporal_workflow_id, inv.temporal_run_id


def _detail(user_id: uuid.UUID, investigation_id: uuid.UUID, settings: Any, wf_status: str | None) -> Any:
    with tenant_session(user_id) as s:
        return svc.detail(s, investigation_id, settings, wf_status)


@router.get("/{investigation_id}", response_model=InvestigationDetail)
async def get_investigation(
    investigation_id: uuid.UUID, user_id: UserId, settings: SettingsDep, gateway: MaybeGateway
) -> InvestigationDetail:
    workflow_id, run_id = await run_in_threadpool(_workflow_ids, user_id, investigation_id)
    wf_status = None
    if gateway is not None and workflow_id:
        wf_status = await gateway.describe_status(workflow_id, run_id)  # None if the history is gone
    return await run_in_threadpool(_detail, user_id, investigation_id, settings, wf_status)


@router.get("/{investigation_id}/evidence", response_model=list[EvidenceItem])
def investigation_evidence(investigation_id: uuid.UUID, user_id: UserId) -> list[EvidenceItem]:
    with tenant_session(user_id) as s:
        return svc.evidence(s, investigation_id)


@router.get("/{investigation_id}/recommendations", response_model=list[RecommendationOut])
def investigation_recommendations(investigation_id: uuid.UUID, user_id: UserId) -> list[RecommendationOut]:
    with tenant_session(user_id) as s:
        return rec_svc.list_for(s, investigation_id)


@router.post("/{investigation_id}/ask", response_model=AskResponse)
def ask(
    investigation_id: uuid.UUID, body: AskRequest, user_id: UserId, ports: PortsDep, settings: SettingsDep
) -> AskResponse:
    with tenant_session(user_id) as s:
        thread_id = svc.get(s, investigation_id).backboard_thread_id
    if not thread_id:
        raise ConflictError("Follow-up context is not ready for this investigation", code="context_not_ready")
    try:
        answer, memories = ports.context_store().ask(
            user_key=str(user_id), thread_id=thread_id, question=body.question
        )
    except AdapterUnavailableError:
        raise ServiceUnavailableError(
            "Follow-up questions are not configured", code="context_unavailable"
        ) from None
    return AskResponse(
        answer=answer,
        retrieved_memories=[
            RetrievedMemory(
                memory=str(m.get("memory", "")),
                score=m.get("score"),
                id=str(m["id"]) if m.get("id") is not None else None,
            )
            for m in memories or []
        ],
        model=settings.llm_model
        if settings.backboard_generation == "vendorwake"
        else settings.backboard_model,
    )
