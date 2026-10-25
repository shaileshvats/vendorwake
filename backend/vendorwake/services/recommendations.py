"""Recommendations: listing, human decisions and verification state. Tenant session only.

A decision is persisted here first and then signalled to the workflow; approving never applies a patch or
pushes anything.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vendorwake.core.exceptions import ConflictError, NotFoundError
from vendorwake.db.models import Recommendation as RecommendationRow
from vendorwake.domain.contracts import Recommendation, VerificationResult
from vendorwake.services.schemas import RecommendationOut

DECISION_STATUS = {"approve": "approved", "dismiss": "dismissed"}


def to_out(row: RecommendationRow) -> RecommendationOut:
    return RecommendationOut(
        id=row.id,
        recommendation_type=row.recommendation_type,  # type: ignore[arg-type]
        description=row.description,
        file_path=row.file_path,
        patch=row.patch,
        verification=VerificationResult.model_validate(row.verification) if row.verification else None,
        approval_status=row.approval_status,  # type: ignore[arg-type]
        verification_status=row.verification_status,
    )


def get(session: Session, recommendation_id: uuid.UUID) -> RecommendationRow:
    row = session.get(RecommendationRow, recommendation_id, populate_existing=True)
    if row is None:
        raise NotFoundError("Recommendation not found")
    return row


def list_for(session: Session, investigation_id: uuid.UUID) -> list[RecommendationOut]:
    from vendorwake.services import investigations as inv_svc

    inv_svc.get(session, investigation_id)
    rows = session.scalars(
        select(RecommendationRow)
        .where(RecommendationRow.investigation_id == investigation_id)
        .order_by(RecommendationRow.position)
    )
    return [to_out(r) for r in rows]


def decide(session: Session, recommendation_id: uuid.UUID, decision: str) -> RecommendationRow:
    """Record approve/dismiss. Repeating the same decision is a no-op; changing it is a 409."""
    row = get(session, recommendation_id)
    target = DECISION_STATUS[decision]
    if row.approval_status == target:
        return row
    if row.approval_status != "pending":
        raise ConflictError(f"Recommendation is already {row.approval_status}", code="already_decided")
    row.approval_status = target
    row.decided_at = datetime.now(UTC)
    session.flush()
    return row


def mark_verification_pending(session: Session, recommendation_id: uuid.UUID) -> RecommendationRow:
    row = get(session, recommendation_id)
    row.verification_status = "pending"
    session.flush()
    return row


# ---------------------------------------------------------------------------------------------- worker
def upsert_all(
    session: Session, user_id: uuid.UUID, investigation_id: uuid.UUID, recs: list[Recommendation]
) -> list[uuid.UUID]:
    """Upsert by (investigation_id, position); keeps human decisions made earlier."""
    existing = {
        r.position: r
        for r in session.scalars(
            select(RecommendationRow).where(RecommendationRow.investigation_id == investigation_id)
        )
    }
    ids: list[uuid.UUID] = []
    for position, rec in enumerate(recs):
        row = existing.get(position)
        if row is None:
            row = RecommendationRow(user_id=user_id, investigation_id=investigation_id, position=position)
            session.add(row)
        row.recommendation_type = rec.recommendation_type
        row.description = rec.description
        row.file_path = rec.file_path
        row.patch = rec.patch
        row.verification = rec.verification.model_dump(mode="json") if rec.verification else None
        row.verification_status = verification_status(rec)
        session.flush()
        ids.append(row.id)
    return ids


def pending_ids(session: Session, investigation_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        session.scalars(
            select(RecommendationRow.id)
            .where(
                RecommendationRow.investigation_id == investigation_id,
                RecommendationRow.approval_status == "pending",
            )
            .order_by(RecommendationRow.position)
        )
    )


def verification_status(rec: Recommendation) -> str:
    if rec.verification is not None:
        return rec.verification.status.value
    return "not_testable" if rec.patch is None else "requires_human_review"


def save_verification(
    session: Session, recommendation_id: uuid.UUID, result: VerificationResult | None
) -> str:
    row = get(session, recommendation_id)
    row.verification = result.model_dump(mode="json") if result else None
    row.verification_status = result.status.value if result else "requires_human_review"
    return row.verification_status
