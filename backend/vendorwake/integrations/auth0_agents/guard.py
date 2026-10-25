"""Tool-permission layer for agents: every tool call is checked against the agent's verified token.

Scopes come only from an Auth0 access token verified by `vendorwake.core.security.verify_access_token`
(RS256, issuer, audience, expiry; the same code path as the API). Nothing the model outputs can widen
them: the guard takes no scope input, the operation map is immutable, and unknown operations are
denied. Denials are logged with agent, operation, required scope and reason; tokens never are.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import MappingProxyType

from vendorwake.core.config import Settings
from vendorwake.core.security import (
    KeyResolver,
    KeySourceUnavailableError,
    TokenValidationError,
    auth_configured,
    get_key_resolver,
    verify_access_token,
)
from vendorwake.integrations.auth0_agents.tokens import AgentTokenProvider
from vendorwake.integrations.errors import IntegrationError

logger = logging.getLogger(__name__)

TOOL_SCOPES: MappingProxyType[str, str] = MappingProxyType(
    {
        "read_docs": "docs:read",
        "read_repo": "repo:read",
        "propose_patch": "patch:propose",
        "push_branch": "repo:write",
    }
)


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    agent: str
    operation: str
    required_scope: str | None
    reason: str  # "granted" or a short machine-readable denial reason
    subject: str | None = None
    granted_scopes: frozenset[str] = frozenset()

    def as_log(self) -> dict[str, object]:
        return {
            "agent": self.agent,
            "operation": self.operation,
            "required_scope": self.required_scope,
            "reason": self.reason,
            "subject": self.subject,
            "granted_scopes": sorted(self.granted_scopes),
        }


class ToolPermissionDenied(Exception):
    def __init__(self, decision: PermissionDecision) -> None:
        super().__init__(f"{decision.agent} may not {decision.operation}: {decision.reason}")
        self.decision = decision


class ToolPermissionGuard:
    def __init__(
        self,
        settings: Settings,
        *,
        token_provider: AgentTokenProvider | None = None,
        resolver: KeyResolver | None = None,
    ) -> None:
        self._settings = settings
        self._tokens = token_provider
        self._resolver = resolver

    def _key_resolver(self) -> KeyResolver:
        if self._resolver is None:
            # Same cached PyJWKClient the API uses; raises AuthNotConfiguredError when Auth0 is unset.
            self._resolver = get_key_resolver(self._settings)
        return self._resolver

    def check(self, *, agent: str, operation: str, token: str | None = None) -> PermissionDecision:
        """Decide whether ``agent`` may perform ``operation``. When ``token`` is omitted it is obtained
        from the token provider for that agent. Never raises for a denial; see `require`."""
        required = TOOL_SCOPES.get(operation)
        if required is None:
            return self._deny(agent, operation, None, "unknown_operation")
        if not auth_configured(self._settings):
            return self._deny(agent, operation, required, "auth_not_configured")
        if token is None:
            if self._tokens is None:
                return self._deny(agent, operation, required, "no_token")
            try:
                token = self._tokens.get_token(agent)
            except IntegrationError as exc:
                return self._deny(agent, operation, required, f"token_unavailable:{type(exc).__name__}")
        try:
            principal = verify_access_token(token, self._settings, self._key_resolver())
        except TokenValidationError:
            return self._deny(agent, operation, required, "invalid_token")
        except KeySourceUnavailableError:
            return self._deny(agent, operation, required, "jwks_unavailable")
        except Exception as exc:  # e.g. AuthNotConfiguredError: fail closed
            return self._deny(agent, operation, required, f"verification_error:{type(exc).__name__}")

        if principal.kind != "agent":
            return self._deny(agent, operation, required, "not_an_agent_token", principal.subject)
        expected_client = self._tokens.client_id_for(agent) if self._tokens else None
        if expected_client and principal.subject != f"{expected_client}@clients":
            return self._deny(
                agent, operation, required, "token_belongs_to_another_client", principal.subject
            )
        if required not in principal.scopes:
            return self._deny(
                agent, operation, required, "missing_scope", principal.subject, principal.scopes
            )
        decision = PermissionDecision(
            allowed=True,
            agent=agent,
            operation=operation,
            required_scope=required,
            reason="granted",
            subject=principal.subject,
            granted_scopes=principal.scopes,
        )
        logger.info("agent tool call allowed", extra=decision.as_log())
        return decision

    def require(self, *, agent: str, operation: str, token: str | None = None) -> PermissionDecision:
        decision = self.check(agent=agent, operation=operation, token=token)
        if not decision.allowed:
            raise ToolPermissionDenied(decision)
        return decision

    @staticmethod
    def _deny(
        agent: str,
        operation: str,
        required: str | None,
        reason: str,
        subject: str | None = None,
        scopes: frozenset[str] = frozenset(),
    ) -> PermissionDecision:
        decision = PermissionDecision(
            allowed=False,
            agent=agent,
            operation=operation,
            required_scope=required,
            reason=reason,
            subject=subject,
            granted_scopes=scopes,
        )
        logger.warning("agent tool call denied", extra=decision.as_log())
        return decision
