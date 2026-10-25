"""REST API (docs/api-contract.md) against the real database, as the RLS app role.

Auth uses a locally signed RS256 token (FakeKeyResolver); Temporal is a recording fake gateway; pipeline and
sponsor adapters are FakePorts. Workflow side effects are produced by calling the real activities.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.integration.fakes import VENDOR, FakePorts
from tests.integration.test_workflow_activities import SETTINGS, _discover, _run_steps
from tests.unit.conftest import AUDIENCE, ISSUER, KID, FakeKeyResolver
from vendorwake.api.app import create_app
from vendorwake.core.security import get_key_resolver

pytestmark = pytest.mark.integration


class FakeGateway:
    def __init__(self) -> None:
        self.started: list[tuple[str, Any]] = []
        self.signals: list[tuple[str, uuid.UUID, str]] = []
        self.history_exists = True

    async def start_repository_analysis(self, repository_id: uuid.UUID, user_id: uuid.UUID) -> str:
        self.started.append(("repository", repository_id))
        return f"repository-analysis-{repository_id}"

    async def start_discovery(self, vendor: str) -> str:
        self.started.append(("discovery", vendor))
        return f"discovery-{vendor}"

    async def start_investigation(self, investigation_id: uuid.UUID, user_id: uuid.UUID) -> str:
        self.started.append(("investigation", investigation_id))
        return f"investigation-{investigation_id}"

    async def start_verification(self, recommendation_id: uuid.UUID, user_id: uuid.UUID) -> str:
        self.started.append(("verification", recommendation_id))
        return f"verification-{recommendation_id}"

    async def signal_decision(self, workflow_id: str, recommendation_id: uuid.UUID, decision: str) -> bool:
        self.signals.append((workflow_id, recommendation_id, decision))
        return self.history_exists

    async def describe_status(self, workflow_id: str, run_id: str | None) -> str | None:
        return "running" if self.history_exists else None

    async def ensure_schedule(self, vendor: str) -> str:
        return f"monitor-{vendor}"

    async def pause_schedule(self, vendor: str) -> str:
        return f"monitor-{vendor}"


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def api(key: rsa.RSAPrivateKey, migrated: None) -> Iterator[tuple[TestClient, FakeGateway, FakePorts]]:
    app = create_app(SETTINGS)
    app.dependency_overrides[get_key_resolver] = lambda: FakeKeyResolver(key)
    gateway, ports = FakeGateway(), FakePorts()
    app.state.workflow_gateway = gateway
    app.state.ports = ports
    with TestClient(app) as client:
        yield client, gateway, ports


@pytest.fixture()
def subjects(owner_engine: Engine) -> Iterator[dict[str, str]]:
    suffix = uuid.uuid4().hex[:8]
    subs = {"a": f"test|route-a-{suffix}", "b": f"test|route-b-{suffix}"}
    yield subs
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE auth_provider_id = ANY(:s)"), {"s": list(subs.values())})


def auth(key: rsa.RSAPrivateKey, sub: str) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {"sub": sub, "aud": AUDIENCE, "iss": ISSUER, "iat": now, "exp": now + 300},
        key,
        algorithm="RS256",
        headers={"kid": KID},
    )
    return {"Authorization": f"Bearer {token}"}


def test_requires_auth(api) -> None:
    client, _, _ = api
    assert client.get("/api/repositories").status_code == 401


def test_repositories_flow_and_isolation(api, key, subjects, worker_engine: Engine) -> None:
    client, gateway, _ = api
    a, b = auth(key, subjects["a"]), auth(key, subjects["b"])
    created = client.post("/api/repositories", json={"owner": "acme-org", "name": "billing"}, headers=a)
    assert created.status_code == 201, created.text
    repo = created.json()
    assert repo["analysis_status"] == "pending" and repo["ref"] == "main" and repo["subpath"] == ""
    assert repo["html_url"] == "https://github.com/acme-org/billing" and repo["integration_count"] == 0
    assert gateway.started == [("repository", uuid.UUID(repo["id"]))]
    # idempotent create, list, get
    assert (
        client.post("/api/repositories", json={"owner": "acme-org", "name": "billing"}, headers=a).json()[
            "id"
        ]
        == repo["id"]
    )
    assert [r["id"] for r in client.get("/api/repositories", headers=a).json()] == [repo["id"]]
    assert client.get(f"/api/repositories/{repo['id']}", headers=a).status_code == 200
    assert client.get(f"/api/repositories/{repo['id']}/dependencies", headers=a).status_code == 404
    # tenant B sees nothing
    assert client.get("/api/repositories", headers=b).json() == []
    assert client.get(f"/api/repositories/{repo['id']}", headers=b).status_code == 404
    # bad input
    bad = client.post(
        "/api/repositories", json={"owner": "acme", "name": "x", "subpath": "../etc"}, headers=a
    )
    assert bad.status_code == 422
    assert client.post(f"/api/repositories/{repo['id']}/analyze", headers=a).status_code == 202


def test_vendors_monitoring_and_discovery(api, key, subjects, clean_vendor: str) -> None:
    client, gateway, _ = api
    a = auth(key, subjects["a"])
    vendors = {v["name"]: v for v in client.get("/api/vendors", headers=a).json()}
    assert vendors[VENDOR]["monitoring_status"] == "off"
    assert vendors[VENDOR]["official_domains"] == ["docs.acme.test"]
    started = client.post("/api/monitoring/start", json={"vendor": VENDOR}, headers=a)
    assert started.json() == {
        "vendor": VENDOR,
        "monitoring_status": "active",
        "schedule_id": f"monitor-{VENDOR}",
    }
    assert {v["name"]: v for v in client.get("/api/vendors", headers=a).json()}[VENDOR][
        "monitoring_status"
    ] == "active"
    assert client.post("/api/monitoring/stop", json={"vendor": VENDOR}, headers=a).json() == {
        "vendor": VENDOR,
        "monitoring_status": "paused",
    }
    assert client.post("/api/monitoring/start", json={"vendor": "nope"}, headers=a).status_code == 404
    discovered = client.post(f"/api/vendors/{VENDOR}/discover", headers=a)
    assert discovered.status_code == 202 and discovered.json() == {"workflow_id": f"discovery-{VENDOR}"}


def test_changes_investigations_and_decisions(
    api, key, subjects, worker_engine: Engine, clean_vendor: str
) -> None:
    client, gateway, ports = api
    a, b = auth(key, subjects["a"]), auth(key, subjects["b"])
    first = _discover(ports)
    ports.page += "\n## 2.1.0\n- Removed `acme.Refund.old()`.\n"
    second = _discover(ports)

    changes = client.get(f"/api/changes?vendor={VENDOR}", headers=a).json()
    assert {c["id"] for c in changes} == {str(first), str(second)}
    detail = client.get(f"/api/changes/{second}", headers=a).json()
    assert detail["is_official"] and detail["change_count"] == 1 and detail["grounding"]["ok"] is True
    assert detail["source"]["fetched_via"] == "http" and len(detail["source"]["content_sha256"]) == 64
    assert detail["model_calls"][0]["model"] == "fake-gemma" and detail["affected_repositories"] == []
    related = client.get(f"/api/changes/{second}/related", headers=a).json()
    assert related[0]["id"] == str(first) and set(related[0]) == {
        "id",
        "vendor",
        "title",
        "similarity",
        "detected_at",
    }

    repo = client.post("/api/repositories", json={"owner": "acme-org", "name": "billing"}, headers=a).json()
    from vendorwake.workflows import types as t
    from vendorwake.workflows.activities import RepositoryActivities

    user_a = _user_id(subjects["a"])
    RepositoryActivities(ports, SETTINGS).analyze(
        t.RepositoryAnalysisInput(repository_id=repo["id"], user_id=user_a)
    )
    deps = client.get(f"/api/repositories/{repo['id']}/dependencies", headers=a).json()
    assert deps["integrations"][0]["vendor"] == VENDOR

    # B cannot investigate A's repository
    assert (
        client.post(
            f"/api/changes/{second}/investigate", json={"repository_id": repo["id"]}, headers=b
        ).status_code
        == 404
    )
    started = client.post(f"/api/changes/{second}/investigate", json={"repository_id": repo["id"]}, headers=a)
    assert started.status_code == 202
    inv_id = started.json()["investigation_id"]
    assert started.json()["workflow_id"] == f"investigation-{inv_id}"

    _run_steps(ports, user_a, uuid.UUID(inv_id))

    summaries = client.get("/api/investigations", headers=a).json()
    assert summaries[0]["id"] == inv_id and summaries[0]["status"] == "waiting_approval"
    assert client.get("/api/investigations", headers=b).json() == []
    inv = client.get(f"/api/investigations/{inv_id}", headers=a).json()
    assert inv["assessment"]["overall_status"] == "confirmed" and inv["workflow"]["status"] == "running"
    assert inv["workflow"]["ui_url"].endswith(f"/workflows/investigation-{inv_id}/r1/history")
    assert inv["trace"]["sentry_trace_id"] == "ab" * 16 and inv["agent_session"] is None
    assert [e for e in inv["timeline"] if e["step"] == t.STEP_PERSIST]
    gateway.history_exists = False  # e.g. the embedded dev server restarted: the DB still answers
    assert (
        client.get(f"/api/investigations/{inv_id}", headers=a).json()["workflow"]["status"]
        == "waiting_approval"
    )
    assert client.get(f"/api/investigations/{inv_id}", headers=b).status_code == 404

    evidence = client.get(f"/api/investigations/{inv_id}/evidence", headers=a).json()
    assert evidence[0]["kind"] == "code_reference" and evidence[0]["deterministic"] is True
    recs = client.get(f"/api/investigations/{inv_id}/recommendations", headers=a).json()
    assert recs[0]["approval_status"] == "pending" and recs[0]["verification_status"] == "passed"

    ask = client.post(f"/api/investigations/{inv_id}/ask", json={"question": "Which file?"}, headers=a).json()
    assert ask["answer"] == "answer to Which file?" and ask["retrieved_memories"][0]["id"] == "mem-1"
    assert ask["model"] == SETTINGS.backboard_model

    rec_id = recs[0]["id"]
    assert client.post(f"/api/recommendations/{rec_id}/approve", headers=b).status_code == 404
    approved = client.post(f"/api/recommendations/{rec_id}/approve", headers=a)
    assert approved.json()["approval_status"] == "approved"
    assert gateway.signals == [(f"investigation-{inv_id}", uuid.UUID(rec_id), "approve")]
    assert client.post(f"/api/recommendations/{rec_id}/approve", headers=a).status_code == 200  # idempotent
    assert client.post(f"/api/recommendations/{rec_id}/dismiss", headers=a).status_code == 409
    verify = client.post(f"/api/recommendations/{rec_id}/verify", headers=a)
    assert verify.status_code == 202 and verify.json()["verification_status"] == "pending"

    changed = client.get(f"/api/changes/{second}", headers=a).json()
    assert changed["affected_repositories"] == [
        {"repository_id": repo["id"], "investigation_id": inv_id, "impact_status": "confirmed"}
    ]
    assert client.get(f"/api/changes/{second}", headers=b).json()["affected_repositories"] == []


def test_security_agents(api, key, subjects) -> None:
    client, _, _ = api
    a = auth(key, subjects["a"])
    assert client.get("/api/security/agents", headers=a).json() == [
        {"agent": "migration", "client_id": "client-123", "scopes": ["patch:propose", "repo:read"]}
    ]
    denied = client.post(
        "/api/security/agents/check", json={"agent": "migration", "operation": "push_branch"}, headers=a
    )
    assert denied.json() == {
        "agent": "migration",
        "operation": "push_branch",
        "required_scope": "repo:write",
        "allowed": False,
        "reason": "insufficient_scope",
    }
    bad = client.post(
        "/api/security/agents/check", json={"agent": "migration", "operation": "rm_rf"}, headers=a
    )
    assert bad.status_code == 422


def _user_id(subject: str) -> uuid.UUID:
    from vendorwake.db.session import upsert_user

    return upsert_user(subject, None)
