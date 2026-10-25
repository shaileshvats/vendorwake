"""Repositories: register, list, (re-)analyse, read the dependency inventory."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from starlette.concurrency import run_in_threadpool

from vendorwake.db.session import tenant_session
from vendorwake.domain.contracts import RepositoryInventory
from vendorwake.services import repositories as svc
from vendorwake.services.context import UserId, get_gateway
from vendorwake.services.schemas import RepositoryCreate, RepositoryOut

router = APIRouter(prefix="/repositories", tags=["repositories"])
Gateway = Annotated[Any, Depends(get_gateway)]


def _create(user_id: uuid.UUID, body: RepositoryCreate) -> uuid.UUID:
    with tenant_session(user_id) as s:
        return svc.create_or_get(
            s, user_id, owner=body.owner, name=body.name, ref=body.ref, subpath=body.subpath
        ).id


def _get(user_id: uuid.UUID, repository_id: uuid.UUID) -> RepositoryOut:
    with tenant_session(user_id) as s:
        return svc.to_out(svc.get(s, repository_id))


def _mark_pending(user_id: uuid.UUID, repository_id: uuid.UUID) -> RepositoryOut:
    with tenant_session(user_id) as s:
        repo = svc.get(s, repository_id)
        if repo.analysis_status != "running":
            svc.set_status(s, repository_id, "pending")
        s.flush()
        return svc.to_out(repo)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RepositoryOut)
async def create_repository(body: RepositoryCreate, user_id: UserId, gateway: Gateway) -> RepositoryOut:
    repository_id = await run_in_threadpool(_create, user_id, body)
    out = await run_in_threadpool(_mark_pending, user_id, repository_id)
    await gateway.start_repository_analysis(repository_id, user_id)
    return out


@router.get("", response_model=list[RepositoryOut])
def list_repositories(user_id: UserId) -> list[RepositoryOut]:
    with tenant_session(user_id) as s:
        return [svc.to_out(r) for r in svc.list_all(s)]


@router.get("/{repository_id}", response_model=RepositoryOut)
def get_repository(repository_id: uuid.UUID, user_id: UserId) -> RepositoryOut:
    return _get(user_id, repository_id)


@router.post("/{repository_id}/analyze", status_code=status.HTTP_202_ACCEPTED, response_model=RepositoryOut)
async def analyze_repository(repository_id: uuid.UUID, user_id: UserId, gateway: Gateway) -> RepositoryOut:
    out = await run_in_threadpool(_mark_pending, user_id, repository_id)
    await gateway.start_repository_analysis(repository_id, user_id)
    return out


@router.get("/{repository_id}/dependencies", response_model=RepositoryInventory)
def repository_dependencies(repository_id: uuid.UUID, user_id: UserId) -> RepositoryInventory:
    with tenant_session(user_id) as s:
        return svc.inventory(s, repository_id)
