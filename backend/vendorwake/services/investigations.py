"""Investigations: API reads/creates and the worker's step store, timeline and report persistence.

Everything runs on a tenant session (RLS). The database is the source of truth for the API: the detail
view is built from persisted columns, so it still works if the workflow history is gone (e.g. an embedded
Temporal dev server that restarted).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from vendorwake.core.config import Settings
from vendorwake.core.exceptions import NotFoundError
from vendorwake.db.models import (
    AgentSession,
    ImpactEvidence,
    Investigation,
    InvestigationStep,
    Repository,
    Vendor,
    VendorChange,
)
from vendorwake.domain.contracts import EvidenceItem, ImpactAssessment, Recommendation
from vendorwake.services import changes as changes_svc
from vendorwake.services import repositories as repo_svc
from vendorwake.services.schemas import (
    AgentSessionOut,
    ChangeRef,
    InvestigationDetail,
    InvestigationSummary,
    RepositoryRef,
    TimelineEntry,
    TraceInfo,
    WorkflowInfo,
    as_float,
)

ACTIVE_STATUSES = ("queued", "running", "waiting_approval")
DETAIL_MAX = 500


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------------------------- API
def create(
    session: Session, user_id: uuid.UUID, change_id: uuid.UUID, repository_id: uuid.UUID
) -> Investigation:
    changes_svc.get_change(session, change_id)
    repo_svc.get(session, repository_id)
    inv = Investigation(user_id=user_id, change_id=change_id, repository_id=repository_id, status="queued")
    session.add(inv)
    session.flush()
    inv.temporal_workflow_id = f"investigation-{inv.id}"
    append_timeline(session, inv.id, "queued", "completed", "investigation created")
    session.flush()
    return inv


def get(session: Session, investigation_id: uuid.UUID) -> Investigation:
    inv = session.get(Investigation, investigation_id, populate_existing=True)
    if inv is None:
        raise NotFoundError("Investigation not found")
    return inv


def _summary_rows(session: Session, investigation_id: uuid.UUID | None = None) -> list[Any]:
    stmt = (
        select(Investigation, VendorChange, Vendor.name, Repository)
        .join(VendorChange, VendorChange.id == Investigation.change_id)
        .join(Vendor, Vendor.id == VendorChange.vendor_id)
        .join(Repository, Repository.id == Investigation.repository_id)
        .order_by(Investigation.created_at.desc())
    )
    if investigation_id is not None:
        stmt = stmt.where(Investigation.id == investigation_id)
    return list(session.execute(stmt).all())


def _to_summary(
    inv: Investigation, change: VendorChange, vendor: str, repo: Repository
) -> InvestigationSummary:
    ref = repo_svc.repo_ref(repo)
    status = inv.status if inv.status != "cancelled" else "failed"
    return InvestigationSummary(
        id=inv.id,
        change=ChangeRef(id=change.id, vendor=vendor, title=change.title, severity=change.severity),
        repository=RepositoryRef(id=repo.id, owner=ref.owner, name=ref.name),
        status=status,  # type: ignore[arg-type]
        impact_status=inv.impact_status,
        confidence=as_float(inv.confidence),
        created_at=inv.created_at,
        completed_at=inv.completed_at,
    )


def list_summaries(session: Session) -> list[InvestigationSummary]:
    return [_to_summary(*row) for row in _summary_rows(session)]


def detail(
    session: Session, investigation_id: uuid.UUID, settings: Settings, workflow_status: str | None
) -> InvestigationDetail:
    rows = _summary_rows(session, investigation_id)
    if not rows:
        raise NotFoundError("Investigation not found")
    inv, change, vendor, repo = rows[0]
    summary = _to_summary(inv, change, vendor, repo)

    from vendorwake.workflows.gateway import workflow_ui_url

    trace = None
    if inv.sentry_trace_id:
        org = settings.sentry_org_url.rstrip("/")
        trace = TraceInfo(
            sentry_trace_id=inv.sentry_trace_id,
            url=f"{org}/explore/traces/trace/{inv.sentry_trace_id}/" if org else None,
        )
    entire = session.scalar(
        select(AgentSession).where(AgentSession.investigation_id == inv.id, AgentSession.provider == "entire")
    )
    agent_session = (
        AgentSessionOut(
            session_id=entire.external_session_id,
            checkpoint_id=entire.checkpoint_id,
            commit_sha=entire.commit_sha,
            transcript_excerpt=entire.transcript_excerpt,
            url=entire.url,
        )
        if entire is not None
        else None
    )
    return InvestigationDetail(
        **summary.model_dump(),
        summary=inv.summary,
        assessment=ImpactAssessment.model_validate(inv.report) if inv.report else None,
        workflow=WorkflowInfo(
            id=inv.temporal_workflow_id,
            run_id=inv.temporal_run_id,
            # Temporal's own status when the history exists; otherwise the persisted one.
            status=workflow_status or inv.status,
            ui_url=workflow_ui_url(settings, inv.temporal_workflow_id, inv.temporal_run_id)
            if inv.temporal_workflow_id
            else None,
        ),
        trace=trace,
        agent_session=agent_session,
        timeline=[TimelineEntry.model_validate(e) for e in inv.timeline or []],
    )


def evidence(session: Session, investigation_id: uuid.UUID) -> list[EvidenceItem]:
    get(session, investigation_id)
    rows = session.scalars(
        select(ImpactEvidence)
        .where(ImpactEvidence.investigation_id == investigation_id)
        .order_by(ImpactEvidence.position)
    )
    return [
        EvidenceItem(
            kind=r.evidence_type,  # type: ignore[arg-type]
            deterministic=r.deterministic,
            description=r.explanation or "",
            file_path=r.file_path,
            line=r.line,
            source_url=r.source_url,
            content_sha256=r.content_reference,
            quote=r.quote,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------------------------- worker
def append_timeline(
    session: Session, investigation_id: uuid.UUID, step: str, status: str, detail: str = ""
) -> None:
    entry = {"at": _now().isoformat(), "step": step, "status": status, "detail": (detail or "")[:DETAIL_MAX]}
    session.execute(
        text(
            "UPDATE investigations SET timeline = timeline || jsonb_build_array(CAST(:e AS jsonb)), "
            "updated_at = now() WHERE id = :i"
        ),
        {"e": json.dumps(entry), "i": investigation_id},
    )


def get_step(session: Session, investigation_id: uuid.UUID, step: str) -> InvestigationStep | None:
    return session.scalar(
        select(InvestigationStep).where(
            InvestigationStep.investigation_id == investigation_id,
            InvestigationStep.step == step,
            InvestigationStep.status == "completed",
        )
    )


def put_step(
    session: Session, user_id: uuid.UUID, investigation_id: uuid.UUID, step: str, output: Any
) -> None:
    session.execute(
        text(
            "INSERT INTO investigation_steps (user_id, investigation_id, step, status, output, completed_at) "
            "VALUES (:u, :i, :s, 'completed', CAST(:o AS jsonb), now()) "
            "ON CONFLICT (investigation_id, step) DO UPDATE SET status = 'completed', "
            "output = EXCLUDED.output, completed_at = EXCLUDED.completed_at, updated_at = now()"
        ),
        {"u": user_id, "i": investigation_id, "s": step, "o": json.dumps(output)},
    )


def start(
    session: Session, investigation_id: uuid.UUID, *, workflow_id: str, run_id: str, trace_id: str
) -> Investigation:
    inv = get(session, investigation_id)
    inv.temporal_workflow_id = workflow_id
    inv.temporal_run_id = run_id
    inv.sentry_trace_id = inv.sentry_trace_id or trace_id
    if inv.status in ("queued", "failed", "running"):
        inv.status = "running"
        inv.error = None
    inv.started_at = inv.started_at or _now()
    append_timeline(session, inv.id, "workflow", "running", f"run {run_id}")
    return inv


def persist_report(
    session: Session,
    user_id: uuid.UUID,
    investigation_id: uuid.UUID,
    assessment: ImpactAssessment,
    recommendations: list[Recommendation],
) -> tuple[list[uuid.UUID], str]:
    """Write report, evidence and recommendations (idempotent). Returns (recommendation ids, status)."""
    from vendorwake.services import recommendations as rec_svc

    inv = get(session, investigation_id)
    inv.report = assessment.model_copy(update={"recommendations": recommendations}).model_dump(mode="json")
    inv.impact_status = assessment.overall_status.value
    inv.confidence = assessment.confidence
    inv.summary = assessment.summary

    session.execute(delete(ImpactEvidence).where(ImpactEvidence.investigation_id == investigation_id))
    for position, item in enumerate(assessment.evidence):
        session.add(
            ImpactEvidence(
                user_id=user_id,
                investigation_id=investigation_id,
                evidence_type=item.kind.value,
                deterministic=item.deterministic,
                explanation=item.description,
                file_path=item.file_path,
                line=item.line,
                source_url=item.source_url,
                content_reference=item.content_sha256,
                quote=item.quote,
                position=position,
            )
        )
    rec_svc.upsert_all(session, user_id, investigation_id, recommendations)
    pending = rec_svc.pending_ids(session, investigation_id)
    if pending:
        inv.status = "waiting_approval"
    session.flush()
    return pending, inv.status


def save_agent_session(
    session: Session, user_id: uuid.UUID, investigation_id: uuid.UUID, provider: str, info: dict[str, Any]
) -> None:
    row = session.scalar(
        select(AgentSession).where(
            AgentSession.investigation_id == investigation_id, AgentSession.provider == provider
        )
    )
    if row is None:
        row = AgentSession(user_id=user_id, investigation_id=investigation_id, provider=provider)
        session.add(row)
    row.external_session_id = _s(info.get("session_id"), 255)
    row.checkpoint_id = _s(info.get("checkpoint_id"), 64)
    row.commit_sha = _s(info.get("commit_sha"), 64)
    row.model = _s(info.get("model"), 255)
    row.transcript_excerpt = _s(info.get("transcript_excerpt"), 4000)
    row.url = _s(info.get("url"), 2048)
    row.ended_at = _now()
    row.session_metadata = {k: v for k, v in info.items() if isinstance(v, str | int | float | bool | None)}


def _s(value: Any, limit: int) -> str | None:
    return str(value)[:limit] if value is not None else None


def finish(session: Session, investigation_id: uuid.UUID, outcome: str) -> None:
    inv = get(session, investigation_id)
    inv.status = "completed"
    inv.completed_at = inv.completed_at or _now()
    detail = {
        "completed": "all recommendations decided",
        "approval_timeout": "approval window expired; undecided recommendations stay pending",
        "no_recommendations": "nothing to approve",
    }.get(outcome, outcome)
    append_timeline(session, inv.id, "workflow", "completed", detail)


def fail(session: Session, investigation_id: uuid.UUID, step: str, error_type: str) -> None:
    inv = get(session, investigation_id)
    inv.status = "failed"
    inv.error = f"{step}: {error_type}"[:255]
    inv.completed_at = _now()
    append_timeline(session, inv.id, step, "failed", error_type)
