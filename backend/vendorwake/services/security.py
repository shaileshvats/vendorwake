"""Agent permission demo: list agent identities and run a real ToolPermissionGuard check."""

from __future__ import annotations

import logging

from vendorwake.core.exceptions import ServiceUnavailableError
from vendorwake.services.adapters import AdapterUnavailableError, Ports
from vendorwake.services.schemas import AgentCheckResponse, AgentOut

logger = logging.getLogger(__name__)


def list_agents(ports: Ports) -> list[AgentOut]:
    try:
        agents = ports.permission_guard().list_agents()
    except AdapterUnavailableError:
        raise ServiceUnavailableError(
            "Agent permissions are not configured", code="agents_unavailable"
        ) from None
    return [
        AgentOut(agent=a["agent"], client_id=a.get("client_id"), scopes=a.get("scopes") or []) for a in agents
    ]


def check(ports: Ports, agent: str, operation: str) -> AgentCheckResponse:
    try:
        decision = ports.permission_guard().check(agent, operation)
    except AdapterUnavailableError:
        raise ServiceUnavailableError(
            "Agent permissions are not configured", code="agents_unavailable"
        ) from None
    if not decision.allowed:
        logger.warning(
            "agent permission denied: agent=%s operation=%s scope=%s reason=%s",
            agent,
            operation,
            decision.required_scope,
            decision.reason,
        )
    return AgentCheckResponse(**decision.as_dict())
