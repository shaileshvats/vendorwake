"""Agent permissions (Auth0 demo): list agent identities and run a real permission check."""

from __future__ import annotations

from fastapi import APIRouter

from vendorwake.services import security as svc
from vendorwake.services.context import PortsDep, UserId
from vendorwake.services.schemas import AgentCheckRequest, AgentCheckResponse, AgentOut

router = APIRouter(prefix="/security/agents", tags=["security"])


@router.get("", response_model=list[AgentOut])
def list_agents(_user_id: UserId, ports: PortsDep) -> list[AgentOut]:
    return svc.list_agents(ports)


@router.post("/check", response_model=AgentCheckResponse)
def check_agent(body: AgentCheckRequest, _user_id: UserId, ports: PortsDep) -> AgentCheckResponse:
    return svc.check(ports, body.agent, body.operation)
