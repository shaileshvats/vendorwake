"""deploy_render against an in-memory fake of the Render API v1 (httpx.MockTransport)."""

import json

import httpx
import pytest

import deploy_render as dr
import envfile

SECRET_VALUES = ["sk-or-v1-abc", "app-db-pw", "web-secret", "agent-secret", "rnd_key", "mgmt-secret", "owner-pw"]

ENV = f"""VW_ENV=local
VW_CORS_ORIGINS=http://localhost:3000
VW_MIGRATION_DATABASE_URL=postgresql+psycopg://tsdbadmin:owner-pw@x.tsdb.cloud.timescale.com:3333/tsdb?sslmode=require
VW_DATABASE_URL=postgresql+psycopg://vendorwake_app:app-db-pw@x.tsdb.cloud.timescale.com:3333/tsdb?sslmode=require
VW_APP_DB_PASSWORD=app-db-pw
VW_WORKER_DB_PASSWORD=worker-pw
VW_AUTH0_DOMAIN=t.us.auth0.com
VW_AUTH0_AUDIENCE=https://api.vendorwake.local
VW_AUTH0_AGENT_CREDENTIALS={{"discovery":{{"client_id":"a","client_secret":"agent-secret"}}}}
AUTH0_DOMAIN=t.us.auth0.com
AUTH0_CLIENT_ID=webid
AUTH0_CLIENT_SECRET=web-secret
AUTH0_SECRET={"a" * 64}
AUTH0_AUDIENCE=https://api.vendorwake.local
APP_BASE_URL=http://localhost:3000
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_SENTRY_DSN=
API_BASE_URL=
VW_TEMPORAL_ADDRESS=localhost:7233
VW_TEMPORAL_EMBEDDED=true
VW_LLM_API_KEY=sk-or-v1-abc
VW_SENTRY_DSN=
POSTGRES_PASSWORD=change-me-owner
AUTH0_MGMT_CLIENT_ID=m
AUTH0_MGMT_CLIENT_SECRET=mgmt-secret
RENDER_API_KEY=rnd_key
"""


class FakeRender:
    def __init__(self):
        self.services = {}
        self.env = {}
        self.deploys = {}
        self.calls = []
        self.n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer rnd_key"
        p = request.url.path.removeprefix("/v1")
        m = request.method
        body = json.loads(request.content) if request.content else None
        self.calls.append((m, p, body))
        if p == "/owners":
            return httpx.Response(
                200, json=[{"owner": {"id": "tea-1", "name": "me", "email": "e", "type": "user"}, "cursor": "c"}]
            )
        if p == "/services" and m == "GET":
            names = request.url.params["name"].split(",")
            assert request.url.params["ownerId"] == "tea-1"
            return httpx.Response(
                200, json=[{"service": s, "cursor": s["id"]} for s in self.services.values() if s["name"] in names]
            )
        if p == "/services" and m == "POST":
            self.n += 1
            sid = f"srv-{self.n}"
            sd = dict(body["serviceDetails"])
            sd["url"] = f"https://{body['name']}-x{self.n}.onrender.com"
            svc = {
                "id": sid,
                "name": body["name"],
                "ownerId": body["ownerId"],
                "repo": body["repo"],
                "branch": body["branch"],
                "autoDeployTrigger": body["autoDeployTrigger"],
                "serviceDetails": sd,
            }
            self.services[sid] = svc
            self.env[sid] = {e["key"]: e["value"] for e in body["envVars"]}
            dep = self._deploy(sid)
            return httpx.Response(201, json={"service": svc, "deployId": dep})
        parts = p.split("/")  # ['', 'services', id, ...]
        sid = parts[2]
        if len(parts) == 3 and m == "GET":
            return httpx.Response(200, json=self.services[sid])
        if len(parts) == 3 and m == "PATCH":
            svc = self.services[sid]
            body = dict(body)
            sd = body.pop("serviceDetails", {})
            svc.update(body)
            svc["serviceDetails"].update(sd)
            return httpx.Response(200, json=svc)
        if parts[3] == "env-vars" and m == "GET":
            return httpx.Response(
                200, json=[{"envVar": {"key": k, "value": v}, "cursor": k} for k, v in self.env[sid].items()]
            )
        if parts[3] == "env-vars" and m == "PUT":
            self.env[sid] = {e["key"]: e["value"] for e in body}
            return httpx.Response(200, json=[{"envVar": e, "cursor": e["key"]} for e in body])
        if parts[3] == "deploys" and m == "POST":
            return httpx.Response(201, json={"id": self._deploy(sid, body.get("deployMode")), "status": "created"})
        if parts[3] == "deploys" and m == "GET":
            return httpx.Response(200, json={"id": parts[4], "status": "live"})
        raise AssertionError(f"unexpected {m} {p}")

    def _deploy(self, sid, mode=None):
        self.n += 1
        did = f"dep-{self.n}"
        self.deploys[did] = (sid, mode)
        return did


@pytest.fixture
def env_path(tmp_path):
    p = tmp_path / ".env"
    p.write_text(ENV)
    return p


def run(env_path, fake, *extra):
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    dr.time.sleep = lambda s: None
    return dr.main(["--env-file", str(env_path), "--timeout", "5", *extra], http=http)


def test_env_mapping_never_sends_provisioning_keys():
    env = envfile.parse(ENV)
    api = dr.api_env(env, "https://web.onrender.com")
    web = dr.web_env(env, "https://api.onrender.com", "https://web.onrender.com")
    for values in (api, web):
        assert not [k for k in values if k.startswith(("RENDER_", "AUTH0_MGMT_", "POSTGRES_"))]
        assert "VW_MIGRATION_DATABASE_URL" not in values and "VW_APP_DB_PASSWORD" not in values
    assert (
        api["PORT"] == "8000" and api["VW_ENV"] == "production" and api["VW_CORS_ORIGINS"] == "https://web.onrender.com"
    )
    assert "VW_SENTRY_DSN" not in api  # empty values skipped
    assert "AUTH0_CLIENT_SECRET" not in api
    assert web == {
        "APP_BASE_URL": "https://web.onrender.com",
        "AUTH0_AUDIENCE": "https://api.vendorwake.local",
        "AUTH0_CLIENT_ID": "webid",
        "AUTH0_CLIENT_SECRET": "web-secret",
        "AUTH0_DOMAIN": "t.us.auth0.com",
        "AUTH0_SECRET": "a" * 64,
        "NEXT_PUBLIC_API_BASE_URL": "https://api.onrender.com",
        "PORT": "3000",
    }
    assert "VW_MIGRATION_DATABASE_URL" in dr.api_env(env, "", migrate_on_start=True)


def test_validate_flags_local_database_and_temporal():
    env = envfile.parse(ENV)
    assert dr.validate(env, migrate_on_start=False) == []
    env["VW_DATABASE_URL"] = "postgresql+psycopg://vendorwake_app:x@localhost:5432/vendorwake"
    env["VW_TEMPORAL_EMBEDDED"] = ""
    problems = dr.validate(env, migrate_on_start=False)
    assert any("VW_DATABASE_URL" in p for p in problems) and any("Temporal" in p for p in problems)


def test_first_run_creates_two_free_services_and_feeds_back_urls(env_path, capsys):
    fake = FakeRender()
    assert run(env_path, fake) == 0
    creates = [b for m, p, b in fake.calls if m == "POST" and p == "/services"]
    assert [c["name"] for c in creates] == ["vendorwake-api", "vendorwake-web"]
    api_body, web_body = creates
    for b in creates:
        assert b["type"] == "web_service" and b["serviceDetails"]["plan"] == "free"
        assert b["repo"] == "https://github.com/shaileshvats/vendorwake" and b["branch"] == "main"
        assert b["autoDeployTrigger"] == "commit" and b["serviceDetails"]["runtime"] == "docker"
    assert api_body["serviceDetails"]["envSpecificDetails"] == {
        "dockerfilePath": "./backend/Dockerfile.worker",
        "dockerContext": ".",
    }
    assert api_body["serviceDetails"]["healthCheckPath"] == "/api/health"
    assert web_body["serviceDetails"]["envSpecificDetails"] == {
        "dockerfilePath": "./frontend/Dockerfile",
        "dockerContext": "./frontend",
    }
    api_id, web_id = "srv-1", "srv-3"
    api_url = fake.services[api_id]["serviceDetails"]["url"]
    web_url = fake.services[web_id]["serviceDetails"]["url"]
    assert {e["key"]: e["value"] for e in web_body["envVars"]}["NEXT_PUBLIC_API_BASE_URL"] == api_url
    assert fake.env[api_id]["VW_CORS_ORIGINS"] == web_url
    assert fake.env[web_id]["APP_BASE_URL"] == web_url
    # runtime-only follow-up deploys reuse the build
    assert sorted(mode for sid, mode in fake.deploys.values() if mode) == ["deploy_only", "deploy_only"]
    env = envfile.load(env_path)
    assert env["RENDER_WEB_URL"] == web_url and env["RENDER_API_URL"] == api_url and env["RENDER_OWNER_ID"] == "tea-1"
    out = capsys.readouterr().out
    for s in SECRET_VALUES:
        assert s not in out


def test_second_run_changes_nothing(env_path):
    fake = FakeRender()
    run(env_path, fake)
    fake.calls.clear()
    assert run(env_path, fake) == 0
    assert [c for c in fake.calls if c[0] in ("POST", "PATCH", "PUT")] == []


def test_changed_api_url_rebuilds_web(env_path):
    fake = FakeRender()
    run(env_path, fake)
    fake.services["srv-1"]["serviceDetails"]["url"] = "https://renamed.onrender.com"
    fake.calls.clear()
    run(env_path, fake)
    deploy_posts = [(p, b) for m, p, b in fake.calls if m == "POST" and p.endswith("/deploys")]
    assert deploy_posts == [("/services/srv-3/deploys", {})]  # build_and_deploy: NEXT_PUBLIC_* is build time


def test_drifted_plan_is_patched(env_path):
    fake = FakeRender()
    run(env_path, fake)
    fake.services["srv-1"]["serviceDetails"]["plan"] = "starter"
    fake.calls.clear()
    run(env_path, fake)
    patches = [b for m, p, b in fake.calls if m == "PATCH"]
    assert patches == [{"serviceDetails": {"plan": "free"}}]


def test_dry_run_masks_secrets_and_makes_no_calls(tmp_path, capsys):
    p = tmp_path / ".env"
    p.write_text(ENV.replace("RENDER_API_KEY=rnd_key", "RENDER_API_KEY="))

    def boom(request):
        raise AssertionError("no HTTP expected")

    assert dr.main(["--env-file", str(p), "--dry-run"], http=httpx.Client(transport=httpx.MockTransport(boom))) == 0
    out = capsys.readouterr().out
    assert "[dry-run] CREATE web service vendorwake-api (free" in out
    assert "AUTH0_CLIENT_SECRET=<secret, 10 chars>" in out
    for s in SECRET_VALUES:
        assert s not in out
