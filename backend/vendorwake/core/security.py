"""Auth0 access-token validation and FastAPI auth dependencies.

Tokens must be RS256 JWTs signed by a key from the tenant's JWKS, issued by ``https://<domain>/`` for
``VW_AUTH0_AUDIENCE``, and carry ``exp``, ``iat`` and ``sub``. When Auth0 is not configured, protected
endpoints fail closed with 503 instead of letting requests through.

Tests swap the signing-key source by overriding the `get_key_resolver` dependency.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, Literal, Protocol

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from vendorwake.api.deps import get_app_settings
from vendorwake.core.config import Settings
from vendorwake.core.exceptions import (
    AuthenticationError,
    AuthNotConfiguredError,
    PermissionDeniedError,
    ServiceUnavailableError,
)

logger = logging.getLogger(__name__)

ALGORITHMS = ["RS256"]
REQUIRED_CLAIMS = ["exp", "iat", "sub"]
CLOCK_SKEW_LEEWAY_SECONDS = 30
JWKS_CACHE_LIFESPAN_SECONDS = 600
JWKS_FETCH_TIMEOUT_SECONDS = 5

PrincipalKind = Literal["user", "agent"]


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller. ``kind == "agent"`` for machine (client-credentials) tokens."""

    subject: str
    kind: PrincipalKind
    scopes: frozenset[str]
    email: str | None = None

    @property
    def is_user(self) -> bool:
        return self.kind == "user"

    def has_scopes(self, *scopes: str) -> bool:
        return set(scopes) <= self.scopes


class KeyResolver(Protocol):
    """Anything that maps a JWT to its signing key; `jwt.PyJWKClient` satisfies this."""

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


class TokenValidationError(Exception):
    """The token is not acceptable. The message is for logs only, never for the client."""


class KeySourceUnavailableError(Exception):
    """The JWKS endpoint could not be reached, so no token can currently be verified."""


def auth_configured(settings: Settings) -> bool:
    return bool(settings.auth0_domain.strip() and settings.auth0_audience.strip())


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    # One client per JWKS URL for the process lifetime: it caches the key set (lifespan) and the
    # resolved signing keys, and refetches on an unknown kid (subject to PyJWT's cooldown).
    return jwt.PyJWKClient(
        jwks_url,
        cache_keys=True,
        cache_jwk_set=True,
        lifespan=JWKS_CACHE_LIFESPAN_SECONDS,
        timeout=JWKS_FETCH_TIMEOUT_SECONDS,
    )


def _principal_from_claims(claims: dict[str, Any]) -> Principal:
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise TokenValidationError("sub claim is not a non-empty string")

    scopes: set[str] = set()
    scope_claim = claims.get("scope")
    if isinstance(scope_claim, str):
        scopes.update(s for s in scope_claim.split(" ") if s)
    permissions = claims.get("permissions")
    if isinstance(permissions, list):
        scopes.update(p for p in permissions if isinstance(p, str) and p)

    is_agent = claims.get("gty") == "client-credentials" or subject.endswith("@clients")
    email = claims.get("email")
    return Principal(
        subject=subject,
        kind="agent" if is_agent else "user",
        scopes=frozenset(scopes),
        email=email if isinstance(email, str) and email else None,
    )


def verify_access_token(token: str, settings: Settings, resolver: KeyResolver) -> Principal:
    """Validate an Auth0 access token and return its Principal.

    Raises TokenValidationError for any unacceptable token and KeySourceUnavailableError when the
    JWKS cannot be fetched.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenValidationError(f"malformed token header ({type(exc).__name__})") from exc
    # Reject before any key lookup: blocks alg=none / HS256-with-public-key confusion outright.
    if header.get("alg") not in ALGORITHMS:
        raise TokenValidationError("unexpected signing algorithm")

    try:
        signing_key = resolver.get_signing_key_from_jwt(token)
    except jwt.PyJWKClientConnectionError as exc:
        raise KeySourceUnavailableError("JWKS fetch failed") from exc
    except jwt.PyJWTError as exc:  # unknown kid, unusable key set
        raise TokenValidationError(f"no matching signing key ({type(exc).__name__})") from exc

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            audience=settings.auth0_audience,
            issuer=settings.auth0_issuer,
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as exc:
        raise TokenValidationError(f"token rejected ({type(exc).__name__})") from exc
    return _principal_from_claims(claims)


# --- FastAPI dependencies -------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False, scheme_name="Auth0", description="Auth0 access token (RS256 JWT)")


def get_key_resolver(settings: Annotated[Settings, Depends(get_app_settings)]) -> KeyResolver:
    if not auth_configured(settings):
        raise AuthNotConfiguredError()
    return _jwks_client(settings.auth0_jwks_url)


# Sync (not async) on purpose: a JWKS refresh is a blocking HTTP call, so FastAPI runs this in its
# threadpool rather than on the event loop.
def get_principal(
    settings: Annotated[Settings, Depends(get_app_settings)],
    resolver: Annotated[KeyResolver, Depends(get_key_resolver)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if not auth_configured(settings):
        raise AuthNotConfiguredError()
    if credentials is None:
        raise AuthenticationError()
    try:
        return verify_access_token(credentials.credentials, settings, resolver)
    except TokenValidationError as exc:
        logger.info("access token rejected: %s", exc)
        raise AuthenticationError(
            "Invalid or expired access token", code="invalid_token", error="invalid_token"
        ) from None
    except KeySourceUnavailableError:
        logger.warning("access token not verifiable: JWKS unavailable")
        raise ServiceUnavailableError(
            "Authentication temporarily unavailable", code="auth_unavailable"
        ) from None


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_scopes(*scopes: str) -> Callable[[Principal], Principal]:
    """Dependency factory: the caller must hold every listed scope/permission, else 403."""
    required = frozenset(scopes)
    challenge = f'Bearer error="insufficient_scope", scope="{" ".join(sorted(required))}"'

    def _require_scopes(principal: CurrentPrincipal) -> Principal:
        if not required <= principal.scopes:
            raise PermissionDeniedError(
                "Insufficient scope",
                code="insufficient_scope",
                headers={"WWW-Authenticate": challenge},
            )
        return principal

    return _require_scopes


def require_user(principal: CurrentPrincipal) -> Principal:
    """The caller must be a human user, not a machine (client-credentials) token, else 403."""
    if not principal.is_user:
        raise PermissionDeniedError("This endpoint requires a user token", code="user_required")
    return principal


CurrentUser = Annotated[Principal, Depends(require_user)]
