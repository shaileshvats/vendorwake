#!/usr/bin/env python3
"""Provision Vendorwake's Auth0 tenant idempotently through the Management API v2.

Creates or updates:
  * the API (resource server) whose identifier is VW_AUTH0_AUDIENCE, with the agent scopes, RS256,
    offline access (refresh tokens for the web app), RBAC + permissions in the access token;
  * the Regular Web Application used by the Next.js frontend (callbacks for localhost:3000 and the
    Render web URL);
  * one Machine-to-Machine application per agent with a client grant on the API:
      discovery -> docs:read, repository -> repo:read, migration -> repo:read patch:propose
    (repo:write exists on the API but no agent is granted it; see docs/m0-findings.md, Scenario 6).

Writes AUTH0_CLIENT_ID / AUTH0_CLIENT_SECRET / AUTH0_SECRET (generated if empty), AUTH0_AUDIENCE,
VW_AUTH0_DOMAIN, VW_AUTH0_AUDIENCE and VW_AUTH0_AGENT_CREDENTIALS into .env (mode 600). Secrets are
never printed.

Reads: AUTH0_DOMAIN, AUTH0_MGMT_CLIENT_ID, AUTH0_MGMT_CLIENT_SECRET, VW_AUTH0_AUDIENCE (or
AUTH0_AUDIENCE), RENDER_WEB_URL (written by deploy_render.py; overridden by --web-url).

The Management API application needs these scopes (Dashboard > Applications > APIs > Auth0
Management API > Machine to Machine Applications):
  read:resource_servers create:resource_servers update:resource_servers
  read:clients create:clients update:clients read:client_keys
  read:client_grants create:client_grants update:client_grants

Reference (verified 2026-10-25):
  token:            https://auth0.com/docs/secure/tokens/access-tokens/management-api-access-tokens/get-management-api-access-tokens-for-production
  resource servers: https://auth0.com/docs/api/management/v2/resource-servers/get-resource-servers-by-id
                    https://auth0.com/docs/api/management/v2/resource-servers/post-resource-servers
                    https://auth0.com/docs/api/management/v2/resource-servers/patch-resource-servers-by-id
  clients:          https://auth0.com/docs/api/management/v2/clients/get-clients
                    https://auth0.com/docs/api/management/v2/clients/post-clients
                    https://auth0.com/docs/api/management/v2/clients/patch-clients-by-id
                    https://auth0.com/docs/api/management/v2/clients/get-clients-by-id
  client grants:    https://auth0.com/docs/api/management/v2/client-grants/get-client-grants
                    https://auth0.com/docs/api/management/v2/client-grants/post-client-grants
                    https://auth0.com/docs/api/management/v2/client-grants/patch-client-grants-by-id
  RBAC:             https://auth0.com/docs/get-started/apis/enable-role-based-access-control-for-apis
  app access:       https://auth0.com/docs/get-started/applications/application-access-to-apis-client-grants

Usage:
  python scripts/auth0_setup.py --dry-run
  python scripts/auth0_setup.py [--web-url https://vendorwake-web.onrender.com]
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envfile  # noqa: E402

LOCAL_WEB_URL = "http://localhost:3000"
API_NAME = "Vendorwake API"
WEB_APP_NAME = "Vendorwake Web"
AGENT_APP_NAME = "Vendorwake Agent: {agent}"

API_SCOPES: dict[str, str] = {
    "docs:read": "Read vendor documentation, changelogs and release notes",
    "repo:read": "Read repository contents and dependency manifests",
    "patch:propose": "Propose a patch or migration branch for review",
    "repo:write": "Push branches or open pull requests (granted to no agent by default)",
}
# One M2M client per logical agent, each with only what its workflow steps require
# (backend/vendorwake/workflows/activities.py `_require(...)` calls). No agent gets repo:write.
AGENT_SCOPES: dict[str, list[str]] = {
    "discovery": ["docs:read"],
    "repository": ["repo:read"],
    "impact": ["repo:read"],
    "verification": ["repo:read"],
    "migration": ["repo:read", "patch:propose"],
}

# Resource server settings we enforce. token_dialect access_token_authz adds the `permissions` claim
# (RBAC doc above); the backend accepts scopes from `scope` and `permissions`.
RESOURCE_SERVER_SETTINGS: dict[str, Any] = {
    "signing_alg": "RS256",
    "allow_offline_access": True,
    "skip_consent_for_verifiable_first_party_clients": True,
    "enforce_policies": True,
    "token_dialect": "access_token_authz",
}
# Users may log in through any first-party app (the web app); M2M access needs an explicit client
# grant. Values from the POST /resource-servers schema. Tenants without this feature reject the
# field; we then retry without it (the tenant default applies).
SUBJECT_TYPE_AUTHORIZATION = {
    "user": {"policy": "allow_all"},
    "client": {"policy": "require_client_grant"},
}

CLIENT_LIST_FIELDS = (
    "client_id,name,app_type,callbacks,allowed_logout_urls,web_origins,grant_types,"
    "token_endpoint_auth_method,oidc_conformant,description,is_first_party,jwt_configuration"
)


class Auth0Error(RuntimeError):
    pass


def normalize_domain(value: str) -> str:
    d = value.strip()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix) :]
    return d.rstrip("/")


def normalize_url(value: str) -> str:
    return value.strip().rstrip("/")


def _error_text(resp: httpx.Response) -> str:
    # Auth0 errors are {"statusCode", "error", "message", "errorCode"}; none carry secrets.
    try:
        body = resp.json()
        return f"{body.get('error', '')}: {body.get('message', '')} ({body.get('errorCode', '')})"
    except ValueError:
        return resp.text[:300]


def get_mgmt_token(http: httpx.Client, domain: str, client_id: str, client_secret: str) -> str:
    resp = http.post(
        f"https://{domain}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "audience": f"https://{domain}/api/v2/",
        },
    )
    if resp.status_code != 200:
        raise Auth0Error(f"Management API token request failed: HTTP {resp.status_code} {_error_text(resp)}")
    return resp.json()["access_token"]


class Mgmt:
    """Thin Management API v2 client with 429 back-off."""

    def __init__(self, http: httpx.Client, domain: str, token: str, sleep=time.sleep) -> None:
        self.http = http
        self.base = f"https://{domain}/api/v2"
        self.headers = {"Authorization": f"Bearer {token}"}
        self.sleep = sleep

    def request(self, method: str, path: str, *, ok=(200, 201), allow_404=False, **kw) -> Any:
        for attempt in range(5):
            resp = self.http.request(method, self.base + path, headers=self.headers, **kw)
            if resp.status_code == 429 and attempt < 4:
                self.sleep(min(2**attempt, 10))
                continue
            break
        if allow_404 and resp.status_code == 404:
            return None
        if resp.status_code not in ok:
            raise Auth0Error(f"{method} {path.split('?')[0]} -> HTTP {resp.status_code} {_error_text(resp)}")
        return resp.json() if resp.content else None

    def paginate(self, path: str, params: dict[str, Any], key: str) -> list[dict]:
        # Offset pagination: include_totals=true returns {start, limit, total, <key>: [...]}.
        out: list[dict] = []
        page = 0
        while True:
            body = self.request("GET", path, params={**params, "page": page, "per_page": 100, "include_totals": "true"})
            items = body.get(key, []) if isinstance(body, dict) else body
            out.extend(items)
            total = body.get("total", len(out)) if isinstance(body, dict) else len(out)
            if not items or len(out) >= total:
                return out
            page += 1


@dataclass
class Result:
    actions: list[str] = field(default_factory=list)
    web_client_id: str = ""
    web_client_secret: str = ""
    agents: dict[str, dict[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class Provisioner:
    """Each ensure_* step reads current state, records an action line, and writes unless dry_run.

    With mgmt=None (dry run without credentials) the tenant is assumed to be empty.
    """

    def __init__(self, mgmt: Mgmt | None, *, dry_run: bool, env: dict[str, str]) -> None:
        self.mgmt = mgmt
        self.dry_run = dry_run or mgmt is None
        self.env = env
        self.result = Result()
        self._clients: list[dict] | None = None

    def _act(self, line: str) -> None:
        self.result.actions.append(("[dry-run] " if self.dry_run else "") + line)
        print(("  [dry-run] " if self.dry_run else "  ") + line)

    # --- resource server -----------------------------------------------------------------------
    def ensure_resource_server(self, audience: str) -> None:
        current = None
        if self.mgmt:
            current = self.mgmt.request("GET", f"/resource-servers/{quote(audience, safe='')}", allow_404=True)
        desired_scopes = [{"value": v, "description": d} for v, d in API_SCOPES.items()]
        if current is None:
            body = {
                "name": API_NAME,
                "identifier": audience,
                "scopes": desired_scopes,
                **RESOURCE_SERVER_SETTINGS,
                "subject_type_authorization": SUBJECT_TYPE_AUTHORIZATION,
            }
            self._act(f"CREATE API '{API_NAME}' identifier={audience} scopes={','.join(API_SCOPES)}")
            if not self.dry_run:
                self._write_resource_server("POST", "/resource-servers", body)
            return
        patch: dict[str, Any] = {}
        existing_scopes = current.get("scopes") or []
        have = {s.get("value") for s in existing_scopes}
        missing = [s for s in desired_scopes if s["value"] not in have]
        if missing:
            # PATCH replaces the scopes array, so send existing + missing (never drop scopes).
            patch["scopes"] = [*existing_scopes, *missing]
        for k, v in RESOURCE_SERVER_SETTINGS.items():
            if current.get(k) != v:
                patch[k] = v
        # Only set the access policy when the API has none; an existing one is a deliberate choice.
        if current.get("subject_type_authorization") is None:
            patch["subject_type_authorization"] = SUBJECT_TYPE_AUTHORIZATION
        if not patch:
            self._act(f"OK API '{current.get('name')}' identifier={audience}")
            return
        self._act(f"UPDATE API identifier={audience}: {', '.join(sorted(patch))}")
        if not self.dry_run:
            self._write_resource_server("PATCH", f"/resource-servers/{quote(audience, safe='')}", patch)

    def _write_resource_server(self, method: str, path: str, body: dict[str, Any]) -> None:
        try:
            self.mgmt.request(method, path, json=body)
        except Auth0Error as exc:
            if "subject_type_authorization" in body and "subject_type_authorization" in str(exc):
                body = {k: v for k, v in body.items() if k != "subject_type_authorization"}
                self.result.warnings.append(
                    "tenant rejected subject_type_authorization; retried without it (tenant default applies)"
                )
                self.mgmt.request(method, path, json=body)
            else:
                raise

    # --- clients -------------------------------------------------------------------------------
    def _all_clients(self) -> list[dict]:
        if self._clients is None:
            self._clients = (
                self.mgmt.paginate("/clients", {"fields": CLIENT_LIST_FIELDS, "include_fields": "true"}, "clients")
                if self.mgmt
                else []
            )
        return self._clients

    def _find_client(self, name: str, known_id: str) -> dict | None:
        clients = self._all_clients()
        if known_id:
            for c in clients:
                if c.get("client_id") == known_id:
                    return c
        matches = [c for c in clients if c.get("name") == name]
        if len(matches) > 1:
            raise Auth0Error(f"{len(matches)} applications are named '{name}'; delete the duplicates first")
        return matches[0] if matches else None

    def ensure_client(
        self, name: str, desired: dict[str, Any], *, known_id: str = "", known_secret: str = ""
    ) -> tuple[str, str]:
        """Returns (client_id, client_secret). List fields are merged, never shrunk."""
        current = self._find_client(name, known_id)
        if current is None:
            self._act(f"CREATE application '{name}' ({desired['app_type']})")
            if self.dry_run:
                return f"<new:{name}>", ""
            created = self.mgmt.request("POST", "/clients", json={"name": name, **desired})
            self._clients = None
            return created["client_id"], created["client_secret"]

        client_id = current["client_id"]
        patch: dict[str, Any] = {}
        for k, v in desired.items():
            cur = current.get(k)
            if isinstance(v, list):
                merged = list(cur or [])
                merged += [x for x in v if x not in merged]
                if merged != list(cur or []):
                    patch[k] = merged
            elif isinstance(v, dict):
                if not isinstance(cur, dict) or any(cur.get(dk) != dv for dk, dv in v.items()):
                    patch[k] = {**(cur if isinstance(cur, dict) else {}), **v}
            elif cur != v:
                patch[k] = v
        if patch:
            self._act(f"UPDATE application '{name}' client_id={client_id}: {', '.join(sorted(patch))}")
            if not self.dry_run:
                self.mgmt.request("PATCH", f"/clients/{client_id}", json=patch)
        else:
            self._act(f"OK application '{name}' client_id={client_id}")

        if known_id == client_id and known_secret:
            return client_id, known_secret
        if self.dry_run:
            self._act(f"READ client secret of '{name}' (needs read:client_keys)")
            return client_id, ""
        body = self.mgmt.request(
            "GET", f"/clients/{client_id}", params={"fields": "client_secret", "include_fields": "true"}
        )
        secret = (body or {}).get("client_secret", "")
        if not secret:
            raise Auth0Error(f"could not read the secret of '{name}': grant read:client_keys to the Management API app")
        return client_id, secret

    # --- client grants -------------------------------------------------------------------------
    def ensure_client_grant(self, client_id: str, audience: str, scopes: list[str], label: str) -> None:
        grants: list[dict] = []
        if self.mgmt and not client_id.startswith("<new:"):
            grants = self.mgmt.paginate(
                "/client-grants", {"client_id": client_id, "audience": audience}, "client_grants"
            )
            # A client may also hold a subject_type=user grant for the same API; we manage the M2M one.
            grants = [g for g in grants if g.get("subject_type") in (None, "client")]
        if not grants:
            self._act(f"CREATE client grant {label} -> {audience} scope={' '.join(scopes)}")
            if not self.dry_run:
                self.mgmt.request(
                    "POST", "/client-grants", json={"client_id": client_id, "audience": audience, "scope": scopes}
                )
            return
        grant = grants[0]
        if sorted(grant.get("scope") or []) == sorted(scopes):
            self._act(f"OK client grant {label} scope={' '.join(scopes)}")
            return
        # Least privilege: the grant is set to exactly the scopes this agent needs.
        self._act(
            f"UPDATE client grant {label}: scope {' '.join(sorted(grant.get('scope') or []))} -> {' '.join(scopes)}"
        )
        if not self.dry_run:
            self.mgmt.request("PATCH", f"/client-grants/{grant['id']}", json={"scope": scopes})


def web_app_settings(web_urls: list[str]) -> dict[str, Any]:
    return {
        "app_type": "regular_web",
        "description": "Vendorwake dashboard (Next.js, @auth0/nextjs-auth0)",
        "callbacks": [f"{u}/auth/callback" for u in web_urls],
        "allowed_logout_urls": list(web_urls),
        "web_origins": list(web_urls),
        "grant_types": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": "client_secret_post",
        "oidc_conformant": True,
        "is_first_party": True,
        "jwt_configuration": {"alg": "RS256"},
    }


def agent_app_settings(agent: str) -> dict[str, Any]:
    return {
        "app_type": "non_interactive",
        "description": f"Vendorwake {agent} agent identity (client credentials)",
        "grant_types": ["client_credentials"],
        "token_endpoint_auth_method": "client_secret_post",
        "oidc_conformant": True,
        "is_first_party": True,
        "jwt_configuration": {"alg": "RS256"},
    }


def provision(mgmt: Mgmt | None, env: dict[str, str], audience: str, web_urls: list[str], dry_run: bool) -> Result:
    p = Provisioner(mgmt, dry_run=dry_run, env=env)
    p.ensure_resource_server(audience)

    web_id, web_secret = p.ensure_client(
        WEB_APP_NAME,
        web_app_settings(web_urls),
        known_id=env.get("AUTH0_CLIENT_ID", "").strip(),
        known_secret=env.get("AUTH0_CLIENT_SECRET", "").strip(),
    )
    p.result.web_client_id, p.result.web_client_secret = web_id, web_secret

    known_agents: dict[str, dict[str, str]] = {}
    raw = env.get("VW_AUTH0_AGENT_CREDENTIALS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                known_agents = {k: v for k, v in parsed.items() if isinstance(v, dict)}
        except ValueError:
            p.result.warnings.append("VW_AUTH0_AGENT_CREDENTIALS is not valid JSON; it will be rewritten")

    for agent, scopes in AGENT_SCOPES.items():
        known = known_agents.get(agent, {})
        cid, secret = p.ensure_client(
            AGENT_APP_NAME.format(agent=agent),
            agent_app_settings(agent),
            known_id=known.get("client_id", ""),
            known_secret=known.get("client_secret", ""),
        )
        p.ensure_client_grant(cid, audience, scopes, label=agent)
        p.result.agents[agent] = {"client_id": cid, "client_secret": secret}
    return p.result


def env_updates(result: Result, env: dict[str, str], domain: str, audience: str) -> dict[str, str]:
    updates = {
        "AUTH0_DOMAIN": domain,
        "VW_AUTH0_DOMAIN": domain,
        "AUTH0_AUDIENCE": audience,
        "VW_AUTH0_AUDIENCE": audience,
        "AUTH0_CLIENT_ID": result.web_client_id,
        "AUTH0_CLIENT_SECRET": result.web_client_secret,
        "VW_AUTH0_AGENT_CREDENTIALS": json.dumps(result.agents, separators=(",", ":"), sort_keys=True),
    }
    if not env.get("AUTH0_SECRET", "").strip():
        updates["AUTH0_SECRET"] = secrets.token_hex(32)
    return updates


def main(argv: list[str] | None = None, http: httpx.Client | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env-file", type=Path, default=envfile.default_env_path())
    ap.add_argument("--web-url", help="public frontend URL, e.g. https://vendorwake-web.onrender.com")
    ap.add_argument("--audience", help="API identifier (default: VW_AUTH0_AUDIENCE)")
    ap.add_argument("--dry-run", action="store_true", help="print planned actions; read-only calls only")
    args = ap.parse_args(argv)

    env = envfile.load(args.env_file)
    domain = normalize_domain(env.get("AUTH0_DOMAIN", "") or env.get("VW_AUTH0_DOMAIN", ""))
    audience = (args.audience or env.get("VW_AUTH0_AUDIENCE") or env.get("AUTH0_AUDIENCE") or "").strip()
    web_url = normalize_url(args.web_url or env.get("RENDER_WEB_URL", ""))
    web_urls = [LOCAL_WEB_URL] + ([web_url] if web_url and web_url != LOCAL_WEB_URL else [])

    have_creds = (
        envfile.is_set(env, "AUTH0_DOMAIN")
        and envfile.is_set(env, "AUTH0_MGMT_CLIENT_ID")
        and envfile.is_set(env, "AUTH0_MGMT_CLIENT_SECRET")
    )
    if not audience:
        print("error: VW_AUTH0_AUDIENCE is empty", file=sys.stderr)
        return 2
    if not have_creds and not args.dry_run:
        print("error: set AUTH0_DOMAIN, AUTH0_MGMT_CLIENT_ID and AUTH0_MGMT_CLIENT_SECRET in .env", file=sys.stderr)
        return 2

    print(f"Auth0 tenant: {domain or '<unset>'}   API identifier: {audience}")
    print(f"Web app URLs: {', '.join(web_urls)}")
    own_http = http is None
    http = http or httpx.Client(timeout=30)
    try:
        mgmt = None
        if have_creds:
            token = get_mgmt_token(http, domain, env["AUTH0_MGMT_CLIENT_ID"], env["AUTH0_MGMT_CLIENT_SECRET"])
            mgmt = Mgmt(http, domain, token)
        elif args.dry_run:
            print("  (no Management API credentials: planning against an empty tenant)")
        result = provision(mgmt, env, audience, web_urls, args.dry_run)
    except Auth0Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if own_http:
            http.close()

    for w in result.warnings:
        print(f"warning: {w}")
    updates = env_updates(result, env, domain, audience)
    if args.dry_run or mgmt is None:
        print(f"[dry-run] would write to {args.env_file}: {', '.join(sorted(updates))}")
        return 0
    changed = envfile.write_updates(args.env_file, updates, header="Written by scripts/auth0_setup.py")
    print(f"Updated {args.env_file} (mode 600): {', '.join(changed) if changed else 'no changes'}")
    print("Agent client IDs: " + ", ".join(f"{a}={c['client_id']}" for a, c in result.agents.items()))
    print(f"Web app client ID: {result.web_client_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
