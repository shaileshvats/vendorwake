"""Vendor changes, source snapshots and embeddings.

Reads run on the app role (global tables are readable by every tenant; investigations are RLS-scoped).
Writes (``*_w`` helpers taking a worker session) run on the ``vendorwake_worker`` role.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from vendorwake.core.exceptions import NotFoundError
from vendorwake.db.models import ChangeEmbedding, Investigation, SourceSnapshot, Vendor, VendorChange
from vendorwake.domain.contracts import (
    ChangeExtraction,
    DiscoveryCandidate,
    GroundingReport,
    ModelCallInfo,
    SourceDocument,
)
from vendorwake.services.schemas import (
    AffectedRepository,
    ChangeDetail,
    ChangeSource,
    ChangeSummary,
    GroundingOut,
    RelatedChange,
)

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "unknown": 5}
RELATED_LIMIT = 5


# ---------------------------------------------------------------------------------------------- reads
def _summary(change: VendorChange, vendor: str) -> ChangeSummary:
    return ChangeSummary(
        id=change.id,
        vendor=vendor,
        title=change.title,
        change_type=change.change_type,
        severity=change.severity,
        published_at=change.published_at,
        detected_at=change.detected_at,
        source_url=change.source_url,
        is_official=change.is_official,
        change_count=change.change_count,
    )


def list_changes(session: Session, vendor: str | None = None, limit: int = 200) -> list[ChangeSummary]:
    stmt = select(VendorChange, Vendor.name).join(Vendor, Vendor.id == VendorChange.vendor_id)
    if vendor:
        stmt = stmt.where(Vendor.name == vendor.strip().lower())
    stmt = stmt.order_by(VendorChange.detected_at.desc()).limit(limit)
    return [_summary(c, name) for c, name in session.execute(stmt)]


def get_change(session: Session, change_id: uuid.UUID) -> tuple[VendorChange, str]:
    row = session.execute(
        select(VendorChange, Vendor.name)
        .join(Vendor, Vendor.id == VendorChange.vendor_id)
        .where(VendorChange.id == change_id)
    ).first()
    if row is None:
        raise NotFoundError("Change not found")
    return row[0], row[1]


def change_detail(session: Session, change_id: uuid.UUID) -> ChangeDetail:
    change, vendor = get_change(session, change_id)
    grounding = None
    if change.grounding is not None:
        report = GroundingReport.model_validate(change.grounding)
        grounding = GroundingOut(
            ungrounded_quotes=report.ungrounded_quotes,
            ungrounded_symbols=report.ungrounded_symbols,
            ok=report.ok,
        )
    affected = [
        AffectedRepository(
            repository_id=inv.repository_id, investigation_id=inv.id, impact_status=inv.impact_status
        )
        for inv in session.scalars(  # RLS: only the caller's investigations
            select(Investigation)
            .where(Investigation.change_id == change_id)
            .order_by(Investigation.created_at)
        )
    ]
    return ChangeDetail(
        **_summary(change, vendor).model_dump(),
        extraction=ChangeExtraction.model_validate(change.extraction) if change.extraction else None,
        grounding=grounding,
        source=ChangeSource(
            url=change.source_url,
            retrieved_at=change.retrieved_at,
            fetched_via=change.fetched_via,
            content_sha256=change.content_sha256,
            browser_session_id=change.browser_session_id,
        ),
        discovery=[DiscoveryCandidate.model_validate(c) for c in change.discovery or []],
        model_calls=[ModelCallInfo.model_validate(c) for c in change.model_calls or []],
        affected_repositories=affected,
    )


def related_changes(
    session: Session, change_id: uuid.UUID, limit: int = RELATED_LIMIT
) -> list[RelatedChange]:
    """Nearest past changes by cosine distance (pgvector ``<=>``, served by the HNSW index)."""
    get_change(session, change_id)  # 404 for an unknown change
    rows = session.execute(
        text(
            "SELECT c.id, v.name AS vendor, c.title, c.detected_at, "
            "1 - (e.embedding <=> t.embedding) AS similarity "
            "FROM change_embeddings t "
            "JOIN change_embeddings e ON e.change_id <> t.change_id "
            "JOIN vendor_changes c ON c.id = e.change_id "
            "JOIN vendors v ON v.id = c.vendor_id "
            "WHERE t.change_id = :id "
            "ORDER BY e.embedding <=> t.embedding LIMIT :limit"
        ),
        {"id": change_id, "limit": limit},
    ).all()
    return [
        RelatedChange(
            id=r.id,
            vendor=r.vendor,
            title=r.title,
            similarity=round(float(r.similarity), 6),
            detected_at=r.detected_at,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------------------------- worker writes
def latest_snapshot_w(ws: Session, url: str) -> SourceSnapshot | None:
    return ws.scalar(
        select(SourceSnapshot)
        .where(SourceSnapshot.url == url)
        .order_by(SourceSnapshot.retrieved_at.desc())
        .limit(1)
    )


def insert_snapshot_w(
    ws: Session, vendor_id: uuid.UUID, source_id: uuid.UUID | None, doc: SourceDocument
) -> SourceSnapshot:
    snapshot = SourceSnapshot(
        id=uuid.uuid4(),
        retrieved_at=doc.retrieved_at,
        vendor_id=vendor_id,
        source_id=source_id,
        url=doc.url,
        content_sha256=doc.content_sha256,
        content=doc.text,
        title=doc.title,
        fetched_via=doc.fetched_via,
        browser_session_id=doc.browser_session_id,
        screenshot_ref=doc.screenshot_ref,
    )
    ws.add(snapshot)
    ws.flush()
    return snapshot


def load_snapshot_w(ws: Session, snapshot_id: uuid.UUID, retrieved_at: datetime) -> SourceSnapshot:
    snapshot = ws.get(SourceSnapshot, (snapshot_id, retrieved_at))
    if snapshot is None:
        raise NotFoundError("snapshot not found")
    return snapshot


def snapshot_document(snapshot: SourceSnapshot) -> SourceDocument:
    return SourceDocument(
        url=snapshot.url,
        retrieved_at=snapshot.retrieved_at,
        fetched_via=snapshot.fetched_via,  # type: ignore[arg-type]
        content_sha256=snapshot.content_sha256,
        text=snapshot.content,
        title=snapshot.title,
        browser_session_id=snapshot.browser_session_id,
        screenshot_ref=snapshot.screenshot_ref,
    )


def change_by_hash_w(ws: Session, vendor_id: uuid.UUID, sha: str) -> uuid.UUID | None:
    return ws.scalar(
        select(VendorChange.id).where(VendorChange.vendor_id == vendor_id, VendorChange.content_sha256 == sha)
    )


def _published_at(release_date: str | None) -> datetime | None:
    if not release_date:
        return None
    try:
        parsed = datetime.fromisoformat(release_date)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def change_title(extraction: ChangeExtraction) -> str:
    if extraction.package and extraction.version:
        return f"{extraction.package} {extraction.version}"[:200]
    return (extraction.changes[0].title if extraction.changes else f"{extraction.vendor} update")[:200]


def save_change_w(
    ws: Session,
    *,
    vendor_id: uuid.UUID,
    source_id: uuid.UUID | None,
    snapshot: SourceSnapshot,
    extraction: ChangeExtraction,
    grounding: GroundingReport,
    model_calls: list[ModelCallInfo],
    candidates: list[DiscoveryCandidate],
    is_official: bool,
) -> tuple[uuid.UUID, bool]:
    """Insert the change for (vendor, content hash); returns (id, created). Idempotent."""
    top = min(extraction.changes, key=lambda c: SEVERITY_RANK.get(c.severity.value, 9))
    params: dict[str, Any] = {
        "vendor_id": vendor_id,
        "source_id": source_id,
        "title": change_title(extraction),
        "change_type": top.change_type.value,
        "severity": top.severity.value,
        "published_at": _published_at(extraction.release_date),
        "source_url": snapshot.url,
        "package": extraction.package[:255] if extraction.package else None,
        "version": extraction.version[:128] if extraction.version else None,
        "sha": snapshot.content_sha256,
        "snapshot_id": snapshot.id,
        "retrieved_at": snapshot.retrieved_at,
        "fetched_via": snapshot.fetched_via,
        "browser_session_id": snapshot.browser_session_id,
        "is_official": is_official,
        "change_count": len(extraction.changes),
        "rhr": extraction.requires_human_review or not grounding.ok,
        "extraction": extraction.model_dump_json(),
        "grounding": grounding.model_dump_json(),
        "grounding_ok": grounding.ok,
        "model_calls": json.dumps([c.model_dump(mode="json") for c in model_calls]),
        "discovery": json.dumps([c.model_dump(mode="json") for c in candidates]),
        "ref": f"snapshot:{snapshot.id}",
    }
    created_id = ws.execute(
        text(
            "INSERT INTO vendor_changes (vendor_id, source_id, title, change_type, severity, published_at, "
            "source_url, package, version, content_sha256, snapshot_id, retrieved_at, fetched_via, "
            "browser_session_id, is_official, change_count, requires_human_review, extraction, grounding, "
            "grounding_ok, model_calls, discovery, current_content_reference) "
            "VALUES (:vendor_id, :source_id, :title, :change_type, :severity, :published_at, :source_url, "
            ":package, :version, :sha, :snapshot_id, :retrieved_at, :fetched_via, :browser_session_id, "
            ":is_official, :change_count, :rhr, CAST(:extraction AS jsonb), CAST(:grounding AS jsonb), "
            ":grounding_ok, CAST(:model_calls AS jsonb), CAST(:discovery AS jsonb), :ref) "
            "ON CONFLICT (vendor_id, content_sha256) DO NOTHING RETURNING id"
        ),
        params,
    ).scalar_one_or_none()
    if created_id is not None:
        return created_id, True
    existing = change_by_hash_w(ws, vendor_id, snapshot.content_sha256)
    assert existing is not None
    return existing, False


def embedding_text(change: VendorChange) -> str:
    parts = [change.title]
    extraction = change.extraction or {}
    for item in extraction.get("changes") or []:
        parts.append(
            f"{item.get('change_type')}: {item.get('title')}. {' '.join(item.get('affected_symbols') or [])}"
        )
    return "\n".join(p for p in parts if p)


def save_embedding_w(ws: Session, change_id: uuid.UUID, model: str, vector: list[float]) -> None:
    existing = ws.get(ChangeEmbedding, change_id)
    if existing is None:
        ws.add(ChangeEmbedding(change_id=change_id, model=model, embedding=vector))
    else:
        existing.model, existing.embedding = model, vector
