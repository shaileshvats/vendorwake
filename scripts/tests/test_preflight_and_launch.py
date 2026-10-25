import json
import os

import httpx
import pytest

import launch
import preflight

ENV = {
    "VW_LLM_API_KEY": "sk-or-secret",
    "VW_LLM_BASE_URL": "https://openrouter.ai/api/v1",
    "VW_SERPAPI_API_KEY": "serp-secret",
    "VW_BROWSERBASE_API_KEY": "bb-secret",
    "VW_BROWSERBASE_PROJECT_ID": "proj-1",
    "VW_BACKBOARD_API_KEY": "bk-secret",
    "VW_SENTRY_DSN": "https://0123456789abcdef0123456789abcdef@o1.ingest.us.sentry.io/42",
    "NEXT_PUBLIC_SENTRY_DSN": "not-a-dsn",
    "VW_GITHUB_TOKEN": "ghp_secret",
    "RENDER_API_KEY": "rnd_secret",
    "AUTH0_DOMAIN": "t.us.auth0.com",
    "VW_AUTH0_DOMAIN": "t.us.auth0.com",
    "AUTH0_AUDIENCE": "https://api.vendorwake.local",
    "VW_AUTH0_AUDIENCE": "https://api.vendorwake.local",
    "AUTH0_CLIENT_ID": "web",
    "AUTH0_CLIENT_SECRET": "web-secret",
    "AUTH0_SECRET": "ab" * 32,
    "AUTH0_MGMT_CLIENT_ID": "mgmt",
    "AUTH0_MGMT_CLIENT_SECRET": "mgmt-secret",
    "VW_AUTH0_AGENT_CREDENTIALS": json.dumps(
        {
            "discovery": {"client_id": "d", "client_secret": "d-secret"},
            "migration": {"client_id": "m", "client_secret": "bad"},
        }
    ),
    "VW_TEMPORAL_EMBEDDED": "true",
    "VW_ENCRYPTION_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
}


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.startswith("https://openrouter.ai/api/v1/key"):
        assert request.headers["authorization"] == "Bearer sk-or-secret"
        return httpx.Response(200, json={"data": {"is_free_tier": True, "limit_remaining": None, "usage": 0}})
    if url.startswith("https://serpapi.com/account.json"):
        return httpx.Response(
            200,
            json={
                "plan_name": "Free Plan",
                "total_searches_left": 250,
                "searches_per_month": 250,
                "api_key": "serp-secret",
                "account_email": "x@y",
            },
        )
    if url == "https://api.browserbase.com/v1/projects/proj-1":
        assert request.headers["x-bb-api-key"] == "bb-secret"
        return httpx.Response(200, json={"id": "proj-1", "name": "vw", "concurrency": 1})
    if url.startswith("https://app.backboard.io/api/assistants"):
        return httpx.Response(401, json={"detail": "bad key"})
    if url == "https://api.github.com/rate_limit":
        return httpx.Response(200, json={"resources": {"core": {"limit": 5000, "remaining": 4999}}})
    if url.startswith("https://api.render.com/v1/owners"):
        return httpx.Response(
            200, json=[{"owner": {"id": "tea-1", "name": "me", "type": "user", "email": "e"}, "cursor": "c"}]
        )
    if url == "https://t.us.auth0.com/.well-known/openid-configuration":
        return httpx.Response(200, json={"issuer": "https://t.us.auth0.com/"})
    if url == "https://t.us.auth0.com/oauth/token":
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        if form["client_id"] == "mgmt":
            return httpx.Response(200, json={"access_token": "tok-secret", "scope": "read:clients create:clients"})
        if form["client_id"] == "web":
            return httpx.Response(403, json={"error": "unauthorized_client"})
        if form["client_id"] == "d" and form["client_secret"] == "d-secret":
            return httpx.Response(200, json={"access_token": "tok-secret", "scope": "docs:read"})
        return httpx.Response(401, json={"error": "access_denied"})
    raise AssertionError(url)


def test_preflight_table_and_no_secret_leak(tmp_path, capsys):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    results = {r.name: r for r in preflight.run_checks(ENV, http, only=None)}
    assert results["OpenRouter (VW_LLM_API_KEY)"].status == "ok"
    assert "searches left 250/250" in results["SerpApi (VW_SERPAPI_API_KEY)"].detail
    assert results["Browserbase"].status == "ok"
    assert results["Backboard (VW_BACKBOARD_API_KEY)"].status == "fail"
    assert results["Sentry DSNs (format only)"].detail == "malformed: NEXT_PUBLIC_SENTRY_DSN"
    assert results["GitHub (VW_GITHUB_TOKEN)"].detail == "core rate limit 4999/5000"
    assert results["Render (RENDER_API_KEY)"].status == "ok"
    assert results["Auth0 web app client"].status == "ok"
    assert "missing scopes" in results["Auth0 Management API app"].detail
    assert results["Auth0 agent 'discovery'"].detail == "scope: docs:read"
    assert results["Auth0 agent 'migration'"].status == "fail"
    assert results["Temporal"].status == "skip"
    assert results["VW_ENCRYPTION_KEY (Fernet format)"].status == "ok"
    table = preflight.render_table(list(results.values()))
    for key in (
        "sk-or-secret",
        "serp-secret",
        "bb-secret",
        "ghp_secret",
        "rnd_secret",
        "web-secret",
        "mgmt-secret",
        "d-secret",
        "tok-secret",
    ):
        assert key not in table


def test_preflight_env_file_mode(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\n")
    os.chmod(p, 0o644)
    assert preflight.check_env_file(p).status == "fail"
    os.chmod(p, 0o600)
    assert preflight.check_env_file(p).status == "ok"


def test_launch_commands_default_and_embedded():
    cmds = dict(launch.build_commands({"PORT": "10000"}))
    assert list(cmds) == ["api", "worker"]
    assert cmds["api"][-5:] == ["--port", "10000", "--proxy-headers", "--forwarded-allow-ips", "*"]
    cmds = dict(
        launch.build_commands(
            {
                "VW_TEMPORAL_EMBEDDED": "true",
                "VW_TEMPORAL_ADDRESS": "localhost:7233",
                "VW_TEMPORAL_NAMESPACE": "vw",
                "VW_RUN_WORKER": "false",
            }
        )
    )
    assert list(cmds) == ["temporal", "api"]
    assert cmds["temporal"][:3] == ["temporal", "server", "start-dev"] and "--namespace" in cmds["temporal"]
    with pytest.raises(SystemExit):
        launch.build_commands({"VW_TEMPORAL_EMBEDDED": "true", "VW_TEMPORAL_ADDRESS": "ns.acct.tmprl.cloud:7233"})
