#!/usr/bin/env python3
"""Create or update Vendorwake's Render services idempotently through the Render public API v1.

Free plan only (no money is spent). Render has no free background workers, so there are two free
web services, both built with Docker from github.com/shaileshvats/vendorwake (branch main):

  vendorwake-api  backend/Dockerfile.worker, context = repo root. The combined image runs uvicorn
                  and the Temporal worker in one container (scripts/container/launch.py), plus an
                  embedded `temporal server start-dev` when VW_TEMPORAL_EMBEDDED=true.
                  Health check /api/health.
  vendorwake-web  frontend/Dockerfile, context = frontend/. NEXT_PUBLIC_API_BASE_URL = API URL
                  (Render passes service env vars to Docker builds as build args).

Free web services have no pre-deploy command (paid only), so migrations run from your machine
(docs/deploy.md), or, opt-in, at container start with --migrate-on-start (this sends the owner
database URL to Render).

Env vars pushed per service (from .env, empty values skipped):
  api: every VW_* key except VW_MIGRATION_DATABASE_URL, VW_APP_DB_PASSWORD, VW_WORKER_DB_PASSWORD;
       plus PORT=8000, VW_ENV=production (if .env says local) and VW_CORS_ORIGINS=<web URL>.
  web: AUTH0_DOMAIN AUTH0_CLIENT_ID AUTH0_CLIENT_SECRET AUTH0_SECRET AUTH0_AUDIENCE, NEXT_PUBLIC_*,
       plus PORT=3000, APP_BASE_URL=<web URL>, NEXT_PUBLIC_API_BASE_URL=<api URL>.
Never pushed: RENDER_*, AUTH0_MGMT_*, POSTGRES_*, the owner DB URL and role passwords.
The environment PUT replaces the service's whole variable list (Render API doc below), so .env is
the source of truth.

Writes RENDER_OWNER_ID, RENDER_API_URL and RENDER_WEB_URL (provisioning-only) into .env.
Afterwards run `python scripts/auth0_setup.py` again: it reads RENDER_WEB_URL for the callbacks.

Render API reference (verified 2026-10-25, https://api-docs.render.com/reference):
  GET  /v1/owners                                 list-owners
  GET  /v1/services?name=&ownerId=                list-services   -> [{service, cursor}]
  POST /v1/services                               create-service  -> {service, deployId}
  PATCH /v1/services/{id}                         update-service  (not deployed automatically)
  GET  /v1/services/{id}/env-vars                 get-env-vars-for-service -> [{envVar, cursor}]
  PUT  /v1/services/{id}/env-vars                 update-env-vars-for-service (replaces all)
  POST /v1/services/{id}/deploys                  create-deploy   (deployMode deploy_only|build_and_deploy)
  GET  /v1/services/{id}/deploys/{deployId}       retrieve-deploy
Docs: https://render.com/docs/docker (env vars -> build args, BuildKit, .dockerignore),
      https://render.com/docs/free (free web services only; spin down after 15 min idle),
      https://render.com/docs/deploys (pre-deploy: paid only; auto-deploy needs a linked Git provider).

Usage:
  python scripts/deploy_render.py --dry-run
  python scripts/deploy_render.py [--region virginia] [--no-wait]
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envfile  # noqa: E402

API_BASE = "https://api.render.com/v1"
REPO_URL = "https://github.com/shaileshvats/vendorwake"
BRANCH = "main"
API_SERVICE = "vendorwake-api"
WEB_SERVICE = "vendorwake-web"
REGIONS = ("oregon", "ohio", "virginia", "frankfurt", "singapore")
TERMINAL_OK = {"live"}
TERMINAL_FAIL = {"build_failed", "update_failed", "canceled", "pre_deploy_failed", "deactivated"}

NEVER_SEND_PREFIXES = ("RENDER_", "AUTH0_MGMT_", "POSTGRES_")
API_EXCLUDE = {"VW_MIGRATION_DATABASE_URL", "VW_APP_DB_PASSWORD", "VW_WORKER_DB_PASSWORD"}
WEB_KEYS = ("AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET", "AUTH0_SECRET", "AUTH0_AUDIENCE")


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    dockerfile_path: str
    docker_context: str
    health_check_path: str

    def details(self, plan: str, region: str) -> dict[str, Any]:
        return {
            "runtime": "docker",
            "plan": plan,
            "region": region,
            "numInstances": 1,
            "healthCheckPath": self.health_check_path,
            "envSpecificDetails": {"dockerfilePath": self.dockerfile_path, "dockerContext": self.docker_context},
        }


SPECS = {
    API_SERVICE: ServiceSpec(API_SERVICE, "./backend/Dockerfile.worker", ".", "/api/health"),
    WEB_SERVICE: ServiceSpec(WEB_SERVICE, "./frontend/Dockerfile", "./frontend", "/"),
}


def _nonempty(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if v.strip() != ""}


def api_env(env: dict[str, str], web_url: str, *, migrate_on_start: bool = False) -> dict[str, str]:
    out = {
        k: v
        for k, v in _nonempty(env).items()
        if k.startswith("VW_") and (k not in API_EXCLUDE or (migrate_on_start and k == "VW_MIGRATION_DATABASE_URL"))
    }
    if out.get("VW_ENV", "local") == "local":
        out["VW_ENV"] = "production"
    if web_url:
        out["VW_CORS_ORIGINS"] = web_url
    if migrate_on_start:
        out["VW_MIGRATE_ON_START"] = "true"
    out["PORT"] = "8000"
    return dict(sorted(out.items()))


def web_env(env: dict[str, str], api_url: str, web_url: str) -> dict[str, str]:
    src = _nonempty(env)
    out = {k: src[k] for k in WEB_KEYS if k in src}
    out.update({k: v for k, v in src.items() if k.startswith("NEXT_PUBLIC_")})
    out["NEXT_PUBLIC_API_BASE_URL"] = api_url
    if web_url:
        out["APP_BASE_URL"] = web_url
    out["PORT"] = "3000"
    return dict(sorted(out.items()))


def assert_no_provisioning_keys(values: dict[str, str]) -> None:
    bad = [
        k for k in values if k.startswith(NEVER_SEND_PREFIXES) or k in {"VW_APP_DB_PASSWORD", "VW_WORKER_DB_PASSWORD"}
    ]
    if bad:
        raise RenderError(f"refusing to send provisioning-only keys to a service: {', '.join(bad)}")


def validate(env: dict[str, str], *, migrate_on_start: bool) -> list[str]:
    """Problems that would make the deployed app fail. Returned, not raised (dry-run shows them)."""
    problems = []
    db = env.get("VW_DATABASE_URL", "")
    if not db or "@localhost" in db or "@127.0.0.1" in db or "@postgres:" in db:
        problems.append("VW_DATABASE_URL points at a local database; run scripts/tiger_bootstrap.py first")
    for key in ("VW_AUTH0_DOMAIN", "AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET", "AUTH0_SECRET"):
        if not envfile.is_set(env, key):
            problems.append(f"{key} is not set; run scripts/auth0_setup.py first")
    addr = env.get("VW_TEMPORAL_ADDRESS", "localhost:7233").strip()
    embedded = env.get("VW_TEMPORAL_EMBEDDED", "").strip().lower() == "true"
    if addr in ("localhost:7233", "127.0.0.1:7233") and not embedded:
        problems.append("VW_TEMPORAL_ADDRESS is localhost:7233: set Temporal Cloud values or VW_TEMPORAL_EMBEDDED=true")
    if addr.endswith(".tmprl.cloud:7233") and not envfile.is_set(env, "VW_TEMPORAL_API_KEY"):
        problems.append("Temporal Cloud address without VW_TEMPORAL_API_KEY")
    if migrate_on_start and not env.get("VW_MIGRATION_DATABASE_URL"):
        problems.append("--migrate-on-start needs VW_MIGRATION_DATABASE_URL")
    return problems


class Render:
    def __init__(self, http: httpx.Client, api_key: str, sleep=time.sleep) -> None:
        self.http = http
        self.headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        self.sleep = sleep

    def request(self, method: str, path: str, *, ok=(200, 201, 202), **kw) -> Any:
        for attempt in range(5):
            resp = self.http.request(method, API_BASE + path, headers=self.headers, **kw)
            if resp.status_code == 429 and attempt < 4:
                self.sleep(min(2**attempt * 2, 30))
                continue
            break
        if resp.status_code not in ok:
            try:
                msg = resp.json().get("message", "")
            except ValueError:
                msg = resp.text[:300]
            raise RenderError(f"{method} {path.split('?')[0]} -> HTTP {resp.status_code} {msg}")
        return resp.json() if resp.content else None

    def paginate(self, path: str, params: dict[str, Any]) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while True:
            page = self.request("GET", path, params={**params, "limit": 100, **({"cursor": cursor} if cursor else {})})
            out.extend(page)
            if len(page) < 100:
                return out
            cursor = page[-1]["cursor"]


@dataclass
class ServiceState:
    name: str
    id: str = ""
    url: str = ""
    deploy_id: str = ""
    needs_runtime_deploy: bool = False  # env-only change to apply after the current build is live
    created: bool = False


@dataclass
class Deployer:
    render: Render | None
    owner_id: str
    region: str
    auto_deploy: bool
    dry_run: bool
    actions: list[str] = field(default_factory=list)

    def _act(self, line: str) -> None:
        line = ("[dry-run] " if self.dry_run else "") + line
        self.actions.append(line)
        print("  " + line)

    def existing(self) -> dict[str, dict]:
        if self.render is None:
            return {}
        rows = self.render.paginate(
            "/services",
            {"name": ",".join(SPECS), "ownerId": self.owner_id, "includePreviews": "false"},
        )
        found: dict[str, dict] = {}
        for row in rows:
            svc = row["service"]
            if svc["name"] in SPECS:
                if svc["name"] in found:
                    raise RenderError(f"two services are named {svc['name']}; delete one in the dashboard")
                found[svc["name"]] = svc
        return found

    def _show_env(self, values: dict[str, str]) -> str:
        return ", ".join(f"{k}={envfile.mask(k, v)}" for k, v in values.items())

    def ensure_service(self, spec: ServiceSpec, current: dict | None, env_values: dict[str, str]) -> ServiceState:
        assert_no_provisioning_keys(env_values)
        state = ServiceState(spec.name)
        trigger = "commit" if self.auto_deploy else "off"
        if current is None:
            body = {
                "type": "web_service",
                "name": spec.name,
                "ownerId": self.owner_id,
                "repo": REPO_URL,
                "branch": BRANCH,
                "autoDeployTrigger": trigger,
                "envVars": [{"key": k, "value": v} for k, v in env_values.items()],
                "serviceDetails": spec.details("free", self.region),
            }
            self._act(
                f"CREATE web service {spec.name} (free, {self.region}, {spec.dockerfile_path}, "
                f"context {spec.docker_context}, health {spec.health_check_path}, autoDeploy={trigger})"
            )
            self._act(f"  env: {self._show_env(env_values)}")
            if self.dry_run:
                state.url = f"https://{spec.name}.onrender.com"  # typical; real URL comes from the API
                return state
            created = self.render.request("POST", "/services", json=body)
            svc = created["service"]
            state.id, state.url = svc["id"], svc["serviceDetails"].get("url", "")
            state.deploy_id, state.created = created.get("deployId", ""), True
            return state

        state.id, state.url = current["id"], current.get("serviceDetails", {}).get("url", "")
        patch = self._service_patch(spec, current, trigger)
        rebuild = False
        if patch:
            self._act(
                f"UPDATE {spec.name}: {', '.join(sorted(patch))}"
                + (f" serviceDetails.{','.join(sorted(patch['serviceDetails']))}" if "serviceDetails" in patch else "")
            )
            if not self.dry_run:
                self.render.request("PATCH", f"/services/{state.id}", json=patch)
            rebuild = True
        current_env = self._current_env(state.id)
        if current_env != env_values:
            added = sorted(set(env_values) - set(current_env))
            removed = sorted(set(current_env) - set(env_values))
            changed = sorted(k for k in env_values if k in current_env and current_env[k] != env_values[k])
            self._act(f"SET env on {spec.name}: added={added} removed={removed} changed={changed}")
            if not self.dry_run:
                self.render.request(
                    "PUT",
                    f"/services/{state.id}/env-vars",
                    json=[{"key": k, "value": v} for k, v in env_values.items()],
                )
            # NEXT_PUBLIC_* are inlined at build time, so changing them needs a rebuild.
            if any(k.startswith("NEXT_PUBLIC_") for k in added + changed):
                rebuild = True
            elif not rebuild:
                state.needs_runtime_deploy = True
        else:
            self._act(f"OK env on {spec.name} ({len(env_values)} vars)")
        if rebuild:
            state.deploy_id = self.deploy(state, "build_and_deploy")
        return state

    def _service_patch(self, spec: ServiceSpec, current: dict, trigger: str) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        if current.get("repo", "").rstrip("/").removesuffix(".git") != REPO_URL:
            patch["repo"] = REPO_URL
        if current.get("branch") != BRANCH:
            patch["branch"] = BRANCH
        if current.get("autoDeployTrigger") not in (None, trigger):
            patch["autoDeployTrigger"] = trigger
        details = current.get("serviceDetails", {})
        sd: dict[str, Any] = {}
        if details.get("plan") != "free":
            sd["plan"] = "free"
        if details.get("healthCheckPath") != spec.health_check_path:
            sd["healthCheckPath"] = spec.health_check_path
        docker = details.get("envSpecificDetails", {})
        if (docker.get("dockerfilePath"), docker.get("dockerContext")) != (spec.dockerfile_path, spec.docker_context):
            sd["envSpecificDetails"] = {"dockerfilePath": spec.dockerfile_path, "dockerContext": spec.docker_context}
        if details.get("runtime") not in (None, "docker"):
            sd["runtime"] = "docker"
        if sd:
            patch["serviceDetails"] = sd
        return patch

    def _current_env(self, service_id: str) -> dict[str, str]:
        if self.render is None or not service_id:
            return {}
        rows = self.render.paginate(f"/services/{service_id}/env-vars", {})
        return dict(sorted((r["envVar"]["key"], r["envVar"]["value"]) for r in rows))

    def set_env(self, state: ServiceState, values: dict[str, str], summary: str) -> None:
        assert_no_provisioning_keys(values)
        self._act(f"SET env on {state.name}: {summary}")
        if not self.dry_run:
            self.render.request(
                "PUT", f"/services/{state.id}/env-vars", json=[{"key": k, "value": v} for k, v in values.items()]
            )
        state.needs_runtime_deploy = True

    def deploy(self, state: ServiceState, mode: str) -> str:
        self._act(f"DEPLOY {state.name} ({mode})")
        if self.dry_run:
            return ""
        body = {"deployMode": mode} if mode == "deploy_only" else {}
        dep = self.render.request("POST", f"/services/{state.id}/deploys", json=body)
        return (dep or {}).get("id", "")

    def wait(self, state: ServiceState, timeout: float, poll: float = 15) -> str:
        if self.dry_run or not state.deploy_id:
            return "skipped"
        deadline = time.monotonic() + timeout
        last = ""
        while True:
            dep = self.render.request("GET", f"/services/{state.id}/deploys/{state.deploy_id}")
            status = dep.get("status", "")
            if status != last:
                print(f"  {state.name}: deploy {state.deploy_id} {status}")
                last = status
            if status in TERMINAL_OK | TERMINAL_FAIL:
                return status
            if time.monotonic() > deadline:
                return f"timeout ({status})"
            self.render.sleep(poll)


def resolve_owner(render: Render | None, env: dict[str, str], explicit: str | None) -> str:
    if explicit or env.get("RENDER_OWNER_ID"):
        return explicit or env["RENDER_OWNER_ID"]
    if render is None:
        return "<owner-id>"
    owners = [row["owner"] for row in render.paginate("/owners", {})]
    if len(owners) == 1:
        return owners[0]["id"]
    listing = "; ".join(f"{o['id']} ({o['name']}, {o['type']})" for o in owners)
    raise RenderError(f"several Render workspaces; pass --owner-id. Found: {listing}")


def run(
    render: Render | None, env: dict[str, str], args: argparse.Namespace
) -> tuple[str, dict[str, ServiceState], bool]:
    """Returns (owner_id, states, all_ok)."""
    owner = resolve_owner(render, env, args.owner_id)
    print(f"Render workspace: {owner}   repo: {REPO_URL}@{BRANCH}   region: {args.region}   plan: free")
    d = Deployer(render, owner, args.region, auto_deploy=not args.no_auto_deploy, dry_run=args.dry_run)
    existing = d.existing()
    known_web = existing.get(WEB_SERVICE, {}).get("serviceDetails", {}).get("url", "") or env.get("RENDER_WEB_URL", "")
    mig = args.migrate_on_start

    api = d.ensure_service(SPECS[API_SERVICE], existing.get(API_SERVICE), api_env(env, known_web, migrate_on_start=mig))
    web = d.ensure_service(SPECS[WEB_SERVICE], existing.get(WEB_SERVICE), web_env(env, api.url, known_web))

    # The web URL is only known once vendorwake-web exists; feed it back as runtime-only variables.
    if web.url and web.url != known_web:
        d.set_env(api, api_env(env, web.url, migrate_on_start=mig), f"VW_CORS_ORIGINS={web.url}")
        d.set_env(web, web_env(env, api.url, web.url), f"APP_BASE_URL={web.url}")

    all_ok = True
    for s in (api, web):
        if args.no_wait:
            if s.needs_runtime_deploy and not s.deploy_id:
                s.deploy_id = d.deploy(s, "build_and_deploy")
            continue
        status = d.wait(s, args.timeout) if s.deploy_id else "no build"
        if s.needs_runtime_deploy and status in ("live", "no build", "skipped"):
            # deploy_only redeploys the last successful build with the new runtime env vars; a
            # service created in this run without a live build yet needs a full build instead.
            mode = "build_and_deploy" if (s.created and status != "live") else "deploy_only"
            s.deploy_id = d.deploy(s, mode)
            status = d.wait(s, args.timeout)
        if status not in ("live", "no build", "skipped"):
            all_ok = False
        print(f"  {s.name}: {status}")
    return owner, {API_SERVICE: api, WEB_SERVICE: web}, all_ok


def main(argv: list[str] | None = None, http: httpx.Client | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create/update the free Render services for Vendorwake.")
    ap.add_argument("--env-file", type=Path, default=envfile.default_env_path())
    ap.add_argument("--owner-id", help="Render workspace ID (default: RENDER_OWNER_ID or the only workspace)")
    ap.add_argument(
        "--region",
        default="virginia",
        choices=REGIONS,
        help="virginia is closest to Tiger Cloud's free region (us-east-1)",
    )
    ap.add_argument("--no-auto-deploy", action="store_true", help="autoDeployTrigger=off (no linked GitHub)")
    ap.add_argument(
        "--migrate-on-start",
        action="store_true",
        help="run alembic at container start (sends VW_MIGRATION_DATABASE_URL to Render)",
    )
    ap.add_argument("--no-wait", action="store_true", help="don't poll deploy status")
    ap.add_argument("--timeout", type=float, default=1800, help="seconds to wait per deploy")
    ap.add_argument("--dry-run", action="store_true", help="print the plan with secrets masked; no writes")
    ap.add_argument("--force", action="store_true", help="deploy even if validation finds problems")
    args = ap.parse_args(argv)

    env = envfile.load(args.env_file)
    problems = validate(env, migrate_on_start=args.migrate_on_start)
    for p in problems:
        print(f"{'warning' if args.dry_run or args.force else 'error'}: {p}", file=sys.stderr)
    if problems and not (args.dry_run or args.force):
        return 2
    have_key = envfile.is_set(env, "RENDER_API_KEY")
    if not have_key and not args.dry_run:
        print("error: RENDER_API_KEY is not set", file=sys.stderr)
        return 2

    own_http = http is None
    http = http or httpx.Client(timeout=60)
    try:
        render = Render(http, env["RENDER_API_KEY"]) if have_key else None
        if render is None:
            print("  (no RENDER_API_KEY: planning against an empty workspace)")
        owner, states, all_ok = run(render, env, args)
    except RenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if own_http:
            http.close()

    api, web = states[API_SERVICE], states[WEB_SERVICE]
    print(f"API: {api.url or '<pending>'}   health: {api.url}/api/health")
    print(f"Web: {web.url or '<pending>'}")
    if not args.dry_run:
        updates = {"RENDER_OWNER_ID": owner, "RENDER_API_URL": api.url, "RENDER_WEB_URL": web.url}
        envfile.write_updates(
            args.env_file, {k: v for k, v in updates.items() if v}, header="Written by scripts/deploy_render.py"
        )
    print("Next: python scripts/auth0_setup.py   (adds RENDER_WEB_URL to the Auth0 callback/logout URLs)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
