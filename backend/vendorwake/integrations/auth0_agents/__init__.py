"""Auth0 agent identities (client credentials over httpx) and the tool-permission guard."""

from __future__ import annotations

from vendorwake.core.config import Settings
from vendorwake.integrations.auth0_agents.guard import (
    TOOL_SCOPES,
    PermissionDecision,
    ToolPermissionDenied,
    ToolPermissionGuard,
)
from vendorwake.integrations.auth0_agents.tokens import (
    AgentCredentials,
    AgentTokenProvider,
    parse_agent_credentials,
)
from vendorwake.integrations.errors import NotConfiguredError


def token_provider_from_settings(settings: Settings, **kwargs: object) -> AgentTokenProvider:
    if not settings.auth0_domain.strip() or not settings.auth0_audience.strip():
        raise NotConfiguredError(
            "Auth0 is not configured: set VW_AUTH0_DOMAIN and VW_AUTH0_AUDIENCE", service="auth0"
        )
    credentials = parse_agent_credentials(settings.auth0_agent_credentials)
    if not credentials:
        raise NotConfiguredError(
            "No agent identities configured: set VW_AUTH0_AGENT_CREDENTIALS", service="auth0"
        )
    return AgentTokenProvider(
        domain=settings.auth0_domain,
        audience=settings.auth0_audience,
        credentials=credentials,
        **kwargs,  # type: ignore[arg-type]
    )


def guard_from_settings(settings: Settings) -> ToolPermissionGuard:
    """Guard with a token provider. Raises NotConfiguredError when agent identities are missing."""
    return ToolPermissionGuard(settings, token_provider=token_provider_from_settings(settings))


from_settings = guard_from_settings

__all__ = [
    "TOOL_SCOPES",
    "AgentCredentials",
    "AgentTokenProvider",
    "PermissionDecision",
    "ToolPermissionDenied",
    "ToolPermissionGuard",
    "from_settings",
    "guard_from_settings",
    "parse_agent_credentials",
    "token_provider_from_settings",
]
