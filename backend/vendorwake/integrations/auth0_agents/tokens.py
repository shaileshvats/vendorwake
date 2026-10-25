"""Per-agent Auth0 machine identities: client-credentials access tokens, cached until near expiry.

Auth0 Client Credentials Flow (https://auth0.com/docs/get-started/authentication-and-authorization-flow/
client-credentials-flow/call-your-api-using-the-client-credentials-flow):
``POST https://{domain}/oauth/token`` with JSON ``{"grant_type": "client_credentials", "client_id",
"client_secret", "audience"}`` returns ``{"access_token", "token_type", "expires_in", "scope"?}``;
errors are 4xx JSON ``{"error", "error_description"}``.

One M2M application per logical agent (docs/m0-findings.md section 5), so each agent's scopes are
granted, audited and revoked individually in the Auth0 dashboard, never by our code.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import httpx
from pydantic import SecretStr

from vendorwake.integrations.errors import NotConfiguredError, RateLimitedError, UpstreamError

logger = logging.getLogger(__name__)

SERVICE = "auth0"
REFRESH_MARGIN_S = 60.0


@dataclass(frozen=True, slots=True)
class AgentCredentials:
    client_id: str
    client_secret: SecretStr = field(repr=False)


def parse_agent_credentials(raw: SecretStr | str | Mapping | None) -> dict[str, AgentCredentials]:
    """Parse ``VW_AUTH0_AGENT_CREDENTIALS``: ``{"<agent>": {"client_id": "...", "client_secret": "..."}}``."""
    if raw is None:
        return {}
    if isinstance(raw, SecretStr):
        raw = raw.get_secret_value()
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            # Do not chain: the decode error message would contain part of the secret.
            raise NotConfiguredError(
                "VW_AUTH0_AGENT_CREDENTIALS is not valid JSON", service=SERVICE
            ) from None
    else:
        data = raw
    if not isinstance(data, Mapping):
        raise NotConfiguredError("VW_AUTH0_AGENT_CREDENTIALS must be a JSON object", service=SERVICE)
    parsed: dict[str, AgentCredentials] = {}
    for agent, entry in data.items():
        if not isinstance(agent, str) or not agent or not isinstance(entry, Mapping):
            raise NotConfiguredError(f"invalid agent credentials entry for {agent!r}", service=SERVICE)
        client_id, secret = entry.get("client_id"), entry.get("client_secret")
        if not isinstance(client_id, str) or not client_id or not isinstance(secret, str) or not secret:
            raise NotConfiguredError(f"agent {agent!r} needs client_id and client_secret", service=SERVICE)
        parsed[agent] = AgentCredentials(client_id=client_id, client_secret=SecretStr(secret))
    return parsed


@dataclass(slots=True)
class _CachedToken:
    access_token: str
    expires_at: float


class AgentTokenProvider:
    def __init__(
        self,
        *,
        domain: str,
        audience: str,
        credentials: Mapping[str, AgentCredentials],
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        refresh_margin_s: float = REFRESH_MARGIN_S,
    ) -> None:
        if not domain or not audience:
            raise ValueError("domain and audience are required")
        self.domain = domain.strip().removeprefix("https://").rstrip("/")
        self.audience = audience
        self._credentials = dict(credentials)
        self._client = client or httpx.Client(timeout=15.0)
        self._owns_client = client is None
        self._clock = clock
        self._margin = refresh_margin_s
        self._cache: dict[str, _CachedToken] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"AgentTokenProvider(domain={self.domain!r}, agents={sorted(self._credentials)!r})"

    @property
    def agents(self) -> frozenset[str]:
        return frozenset(self._credentials)

    def client_id_for(self, agent: str) -> str | None:
        creds = self._credentials.get(agent)
        return creds.client_id if creds else None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def invalidate(self, agent: str) -> None:
        with self._lock:
            self._cache.pop(agent, None)

    def get_token(self, agent: str) -> str:
        """A valid access token for ``agent`` (fetched, or cached until ``expires_in - margin``)."""
        creds = self._credentials.get(agent)
        if creds is None:
            raise NotConfiguredError(f"no Auth0 credentials configured for agent {agent!r}", service=SERVICE)
        with self._lock:
            cached = self._cache.get(agent)
            if cached and cached.expires_at > self._clock():
                return cached.access_token
            token, expires_in = self._fetch(agent, creds)
            self._cache[agent] = _CachedToken(token, self._clock() + max(0.0, expires_in - self._margin))
            return token

    def _fetch(self, agent: str, creds: AgentCredentials) -> tuple[str, float]:
        try:
            response = self._client.post(
                f"https://{self.domain}/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret.get_secret_value(),
                    "audience": self.audience,
                },
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                f"Auth0 token request failed ({type(exc).__name__})", service=SERVICE
            ) from exc
        if response.status_code == 429:
            raise RateLimitedError("Auth0 rate limit reached", service=SERVICE, status_code=429)
        if response.status_code >= 400:
            try:
                error = str(response.json().get("error", "unknown"))[:64]
            except (ValueError, AttributeError):
                error = "unknown"
            logger.warning(
                "auth0 agent token request rejected",
                extra={"agent": agent, "status": response.status_code, "auth0_error": error},
            )
            raise UpstreamError(
                f"Auth0 rejected the token request for agent {agent!r} ({error})",
                service=SERVICE,
                status_code=response.status_code,
            )
        try:
            body = response.json()
            token = body["access_token"]
            expires_in = float(body.get("expires_in", 0))
        except (ValueError, KeyError, TypeError) as exc:
            raise UpstreamError("Auth0 returned an unexpected token response", service=SERVICE) from exc
        if not isinstance(token, str) or not token:
            raise UpstreamError("Auth0 returned an empty access token", service=SERVICE)
        return token, expires_in
