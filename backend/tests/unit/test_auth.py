from __future__ import annotations

from collections.abc import Callable

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from vendorwake.api.app import create_app
from vendorwake.core.security import Principal, get_key_resolver, require_scopes

from .conftest import AUDIENCE, FakeKeyResolver, build_client, make_settings


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_valid_user_token_returns_me(client: TestClient, make_token: Callable[..., str]) -> None:
    token = make_token(
        scope="openid profile read:projects", permissions=["read:alerts"], email="dev@example.com"
    )
    response = client.get("/api/me", headers=bearer(token))
    assert response.status_code == 200
    assert response.json() == {
        "subject": "auth0|user-123",
        "email": "dev@example.com",
        "scopes": ["openid", "profile", "read:alerts", "read:projects"],
    }


def test_audience_list_containing_ours_is_accepted(
    client: TestClient, make_token: Callable[..., str]
) -> None:
    token = make_token(aud=[AUDIENCE, "https://vendorwake-test.us.auth0.com/userinfo"])
    assert client.get("/api/me", headers=bearer(token)).status_code == 200


def test_missing_token_is_401_with_challenge(client: TestClient) -> None:
    response = client.get("/api/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"error": {"code": "unauthenticated", "message": "Authentication required"}}


def test_non_bearer_scheme_is_401(client: TestClient) -> None:
    response = client.get("/api/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def _assert_invalid_token(response) -> None:
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    # Same generic body whatever the reason: no hint about which check failed.
    assert response.json() == {
        "error": {"code": "invalid_token", "message": "Invalid or expired access token"}
    }


def test_expired_token_is_401(client: TestClient, make_token: Callable[..., str]) -> None:
    _assert_invalid_token(client.get("/api/me", headers=bearer(make_token(expires_in=-120))))


def test_wrong_audience_is_401(client: TestClient, make_token: Callable[..., str]) -> None:
    _assert_invalid_token(
        client.get("/api/me", headers=bearer(make_token(aud="https://someone-else.example")))
    )


def test_wrong_issuer_is_401(client: TestClient, make_token: Callable[..., str]) -> None:
    _assert_invalid_token(client.get("/api/me", headers=bearer(make_token(iss="https://evil.example/"))))


@pytest.mark.parametrize("claim", ["exp", "iat", "sub"])
def test_missing_required_claim_is_401(
    client: TestClient, make_token: Callable[..., str], claim: str
) -> None:
    _assert_invalid_token(client.get("/api/me", headers=bearer(make_token(drop=(claim,)))))


def test_hs256_algorithm_confusion_is_401(client: TestClient, make_token: Callable[..., str]) -> None:
    token = make_token(key="guessed-secret-guessed-secret-guessed-secret!", algorithm="HS256")
    _assert_invalid_token(client.get("/api/me", headers=bearer(token)))


def test_token_signed_by_unknown_key_is_401(make_token: Callable[..., str], client: TestClient) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _assert_invalid_token(client.get("/api/me", headers=bearer(make_token(key=other_key))))


def test_unknown_kid_is_401(client: TestClient, make_token: Callable[..., str]) -> None:
    _assert_invalid_token(client.get("/api/me", headers=bearer(make_token(kid="rotated-away"))))


def test_garbage_token_is_401(client: TestClient) -> None:
    _assert_invalid_token(client.get("/api/me", headers=bearer("not-a-jwt")))


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "m2m-client-id@clients", "gty": "client-credentials"},
        {"sub": "m2m-client-id@clients"},
        {"sub": "some-client", "gty": "client-credentials"},
    ],
)
def test_agent_token_on_me_is_403(client: TestClient, make_token: Callable[..., str], claims: dict) -> None:
    response = client.get("/api/me", headers=bearer(make_token(**claims)))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "user_required"


def _scoped_app(settings, resolver) -> FastAPI:
    app = create_app(settings)

    @app.get("/api/_test/scoped")
    def scoped(principal: Principal = Depends(require_scopes("read:projects", "write:projects"))) -> dict:  # noqa: B008
        return {"subject": principal.subject, "kind": principal.kind}

    return app


def test_require_scopes(make_token: Callable[..., str], rsa_private_key) -> None:
    with build_client(_scoped_app(make_settings(), None), FakeKeyResolver(rsa_private_key)) as client:
        partial = client.get("/api/_test/scoped", headers=bearer(make_token(scope="read:projects")))
        assert partial.status_code == 403
        assert partial.json()["error"]["code"] == "insufficient_scope"
        assert 'error="insufficient_scope"' in partial.headers["www-authenticate"]

        full = client.get(
            "/api/_test/scoped",
            headers=bearer(make_token(scope="read:projects", permissions=["write:projects"])),
        )
        assert full.status_code == 200
        assert full.json() == {"subject": "auth0|user-123", "kind": "user"}

        agent = client.get(
            "/api/_test/scoped",
            headers=bearer(
                make_token(sub="svc@clients", gty="client-credentials", scope="read:projects write:projects")
            ),
        )
        assert agent.status_code == 200
        assert agent.json()["kind"] == "agent"


@pytest.mark.parametrize(
    "overrides", [{"auth0_domain": ""}, {"auth0_audience": ""}, {"auth0_domain": "", "auth0_audience": ""}]
)
def test_auth_not_configured_fails_closed(make_token: Callable[..., str], overrides: dict) -> None:
    # No resolver override: exercises the real dependency chain, which must refuse before any network call.
    with TestClient(create_app(make_settings(**overrides))) as client:
        for headers in ({}, bearer(make_token())):
            response = client.get("/api/me", headers=headers)
            assert response.status_code == 503
            assert response.json() == {
                "error": {"code": "auth_not_configured", "message": "Authentication is not configured"}
            }


def test_auth_not_configured_fails_closed_even_with_resolver_override(
    make_token: Callable[..., str], rsa_private_key
) -> None:
    app = create_app(make_settings(auth0_audience=""))
    with build_client(app, FakeKeyResolver(rsa_private_key)) as client:
        assert client.get("/api/me", headers=bearer(make_token())).status_code == 503


def test_jwks_unreachable_is_503(make_token: Callable[..., str]) -> None:
    class DownResolver:
        def get_signing_key_from_jwt(self, token: str):
            raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url")

    with build_client(create_app(make_settings()), DownResolver()) as client:
        response = client.get("/api/me", headers=bearer(make_token()))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "auth_unavailable"


def test_real_resolver_is_cached_pyjwkclient() -> None:
    settings = make_settings()
    first, second = get_key_resolver(settings), get_key_resolver(settings)
    assert isinstance(first, jwt.PyJWKClient)
    assert first is second
    assert first.uri == "https://vendorwake-test.us.auth0.com/.well-known/jwks.json"
