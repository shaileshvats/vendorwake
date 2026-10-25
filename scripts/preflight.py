#!/usr/bin/env python3
"""Check every credential in .env with one cheap, read-only call and print a table.

Secret values are never printed; details come only from non-secret response fields. Where the free
tier's remaining allowance is cheaply knowable it is shown (SerpApi searches left, OpenRouter credit,
GitHub rate limit, Tiger storage vs the 750 MiB free-plan cap, Browserbase concurrency).

Endpoints (verified 2026-10-25):
  OpenRouter  GET https://openrouter.ai/api/v1/key                 https://openrouter.ai/docs/api-reference/limits
  SerpApi     GET https://serpapi.com/account.json?api_key=        https://serpapi.com/account-api (free, no quota)
  Browserbase GET https://api.browserbase.com/v1/projects/{id}     https://docs.browserbase.com/reference/api/get-a-project
  Backboard   GET https://app.backboard.io/api/assistants?limit=1  https://docs.backboard.io/api-reference/assistants/list
  GitHub      GET https://api.github.com/rate_limit (does not count against the limit)
              https://docs.github.com/en/rest/rate-limit/rate-limit
  Auth0       POST https://{domain}/oauth/token (client_credentials), GET /.well-known/openid-configuration
  Render      GET https://api.render.com/v1/owners                 https://api-docs.render.com/reference/list-owners
  Temporal    temporalio Client.connect(address, namespace=, api_key=, tls=True)
              https://docs.temporal.io/develop/python/temporal-client
  Tiger       psycopg connection with the owner and app URLs

Usage: python scripts/preflight.py [--env-file .env] [--only tiger,auth0,...]
Exit code 0 when no check failed (skipped checks are fine).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import socket
import ssl
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envfile  # noqa: E402

OK, FAIL, SKIP = "ok", "fail", "skip"
SYMBOL = {OK: "✓", FAIL: "✗", SKIP: "-"}
SENTRY_DSN = re.compile(r"^https://[0-9a-f]{32}(:[0-9a-f]{32})?@[A-Za-z0-9.-]+(:\d+)?/\d+$")
FREE_TIGER_BYTES = 750 * 1024 * 1024


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _http_fail(name: str, resp: httpx.Response) -> Check:
    return Check(name, FAIL, f"HTTP {resp.status_code}")


def _exc_fail(name: str, exc: Exception) -> Check:
    # Exception messages can echo URLs with credentials; report the class only.
    return Check(name, FAIL, type(exc).__name__)


def check_env_file(path: Path) -> Check:
    if not path.exists():
        return Check(".env file", FAIL, f"{path} missing")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        return Check(".env file", FAIL, f"mode {mode:o}; run chmod 600 {path.name}")
    return Check(".env file", OK, "mode 600")


def check_openrouter(env: dict[str, str], http: httpx.Client) -> Check:
    name = "OpenRouter (VW_LLM_API_KEY)"
    if not envfile.is_set(env, "VW_LLM_API_KEY"):
        return Check(name, SKIP, "not set")
    base = env.get("VW_LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    if "openrouter.ai" not in base:
        return Check(name, SKIP, f"VW_LLM_BASE_URL is not OpenRouter ({base})")
    resp = http.get(f"{base}/key", headers={"Authorization": f"Bearer {env['VW_LLM_API_KEY']}"})
    if resp.status_code != 200:
        return _http_fail(name, resp)
    data = resp.json().get("data", {})
    remaining = data.get("limit_remaining")
    credit = "no credit cap" if remaining is None else f"credit left {remaining}"
    return Check(name, OK, f"free_tier={data.get('is_free_tier')}, {credit}, usage {data.get('usage')}")


def check_serpapi(env: dict[str, str], http: httpx.Client) -> Check:
    name = "SerpApi (VW_SERPAPI_API_KEY)"
    if not envfile.is_set(env, "VW_SERPAPI_API_KEY"):
        return Check(name, SKIP, "not set")
    resp = http.get("https://serpapi.com/account.json", params={"api_key": env["VW_SERPAPI_API_KEY"]})
    if resp.status_code != 200:
        return _http_fail(name, resp)
    d = resp.json()
    return Check(
        name,
        OK,
        f"plan {d.get('plan_name')}, searches left {d.get('total_searches_left')}"
        f"/{d.get('searches_per_month')} per month",
    )


def check_browserbase(env: dict[str, str], http: httpx.Client) -> Check:
    name = "Browserbase"
    if not envfile.is_set(env, "VW_BROWSERBASE_API_KEY"):
        return Check(name, SKIP, "not set")
    pid = env.get("VW_BROWSERBASE_PROJECT_ID", "").strip()
    if not pid:
        return Check(name, FAIL, "VW_BROWSERBASE_PROJECT_ID is empty")
    resp = http.get(
        f"https://api.browserbase.com/v1/projects/{pid}", headers={"X-BB-API-Key": env["VW_BROWSERBASE_API_KEY"]}
    )
    if resp.status_code != 200:
        return _http_fail(name, resp)
    d = resp.json()
    return Check(name, OK, f"project '{d.get('name')}', concurrency {d.get('concurrency')}")


def check_backboard(env: dict[str, str], http: httpx.Client) -> Check:
    name = "Backboard (VW_BACKBOARD_API_KEY)"
    if not envfile.is_set(env, "VW_BACKBOARD_API_KEY"):
        return Check(name, SKIP, "not set")
    resp = http.get(
        "https://app.backboard.io/api/assistants",
        params={"limit": 1},
        headers={"X-API-Key": env["VW_BACKBOARD_API_KEY"]},
    )
    if resp.status_code != 200:
        return _http_fail(name, resp)
    return Check(name, OK, "assistants endpoint reachable")


def check_sentry(env: dict[str, str], _http: httpx.Client) -> Check:
    name = "Sentry DSNs (format only)"
    keys = [k for k in ("VW_SENTRY_DSN", "NEXT_PUBLIC_SENTRY_DSN") if env.get(k, "").strip()]
    if not keys:
        return Check(name, SKIP, "not set (Sentry disabled)")
    bad = [k for k in keys if not SENTRY_DSN.match(env[k].strip())]
    if bad:
        return Check(name, FAIL, f"malformed: {', '.join(bad)}")
    return Check(name, OK, ", ".join(keys))


def check_github(env: dict[str, str], http: httpx.Client) -> Check:
    name = "GitHub (VW_GITHUB_TOKEN)"
    if not envfile.is_set(env, "VW_GITHUB_TOKEN"):
        return Check(name, SKIP, "not set (unauthenticated: 60 requests/hour)")
    resp = http.get(
        "https://api.github.com/rate_limit",
        headers={
            "Authorization": f"Bearer {env['VW_GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if resp.status_code != 200:
        return _http_fail(name, resp)
    core = resp.json().get("resources", {}).get("core", {})
    if core.get("limit", 0) <= 60:
        return Check(name, FAIL, "token not accepted (unauthenticated limit)")
    return Check(name, OK, f"core rate limit {core.get('remaining')}/{core.get('limit')}")


def check_render(env: dict[str, str], http: httpx.Client) -> Check:
    name = "Render (RENDER_API_KEY)"
    if not envfile.is_set(env, "RENDER_API_KEY"):
        return Check(name, SKIP, "not set")
    resp = http.get(
        "https://api.render.com/v1/owners",
        params={"limit": 20},
        headers={"Authorization": f"Bearer {env['RENDER_API_KEY']}", "Accept": "application/json"},
    )
    if resp.status_code != 200:
        return _http_fail(name, resp)
    owners = [row["owner"] for row in resp.json()]
    return Check(name, OK, "workspaces: " + ", ".join(f"{o['name']} ({o['id']})" for o in owners))


def _token(http: httpx.Client, domain: str, cid: str, secret: str, audience: str) -> httpx.Response:
    return http.post(
        f"https://{domain}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": secret,
            "audience": audience,
        },
    )


def check_auth0(env: dict[str, str], http: httpx.Client) -> list[Check]:
    domain = env.get("AUTH0_DOMAIN", "").strip().removeprefix("https://").rstrip("/")
    if not envfile.is_set(env, "AUTH0_DOMAIN"):
        return [Check("Auth0", SKIP, "AUTH0_DOMAIN not set")]
    out: list[Check] = []
    try:
        disc = http.get(f"https://{domain}/.well-known/openid-configuration")
        out.append(
            Check("Auth0 tenant (OIDC discovery)", OK if disc.status_code == 200 else FAIL, f"HTTP {disc.status_code}")
        )
    except httpx.HTTPError as exc:
        return [_exc_fail("Auth0 tenant (OIDC discovery)", exc)]
    for key in ("VW_AUTH0_DOMAIN",):
        if env.get(key, "").strip().removeprefix("https://").rstrip("/") != domain:
            out.append(Check(f"Auth0 {key}", FAIL, "differs from AUTH0_DOMAIN"))
    if env.get("AUTH0_AUDIENCE", "").strip() != env.get("VW_AUTH0_AUDIENCE", "").strip():
        out.append(Check("Auth0 audience", FAIL, "AUTH0_AUDIENCE != VW_AUTH0_AUDIENCE"))

    if envfile.is_set(env, "AUTH0_MGMT_CLIENT_ID") and envfile.is_set(env, "AUTH0_MGMT_CLIENT_SECRET"):
        r = _token(
            http, domain, env["AUTH0_MGMT_CLIENT_ID"], env["AUTH0_MGMT_CLIENT_SECRET"], f"https://{domain}/api/v2/"
        )
        if r.status_code == 200:
            scopes = set(r.json().get("scope", "").split())
            needed = {
                "read:resource_servers",
                "create:resource_servers",
                "update:resource_servers",
                "read:clients",
                "create:clients",
                "update:clients",
                "read:client_keys",
                "read:client_grants",
                "create:client_grants",
                "update:client_grants",
            }
            missing = sorted(needed - scopes)
            out.append(
                Check(
                    "Auth0 Management API app",
                    FAIL if missing else OK,
                    f"missing scopes: {' '.join(missing)}" if missing else "token ok, scopes complete",
                )
            )
        else:
            out.append(_http_fail("Auth0 Management API app", r))
    else:
        out.append(Check("Auth0 Management API app", SKIP, "AUTH0_MGMT_* not set"))

    # The web app has no client_credentials grant: Auth0 answers unauthorized_client after it has
    # authenticated the client, and access_denied when the secret is wrong.
    if envfile.is_set(env, "AUTH0_CLIENT_ID") and envfile.is_set(env, "AUTH0_CLIENT_SECRET"):
        r = _token(http, domain, env["AUTH0_CLIENT_ID"], env["AUTH0_CLIENT_SECRET"], env.get("AUTH0_AUDIENCE", ""))
        err = r.json().get("error", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        ok = r.status_code == 200 or err == "unauthorized_client"
        out.append(
            Check(
                "Auth0 web app client",
                OK if ok else FAIL,
                "client secret accepted" if ok else f"HTTP {r.status_code} {err}",
            )
        )
    else:
        out.append(Check("Auth0 web app client", SKIP, "AUTH0_CLIENT_ID/SECRET not set"))
    secret = env.get("AUTH0_SECRET", "").strip()
    if secret:
        good = bool(re.fullmatch(r"[0-9a-fA-F]{64}", secret))
        out.append(Check("AUTH0_SECRET (format)", OK if good else FAIL, "32-byte hex" if good else "not 64 hex chars"))

    raw = env.get("VW_AUTH0_AGENT_CREDENTIALS", "").strip()
    if not raw:
        out.append(Check("Auth0 agent clients", SKIP, "VW_AUTH0_AGENT_CREDENTIALS not set"))
        return out
    try:
        agents = json.loads(raw)
    except ValueError:
        out.append(Check("Auth0 agent clients", FAIL, "VW_AUTH0_AGENT_CREDENTIALS is not JSON"))
        return out
    for agent, cred in sorted(agents.items()):
        r = _token(
            http, domain, cred.get("client_id", ""), cred.get("client_secret", ""), env.get("VW_AUTH0_AUDIENCE", "")
        )
        if r.status_code == 200:
            out.append(Check(f"Auth0 agent '{agent}'", OK, f"scope: {r.json().get('scope', '')}"))
        else:
            out.append(_http_fail(f"Auth0 agent '{agent}'", r))
    return out


def check_tiger(env: dict[str, str], connect: Callable | None = None) -> list[Check]:
    try:
        import psycopg
    except ImportError:
        return [Check("Tiger Cloud", SKIP, "psycopg not installed (pip install -r scripts/requirements.txt)")]
    connect = connect or psycopg.connect
    from tiger_bootstrap import DbUrl  # same directory

    out: list[Check] = []
    for key, label in (
        ("VW_MIGRATION_DATABASE_URL", "owner"),
        ("VW_DATABASE_URL", "app"),
        ("VW_WORKER_DATABASE_URL", "worker"),
    ):
        raw = env.get(key, "").strip()
        name = f"Tiger DB {label} ({key})"
        if not raw:
            out.append(Check(name, SKIP, "not set"))
            continue
        try:
            url = DbUrl.parse(raw)
            with connect(url.libpq(), connect_timeout=10) as conn:
                row = conn.execute(
                    "SELECT current_user, pg_database_size(current_database()), "
                    "(SELECT string_agg(extname || ' ' || extversion, ', ') FROM pg_extension "
                    " WHERE extname IN ('timescaledb', 'vector')), "
                    "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"
                ).fetchone()
        except Exception as exc:  # noqa: BLE001
            out.append(_exc_fail(name, exc))
            continue
        detail = f"user {row[0]} @ {url.host}, extensions: {row[2] or 'none'}"
        if label == "owner":
            detail += f", size {row[1] / 2**20:.0f}/750 MiB free-plan cap ({100 * row[1] / FREE_TIGER_BYTES:.0f}%)"
        status = OK
        if label != "owner" and row[3]:
            status, detail = FAIL, detail + ", BYPASSRLS is on"
        out.append(Check(name, status, detail))
    return out


def check_temporal(env: dict[str, str]) -> Check:
    name = "Temporal"
    addr = env.get("VW_TEMPORAL_ADDRESS", "localhost:7233").strip()
    ns = env.get("VW_TEMPORAL_NAMESPACE", "default").strip()
    if env.get("VW_TEMPORAL_EMBEDDED", "").strip().lower() == "true":
        return Check(name, SKIP, "embedded dev server in the API container (history not durable)")
    if addr.startswith(("localhost:", "127.0.0.1:")):
        return Check(name, SKIP, f"{addr} is local (docker compose); not checked")
    key = env.get("VW_TEMPORAL_API_KEY", "").strip()
    try:
        from temporalio.client import Client
    except ImportError:
        host, _, port = addr.rpartition(":")
        try:
            ctx = ssl.create_default_context()
            with (
                socket.create_connection((host, int(port)), timeout=10) as sock,
                ctx.wrap_socket(sock, server_hostname=host),
            ):
                pass
        except Exception as exc:  # noqa: BLE001
            return _exc_fail(name, exc)
        return Check(name, OK, f"TLS reachable {addr} (API key not verified: temporalio not installed)")

    async def probe() -> int:
        client = await Client.connect(addr, namespace=ns, api_key=key or None, tls=bool(key))
        return (await client.count_workflows()).count

    try:
        count = asyncio.run(asyncio.wait_for(probe(), 20))
    except Exception as exc:  # noqa: BLE001
        return _exc_fail(name, exc)
    return Check(name, OK, f"namespace {ns} at {addr}: {count} workflow executions")


def check_encryption_key(env: dict[str, str], _http: httpx.Client) -> Check:
    name = "VW_ENCRYPTION_KEY (Fernet format)"
    key = env.get("VW_ENCRYPTION_KEY", "").strip()
    if not key:
        return Check(name, SKIP, "not set")
    try:
        ok = len(base64.urlsafe_b64decode(key.encode())) == 32
    except ValueError:
        ok = False
    return Check(name, OK if ok else FAIL, "32-byte urlsafe base64" if ok else "not a Fernet key")


HTTP_CHECKS = {
    "openrouter": check_openrouter,
    "serpapi": check_serpapi,
    "browserbase": check_browserbase,
    "backboard": check_backboard,
    "sentry": check_sentry,
    "github": check_github,
    "render": check_render,
    "encryption": check_encryption_key,
}


def run_checks(
    env: dict[str, str],
    http: httpx.Client,
    only: set[str] | None = None,
    env_path: Path | None = None,
    tiger_connect: Callable | None = None,
) -> list[Check]:
    def want(name: str) -> bool:
        return only is None or name in only

    results: list[Check] = []
    if env_path is not None and want("envfile"):
        results.append(check_env_file(env_path))
    if want("tiger"):
        results += check_tiger(env, tiger_connect)
    if want("temporal"):
        results.append(check_temporal(env))
    if want("auth0"):
        results += check_auth0(env, http)
    for name, fn in HTTP_CHECKS.items():
        if want(name):
            try:
                results.append(fn(env, http))
            except httpx.HTTPError as exc:
                results.append(_exc_fail(name, exc))
    return results


def render_table(results: list[Check]) -> str:
    width = max(len(r.name) for r in results)
    return "\n".join(f"{SYMBOL[r.status]}  {r.name.ljust(width)}  {r.detail}" for r in results)


def main(argv: list[str] | None = None, http: httpx.Client | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only credential checks for .env")
    ap.add_argument("--env-file", type=Path, default=envfile.default_env_path())
    ap.add_argument("--only", help="comma-separated subset: envfile,tiger,temporal,auth0," + ",".join(HTTP_CHECKS))
    args = ap.parse_args(argv)
    env = envfile.load(args.env_file)
    only = set(args.only.split(",")) if args.only else None
    own = http is None
    http = http or httpx.Client(timeout=20)
    try:
        results = run_checks(env, http, only, env_path=args.env_file)
    finally:
        if own:
            http.close()
    print(render_table(results))
    failed = [r for r in results if r.status == FAIL]
    print(f"\n{len(results) - len(failed)} passed or skipped, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
