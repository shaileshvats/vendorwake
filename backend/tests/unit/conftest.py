"""Fixtures for unit tests: isolated Settings, an RSA signing key and a fake JWKS resolver."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vendorwake.api.app import create_app
from vendorwake.core.config import Settings
from vendorwake.core.security import get_key_resolver

AUTH0_DOMAIN = "vendorwake-test.us.auth0.com"
AUDIENCE = "https://api.vendorwake.test"
ISSUER = f"https://{AUTH0_DOMAIN}/"
KID = "test-key-1"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "test",
        "auth0_domain": AUTH0_DOMAIN,
        "auth0_audience": AUDIENCE,
        "sentry_dsn": None,
        # Nothing listens on port 1; keeps the health check fast and deterministic.
        "temporal_address": "127.0.0.1:1",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="session")
def rsa_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeKeyResolver:
    """Stands in for PyJWKClient: serves one public key under KID, never touches the network."""

    def __init__(self, private_key: rsa.RSAPrivateKey) -> None:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        self._keys = {KID: jwt.PyJWK({**jwk, "kid": KID, "use": "sig", "alg": "RS256"})}

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        kid = jwt.get_unverified_header(token).get("kid")
        try:
            return self._keys[kid]
        except KeyError:
            raise jwt.PyJWKClientError(f"Unable to find a signing key that matches: {kid!r}") from None


@pytest.fixture
def make_token(rsa_private_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def _make_token(
        *,
        sub: str = "auth0|user-123",
        aud: str | list[str] = AUDIENCE,
        iss: str = ISSUER,
        expires_in: int = 300,
        key: Any = None,
        algorithm: str = "RS256",
        kid: str | None = KID,
        drop: tuple[str, ...] = (),
        **claims: Any,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {"sub": sub, "aud": aud, "iss": iss, "iat": now, "exp": now + expires_in}
        payload.update(claims)
        for name in drop:
            payload.pop(name, None)
        headers = {"kid": kid} if kid else None
        return jwt.encode(
            payload, key if key is not None else rsa_private_key, algorithm=algorithm, headers=headers
        )

    return _make_token


def build_client(app: FastAPI, resolver: Any | None) -> TestClient:
    if resolver is not None:
        app.dependency_overrides[get_key_resolver] = lambda: resolver
    return TestClient(app)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI, rsa_private_key: rsa.RSAPrivateKey) -> Iterator[TestClient]:
    with build_client(app, FakeKeyResolver(rsa_private_key)) as test_client:
        yield test_client
