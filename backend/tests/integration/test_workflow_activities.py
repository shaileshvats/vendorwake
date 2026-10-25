"""Real activities against the real database (fake pipeline/sponsor ports): persistence and idempotency.

These are the guarantees that make a worker restart cheap: completed steps are reloaded from
``investigation_steps`` instead of being redone, so the rate-limited model is never asked twice.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from temporalio.exceptions import ApplicationError

from tests.integration.fakes import VENDOR, FakePorts
from tests.unit.conftest import make_settings
from vendorwake.db.session import tenant_session
from vendorwake.services import changes as changes_svc
from vendorwake.services import repositories as repo_svc
from vendorwake.workflows import types as t
from vendorwake.workflows.activities import (
    DiscoveryActivities,
    InvestigationActivities,
    RepositoryActivities,
)

pytestmark = pytest.mark.integration

SETTINGS = make_settings(entire_enabled=True, verification_allowed_repos="acme-org/billing")


def _discover(ports: FakePorts) -> uuid.UUID | None:
    disc = DiscoveryActivities(ports, SETTINGS)
    search = disc.search(t.DiscoverySearchInput(vendor=VENDOR, max_sources=5, workflow_id="wf-test"))
    assert search.urls == ["https://docs.acme.test/changelog"]
    snap = disc.fetch_snapshot(t.FetchInput(vendor=VENDOR, url=search.urls[0]))
    if not snap.changed and snap.already_extracted:
        return None
    result = disc.extract(t.ExtractInput(vendor=VENDOR, snapshot=snap))
    if result.change_id is not None:
        disc.embed(result.change_id)
    return result.change_id


def test_discovery_snapshots_dedupe_and_never_re_extract(
    worker_engine: Engine, owner_engine: Engine, clean_vendor: str
) -> None:
    ports = FakePorts()
    change_id = _discover(ports)
    assert change_id is not None
    assert _discover(ports) is None  # same content: no new snapshot, no second model call
    assert ports.calls["model:extract"] == 1 and ports.calls["embed"] == 1 and ports.calls["fetch"] == 2

    with owner_engine.connect() as conn:
        snaps = conn.execute(
            text(
                "SELECT extraction_status, change_id FROM source_snapshots WHERE url LIKE 'https://docs.acme.test/%'"
            )
        ).all()
        assert [tuple(r) for r in snaps] == [("change", change_id)]
        assert (
            conn.execute(
                text("SELECT count(*) FROM change_embeddings WHERE change_id = :c"), {"c": change_id}
            ).scalar_one()
            == 1
        )

    # A changed page creates a second snapshot and a second change; related-changes finds the first.
    ports.page = ports.page + "\n## 2.1.0\n- Removed `acme.Refund.old()`.\n"
    second = _discover(ports)
    assert second is not None and second != change_id and ports.calls["model:extract"] == 2
    with owner_engine.connect() as conn:
        from sqlalchemy.orm import Session

        related = changes_svc.related_changes(Session(bind=conn), second)
    assert [r.id for r in related][:1] == [change_id] and -1 <= related[0].similarity <= 1


def _seed_repo_and_change(user_id: uuid.UUID, ports: FakePorts) -> tuple[uuid.UUID, uuid.UUID]:
    change_id = _discover(ports)
    assert change_id is not None
    with tenant_session(user_id) as s:
        repo = repo_svc.create_or_get(s, user_id, owner="acme-org", name="billing", ref="main", subpath="")
        repo_id = repo.id
    repo_acts = RepositoryActivities(ports, SETTINGS)
    first = repo_acts.analyze(t.RepositoryAnalysisInput(repository_id=repo_id, user_id=user_id))
    again = repo_acts.analyze(t.RepositoryAnalysisInput(repository_id=repo_id, user_id=user_id))
    assert (first.reused, again.reused, ports.calls["analyze"]) == (False, True, 1)
    return repo_id, change_id


def _new_investigation(user_id: uuid.UUID, repo_id: uuid.UUID, change_id: uuid.UUID) -> uuid.UUID:
    from vendorwake.services import investigations as inv_svc

    with tenant_session(user_id) as s:
        return inv_svc.create(s, user_id, change_id, repo_id).id


def _run_steps(ports: FakePorts, user_id: uuid.UUID, inv_id: uuid.UUID) -> list[t.StepResult]:
    acts = InvestigationActivities(ports, SETTINGS)
    acts.start(
        t.InvestigationStartInput(
            investigation_id=inv_id,
            user_id=user_id,
            workflow_id=f"investigation-{inv_id}",
            run_id="r1",
            trace_id="ab" * 16,
        )
    )
    results = []
    for step in t.INVESTIGATION_STEPS:
        try:
            results.append(acts.step(t.StepInput(investigation_id=inv_id, user_id=user_id, step=step)))
        except ApplicationError:
            if step not in t.BEST_EFFORT_STEPS:
                raise
    return results


def test_investigation_steps_persist_and_are_not_repeated(
    worker_engine: Engine, user_id: uuid.UUID, clean_vendor: str
) -> None:
    ports = FakePorts()
    repo_id, change_id = _seed_repo_and_change(user_id, ports)
    inv_id = _new_investigation(user_id, repo_id, change_id)

    first = _run_steps(ports, user_id, inv_id)
    counted = {k: v for k, v in ports.calls.items() if k.startswith(("model:", "sandbox:"))}
    assert counted == {
        "model:extract": 1,
        "model:assess": 1,
        "model:propose": 1,
        "sandbox:upgrade": 1,
        "sandbox:patched": 1,
    }
    persist = first[-1]
    assert persist.step == t.STEP_PERSIST and len(persist.recommendation_ids) == 1

    # "Restarted worker": fresh activity objects and fresh ports; every completed step is reused.
    fresh = FakePorts()
    second = _run_steps(fresh, user_id, inv_id)
    assert all(r.reused for r in second)
    assert not any(k.startswith(("model:", "sandbox:")) for k in fresh.calls)
    assert second[-1].recommendation_ids == persist.recommendation_ids

    with tenant_session(user_id) as s:
        row = s.execute(
            text(
                "SELECT status, impact_status, report IS NOT NULL, backboard_thread_id, timeline "
                "FROM investigations WHERE id = :i"
            ),
            {"i": inv_id},
        ).one()
        evidence = s.execute(
            text("SELECT count(*) FROM impact_evidence WHERE investigation_id = :i"), {"i": inv_id}
        ).scalar_one()
        rec = s.execute(
            text(
                "SELECT verification_status, patch IS NOT NULL FROM recommendations "
                "WHERE investigation_id = :i"
            ),
            {"i": inv_id},
        ).one()
    status, impact, has_report, thread_id, timeline = row
    assert (status, impact, has_report) == ("waiting_approval", "confirmed", True)
    assert thread_id and evidence == 1 and tuple(rec) == ("passed", True)
    steps = {(e["step"], e["status"]) for e in timeline}
    assert (t.STEP_AGENT_SESSION, "failed") in steps  # Entire unavailable: recorded, not fatal
    assert ("permission", "allowed") in steps


def test_permission_denial_is_non_retryable_and_recorded(
    worker_engine: Engine, user_id: uuid.UUID, clean_vendor: str
) -> None:
    ports = FakePorts(denied={("migration", "propose_patch")})
    repo_id, change_id = _seed_repo_and_change(user_id, ports)
    inv_id = _new_investigation(user_id, repo_id, change_id)
    acts = InvestigationActivities(ports, SETTINGS)
    for step in (t.STEP_UPGRADE_CHECK, t.STEP_ASSESSMENT):
        acts.step(t.StepInput(investigation_id=inv_id, user_id=user_id, step=step))
    with pytest.raises(ApplicationError) as exc:
        acts.step(t.StepInput(investigation_id=inv_id, user_id=user_id, step=t.STEP_PATCH))
    assert exc.value.type == t.PERMISSION_DENIED and exc.value.non_retryable
    assert ports.calls["model:propose"] == 0
    with tenant_session(user_id) as s:
        timeline = s.execute(
            text("SELECT timeline FROM investigations WHERE id = :i"), {"i": inv_id}
        ).scalar_one()
    assert any(
        e["step"] == "permission" and e["status"] == "denied" and "propose_patch" in e["detail"]
        for e in timeline
    )


def test_approval_records_push_denial_and_takes_no_action(
    worker_engine: Engine, user_id: uuid.UUID, clean_vendor: str
) -> None:
    ports = FakePorts()
    repo_id, change_id = _seed_repo_and_change(user_id, ports)
    inv_id = _new_investigation(user_id, repo_id, change_id)
    acts = InvestigationActivities(ports, SETTINGS)
    acts.record_decision(
        t.DecisionInput(
            investigation_id=inv_id, user_id=user_id, recommendation_id=uuid.uuid4(), decision="approve"
        )
    )
    with tenant_session(user_id) as s:
        timeline = s.execute(
            text("SELECT timeline FROM investigations WHERE id = :i"), {"i": inv_id}
        ).scalar_one()
    push = [e for e in timeline if e["step"] == "push_branch"]
    assert len(push) == 1 and push[0]["status"] == "denied" and "no branch was pushed" in push[0]["detail"]
    assert ("migration", "push_branch") in ports.guard.checks
