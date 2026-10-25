# ruff: noqa: S105, S106 - test credentials
from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from vendorwake.integrations import auth0_agents
from vendorwake.integrations.auth0_agents import (
    TOOL_SCOPES,
    AgentTokenProvider,
    ToolPermissionDenied,
    ToolPermissionGuard,
    parse_agent_credentials,
)
from vendorwake.integrations.errors import NotConfiguredError, UpstreamError

from ..conftest import AUDIENCE, AUTH0_DOMAIN, FakeKeyResolver, make_settings
from .conftest import live_env, live_settings, mock_client

SECRET = "super-secret-client-secret"
CREDS = {
    "migration": {"client_id": "mig-client", "client_secret": SECRET},
    "research": {"client_id": "res-client", "client_secret": "other-secret"},
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def token_endpoint(tokens: list[str], *, status: int = 200) -> tuple[Callable, list[dict]]:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"https://{AUTH0_DOMAIN}/oauth/token"
        bodies.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(
                status, json={"error": "access_denied", "error_description": "Unauthorized"}
            )
        return httpx.Response(
            200, json={"access_token": tokens[len(bodies) - 1], "expires_in": 600, "token_type": "Bearer"}
        )

    return handler, bodies


def provider(handler, clock=None) -> AgentTokenProvider:
    return AgentTokenProvider(
        domain=AUTH0_DOMAIN,
        audience=AUDIENCE,
        credentials=parse_agent_credentials(json.dumps(CREDS)),
        client=mock_client(handler),
        clock=clock or Clock(),
    )


def test_parse_credentials_validates_and_hides_secret() -> None:
    parsed = parse_agent_credentials(SecretStr(json.dumps(CREDS)))
    assert parsed["migration"].client_id == "mig-client"
    assert SECRET not in repr(parsed)
    with pytest.raises(NotConfiguredError, match="not valid JSON") as info:
        parse_agent_credentials('{"migration": {"client_secret": "' + SECRET)
    assert SECRET not in str(info.value)
    with pytest.raises(NotConfiguredError, match="client_id and client_secret"):
        parse_agent_credentials({"migration": {"client_id": "x"}})


def test_token_is_requested_with_client_credentials_and_cached_until_expiry() -> None:
    handler, bodies = token_endpoint(["tok-1", "tok-2"])
    clock = Clock()
    p = provider(handler, clock)
    assert p.get_token("migration") == "tok-1"
    assert p.get_token("migration") == "tok-1"
    assert bodies == [
        {
            "grant_type": "client_credentials",
            "client_id": "mig-client",
            "client_secret": SECRET,
            "audience": AUDIENCE,
        }
    ]
    clock.now += 600 - 60 + 1  # past expires_in minus the refresh margin
    assert p.get_token("migration") == "tok-2"


def test_token_errors_never_include_the_secret(caplog: pytest.LogCaptureFixture) -> None:
    handler, _ = token_endpoint([], status=401)
    with pytest.raises(UpstreamError, match="access_denied") as info:
        provider(handler).get_token("migration")
    assert SECRET not in str(info.value) and SECRET not in caplog.text
    with pytest.raises(NotConfiguredError, match="unknown-agent"):
        provider(handler).get_token("unknown-agent")


@pytest.fixture
def guard_factory(rsa_private_key: rsa.RSAPrivateKey):
    def make(tokens: dict[str, str] | None = None) -> ToolPermissionGuard:
        p = None
        if tokens is not None:
            p = provider(lambda r: httpx.Response(500))
            p.get_token = lambda agent: tokens[agent]  # type: ignore[method-assign]
        return ToolPermissionGuard(
            make_settings(), token_provider=p, resolver=FakeKeyResolver(rsa_private_key)
        )

    return make


def agent_token(make_token, client_id: str, scopes: str) -> str:
    return make_token(sub=f"{client_id}@clients", gty="client-credentials", scope=scopes)


def test_scope_map_is_fixed() -> None:
    assert dict(TOOL_SCOPES) == {
        "read_docs": "docs:read",
        "read_repo": "repo:read",
        "propose_patch": "patch:propose",
        "push_branch": "repo:write",
    }
    with pytest.raises(TypeError):
        TOOL_SCOPES["push_branch"] = "docs:read"  # type: ignore[index]


def test_allows_operation_covered_by_verified_scope(guard_factory, make_token) -> None:
    token = agent_token(make_token, "mig-client", "repo:read patch:propose")
    guard = guard_factory({"migration": token})
    decision = guard.check(agent="migration", operation="propose_patch")
    assert decision.allowed and decision.reason == "granted" and decision.subject == "mig-client@clients"
    assert guard.require(agent="migration", operation="read_repo").allowed


def test_push_without_repo_write_is_denied_and_logged(guard_factory, make_token, caplog) -> None:
    caplog.set_level(logging.INFO)
    token = agent_token(make_token, "mig-client", "repo:read patch:propose")
    guard = guard_factory({"migration": token})
    decision = guard.check(agent="migration", operation="push_branch")
    assert (
        not decision.allowed
        and decision.reason == "missing_scope"
        and decision.required_scope == "repo:write"
    )
    with pytest.raises(ToolPermissionDenied, match="missing_scope"):
        guard.require(agent="migration", operation="push_branch")
    denied = [r for r in caplog.records if r.getMessage() == "agent tool call denied"]
    assert denied and denied[0].operation == "push_branch"
    assert token not in caplog.text


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("unknown_op", "unknown_operation"),
        ("user_token", "not_an_agent_token"),
        ("other_client", "token_belongs_to_another_client"),
        ("forged", "invalid_token"),
        ("expired", "invalid_token"),
        ("wrong_audience", "invalid_token"),
    ],
)
def test_denials(case: str, reason: str, guard_factory, make_token) -> None:
    forger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tokens = {
        "unknown_op": agent_token(make_token, "mig-client", "repo:write"),
        "user_token": make_token(sub="auth0|human", scope="repo:write"),
        "other_client": agent_token(make_token, "res-client", "repo:write"),
        "forged": make_token(
            sub="mig-client@clients", gty="client-credentials", scope="repo:write", key=forger
        ),
        "expired": make_token(
            sub="mig-client@clients", gty="client-credentials", scope="repo:write", expires_in=-120
        ),
        "wrong_audience": make_token(
            sub="mig-client@clients", gty="client-credentials", scope="repo:write", aud="x"
        ),
    }
    guard = guard_factory({"migration": tokens[case]})
    operation = "self_grant_scopes" if case == "unknown_op" else "push_branch"
    decision = guard.check(agent="migration", operation=operation)
    assert not decision.allowed and decision.reason == reason


def test_fails_closed_without_auth_config_or_token(guard_factory, make_token, rsa_private_key) -> None:
    unconfigured = ToolPermissionGuard(
        make_settings(auth0_domain=""), resolver=FakeKeyResolver(rsa_private_key)
    )
    assert (
        unconfigured.check(agent="migration", operation="read_docs", token="x").reason
        == "auth_not_configured"
    )
    assert guard_factory(None).check(agent="migration", operation="read_docs").reason == "no_token"


def test_factories_require_configuration() -> None:
    with pytest.raises(NotConfiguredError, match="VW_AUTH0_AGENT_CREDENTIALS"):
        auth0_agents.guard_from_settings(make_settings())
    with pytest.raises(NotConfiguredError, match="VW_AUTH0_DOMAIN"):
        auth0_agents.token_provider_from_settings(make_settings(auth0_domain=""))
    built = auth0_agents.token_provider_from_settings(
        make_settings(auth0_agent_credentials=SecretStr(json.dumps(CREDS)))
    )
    assert built.agents == {"migration", "research"} and SECRET not in repr(built)


@pytest.mark.live
@live_env("VW_AUTH0_DOMAIN", "VW_AUTH0_AUDIENCE", "VW_AUTH0_AGENT_CREDENTIALS")
def test_live_agent_token_is_verified_by_guard() -> None:
    settings = live_settings()
    guard = auth0_agents.guard_from_settings(settings)
    agent = sorted(parse_agent_credentials(settings.auth0_agent_credentials))[0]
    decision = guard.check(agent=agent, operation="read_docs")
    assert decision.subject and decision.subject.endswith("@clients"), decision
