"""auth0_setup against an in-memory fake of the Management API v2 (httpx.MockTransport)."""

import json
import os
import stat
from urllib.parse import parse_qs, unquote

import httpx
import pytest

import auth0_setup
import envfile

DOMAIN = "vw-test.us.auth0.com"
AUD = "https://api.vendorwake.local"
MGMT_SECRET = "mgmt-secret-value-123"


class FakeAuth0:
    def __init__(self):
        self.resource_servers = {}
        self.clients = {}
        self.grants = {}
        self.writes = []  # (method, path, body)
        self.n = 0

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n:04d}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        is_json = request.headers.get("content-type", "").startswith("application/json")
        body = json.loads(request.content) if is_json and request.content else None
        if path == "/oauth/token":
            form = parse_qs(request.content.decode())
            assert form["audience"] == [f"https://{DOMAIN}/api/v2/"]
            if form["client_secret"] != [MGMT_SECRET]:
                return httpx.Response(401, json={"error": "access_denied"})
            return httpx.Response(200, json={"access_token": "mgmt-token", "token_type": "Bearer"})
        assert request.headers["authorization"] == "Bearer mgmt-token"
        assert path.startswith("/api/v2/")
        p = path[len("/api/v2") :]
        params = dict(request.url.params)
        if method in ("POST", "PATCH"):
            self.writes.append((method, p, body))

        if p.startswith("/resource-servers"):
            if method == "POST":
                rs = {"id": self._id("rs"), **body}
                self.resource_servers[body["identifier"]] = rs
                return httpx.Response(201, json=rs)
            ident = unquote(p.split("/", 2)[2])
            rs = self.resource_servers.get(ident)
            if rs is None:
                return httpx.Response(404, json={"error": "Not Found", "message": "Resource server not found"})
            if method == "PATCH":
                assert "identifier" not in body
                rs.update(body)
            return httpx.Response(200, json=rs)

        if p == "/clients" and method == "GET":
            fields = params["fields"].split(",")
            items = [{k: v for k, v in c.items() if k in fields} for c in self.clients.values()]
            start = int(params["page"]) * int(params["per_page"])
            page = items[start : start + int(params["per_page"])]
            return httpx.Response(200, json={"start": start, "limit": 100, "total": len(items), "clients": page})
        if p == "/clients" and method == "POST":
            cid = self._id("cid")
            c = {"client_id": cid, "client_secret": f"secret-of-{cid}", **body}
            self.clients[cid] = c
            return httpx.Response(201, json=c)
        if p.startswith("/clients/"):
            cid = p.split("/")[2]
            c = self.clients[cid]
            if method == "PATCH":
                c.update(body)
                return httpx.Response(200, json=c)
            return httpx.Response(200, json={"client_secret": c["client_secret"]})

        if p == "/client-grants" and method == "GET":
            items = [
                g
                for g in self.grants.values()
                if g["client_id"] == params.get("client_id") and g["audience"] == params.get("audience")
            ]
            return httpx.Response(200, json={"start": 0, "limit": 100, "total": len(items), "client_grants": items})
        if p == "/client-grants" and method == "POST":
            g = {"id": self._id("cgr"), **body}
            self.grants[g["id"]] = g
            return httpx.Response(201, json=g)
        if p.startswith("/client-grants/") and method == "PATCH":
            g = self.grants[p.split("/")[2]]
            g.update(body)
            return httpx.Response(200, json=g)
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def env_path(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        f"# Auth0\nAUTH0_DOMAIN={DOMAIN}\nVW_AUTH0_AUDIENCE={AUD}\nAUTH0_CLIENT_ID=\nAUTH0_CLIENT_SECRET=\n"
        f"AUTH0_SECRET=\nAUTH0_MGMT_CLIENT_ID=mgmt-id\nAUTH0_MGMT_CLIENT_SECRET={MGMT_SECRET}\nOTHER=keep-me\n"
    )
    os.chmod(p, 0o644)
    return p


def run(env_path, fake, *extra):
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    return auth0_setup.main(["--env-file", str(env_path), *extra], http=http)


def test_first_run_creates_everything_and_writes_env(env_path):
    fake = FakeAuth0()
    assert run(env_path, fake, "--web-url", "https://vendorwake-web.onrender.com/") == 0

    rs = fake.resource_servers[AUD]
    assert rs["signing_alg"] == "RS256" and rs["allow_offline_access"] is True
    assert rs["enforce_policies"] is True and rs["token_dialect"] == "access_token_authz"
    assert {s["value"] for s in rs["scopes"]} == {"docs:read", "repo:read", "patch:propose", "repo:write"}

    by_name = {c["name"]: c for c in fake.clients.values()}
    web = by_name["Vendorwake Web"]
    assert web["app_type"] == "regular_web"
    assert web["callbacks"] == [
        "http://localhost:3000/auth/callback",
        "https://vendorwake-web.onrender.com/auth/callback",
    ]
    assert web["allowed_logout_urls"] == ["http://localhost:3000", "https://vendorwake-web.onrender.com"]
    assert web["grant_types"] == ["authorization_code", "refresh_token"]
    for agent in auth0_setup.AGENT_SCOPES:
        c = by_name[f"Vendorwake Agent: {agent}"]
        assert c["app_type"] == "non_interactive" and c["grant_types"] == ["client_credentials"]
    grants = {fake.clients[g["client_id"]]["name"]: sorted(g["scope"]) for g in fake.grants.values()}
    assert grants == {
        "Vendorwake Agent: discovery": ["docs:read"],
        "Vendorwake Agent: repository": ["repo:read"],
        "Vendorwake Agent: impact": ["repo:read"],
        "Vendorwake Agent: verification": ["repo:read"],
        "Vendorwake Agent: migration": ["patch:propose", "repo:read"],
    }
    assert not any("repo:write" in scopes for scopes in grants.values())  # no agent may push

    env = envfile.load(env_path)
    assert env["AUTH0_CLIENT_ID"] == web["client_id"]
    assert env["AUTH0_CLIENT_SECRET"] == web["client_secret"]
    assert len(env["AUTH0_SECRET"]) == 64 and env["OTHER"] == "keep-me"
    assert env["VW_AUTH0_DOMAIN"] == DOMAIN and env["AUTH0_AUDIENCE"] == AUD
    creds = json.loads(env["VW_AUTH0_AGENT_CREDENTIALS"])
    assert set(creds) == set(auth0_setup.AGENT_SCOPES)
    assert creds["migration"]["client_secret"].startswith("secret-of-")
    assert " " not in env["VW_AUTH0_AGENT_CREDENTIALS"]  # compact JSON
    assert stat.S_IMODE(os.stat(env_path).st_mode) == 0o600


def test_second_run_is_idempotent_and_never_prints_secrets(env_path, capsys):
    fake = FakeAuth0()
    assert run(env_path, fake) == 0
    fake.writes.clear()
    before = env_path.read_text()
    assert run(env_path, fake) == 0
    assert fake.writes == []
    assert env_path.read_text() == before
    out = capsys.readouterr().out
    assert MGMT_SECRET not in out
    assert "secret-of-" not in out
    assert envfile.load(env_path)["AUTH0_SECRET"] not in out


def test_web_url_added_later_merges_callbacks(env_path):
    fake = FakeAuth0()
    run(env_path, fake)
    fake.writes.clear()
    run(env_path, fake, "--web-url", "https://vw.onrender.com")
    patches = [w for w in fake.writes if w[0] == "PATCH" and w[1].startswith("/clients/")]
    assert len(patches) == 1
    assert patches[0][2]["callbacks"] == [
        "http://localhost:3000/auth/callback",
        "https://vw.onrender.com/auth/callback",
    ]


def test_extra_scope_on_agent_grant_is_removed(env_path):
    fake = FakeAuth0()
    run(env_path, fake)
    g = next(g for g in fake.grants.values() if fake.clients[g["client_id"]]["name"].endswith("discovery"))
    g["scope"] = ["docs:read", "repo:write"]
    fake.writes.clear()
    run(env_path, fake)
    assert fake.writes == [("PATCH", f"/client-grants/{g['id']}", {"scope": ["docs:read"]})]


def test_existing_api_keeps_custom_scopes(env_path):
    fake = FakeAuth0()
    fake.resource_servers[AUD] = {
        "id": "rs1",
        "identifier": AUD,
        "name": "Mine",
        "scopes": [{"value": "custom:x"}],
        "signing_alg": "RS256",
    }
    run(env_path, fake)
    values = [s["value"] for s in fake.resource_servers[AUD]["scopes"]]
    assert values[0] == "custom:x" and "repo:write" in values


def test_dry_run_without_credentials_makes_no_calls(tmp_path, capsys):
    p = tmp_path / ".env"
    p.write_text(f"AUTH0_DOMAIN=\nVW_AUTH0_AUDIENCE={AUD}\n")

    def boom(request):
        raise AssertionError("no HTTP expected")

    http = httpx.Client(transport=httpx.MockTransport(boom))
    assert auth0_setup.main(["--env-file", str(p), "--dry-run"], http=http) == 0
    out = capsys.readouterr().out
    assert "[dry-run] CREATE API" in out and "CREATE client grant migration" in out
    assert p.read_text() == f"AUTH0_DOMAIN=\nVW_AUTH0_AUDIENCE={AUD}\n"


def test_dry_run_with_credentials_only_reads(env_path):
    fake = FakeAuth0()
    before = env_path.read_text()
    assert run(env_path, fake, "--dry-run") == 0
    assert fake.writes == [] and fake.clients == {}
    assert env_path.read_text() == before


def test_tenant_without_subject_type_authorization_falls_back(env_path, capsys):
    fake = FakeAuth0()
    orig = fake.handler

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/resource-servers"):
            if b"subject_type_authorization" in request.content:
                return httpx.Response(
                    400,
                    json={
                        "error": "Bad Request",
                        "message": "Payload validation error: 'Additional properties not allowed: subject_type_authorization'",
                    },
                )
        return orig(request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert auth0_setup.main(["--env-file", str(env_path)], http=http) == 0
    assert "subject_type_authorization" not in fake.resource_servers[AUD]
    assert "retried without it" in capsys.readouterr().out
