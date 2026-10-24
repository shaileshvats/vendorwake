"""ORM models for the Vendorwake schema.

Two kinds of tables:

* Global upstream data (vendors, monitoring_sources, vendor_changes): public information shared by every
  tenant. The app role may only read it; a worker role will write it (later milestone).
* Tenant-owned data (users and everything below repositories): every row carries a denormalised
  ``user_id`` so that row-level security policies are a single indexed equality check.

Tenant consistency: each child table references its parent through a *composite* foreign key on
``(parent_id, user_id)`` -> ``parent(id, user_id)`` (the parent exposes ``UNIQUE (id, user_id)``). The
database therefore rejects any child whose ``user_id`` differs from its parent's, without triggers.

No credentials or tokens are stored in any of these tables (GitHub access goes through Auth0 Token Vault).

Enumerations are stored as short text columns guarded by CHECK constraints, which are easier to extend in a
migration than PostgreSQL ENUM types. The allowed values live in the tuples below; the initial migration
keeps its own frozen copy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from vendorwake.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

REPOSITORY_PROVIDERS = ("github",)
VENDOR_MONITORING_STATUSES = ("active", "paused", "disabled")
SOURCE_TYPES = (
    "changelog",
    "documentation",
    "release_notes",
    "rss",
    "github_releases",
    "status_page",
    "other",
)
CHANGE_TYPES = ("breaking", "deprecation", "feature", "fix", "security", "documentation", "other")
SEVERITIES = ("critical", "high", "medium", "low", "info", "unknown")
INVESTIGATION_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
IMPACT_STATUSES = ("confirmed", "potential", "unaffected", "unknown")
EVIDENCE_TYPES = (
    "code_reference",
    "dependency_manifest",
    "documentation",
    "changelog",
    "test_result",
    "other",
)
RECOMMENDATION_TYPES = (
    "code_change",
    "dependency_upgrade",
    "configuration_change",
    "monitoring",
    "no_action",
    "other",
)
VERIFICATION_STATUSES = (
    "pending",
    "passed",
    "failed",
    "partially_verified",
    "not_testable",
    "requires_human_review",
)
APPROVAL_STATUSES = ("pending", "approved", "dismissed")

#: Tables protected by row-level security (policy column in parentheses: users -> id, others -> user_id).
TENANT_TABLES = (
    "users",
    "repositories",
    "integrations",
    "investigations",
    "impact_evidence",
    "recommendations",
    "agent_sessions",
)
#: Shared upstream tables, readable by every tenant.
GLOBAL_TABLES = ("vendors", "monitoring_sources", "vendor_changes")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _unit_interval(column: str) -> str:
    return f"{column} >= 0 AND {column} <= 1"


Confidence = Numeric(4, 3)  # 0.000 .. 1.000


def _tenant_user_id() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


# --------------------------------------------------------------------------------------------------
# Tenant root
# --------------------------------------------------------------------------------------------------


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str | None] = mapped_column(String(320))
    # The Auth0 subject ("sub" claim), e.g. "github|12345".
    auth_provider_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)


class Repository(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "external_repository_id"),
        UniqueConstraint("id", "user_id"),  # target of child composite FKs
        CheckConstraint(_in("provider", REPOSITORY_PROVIDERS), name="provider"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )  # indexed by the leading column of uq(user_id, provider, external_repository_id)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'github'"))
    external_repository_id: Mapped[str] = mapped_column(String(255), nullable=False)
    repository_name: Mapped[str] = mapped_column(String(512), nullable=False)
    default_branch: Mapped[str | None] = mapped_column(String(255))


# --------------------------------------------------------------------------------------------------
# Global upstream data
# --------------------------------------------------------------------------------------------------


class Vendor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vendors"
    __table_args__ = (
        CheckConstraint(_in("monitoring_status", VENDOR_MONITORING_STATUSES), name="monitoring_status"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    website: Mapped[str | None] = mapped_column(String(2048))
    category: Mapped[str | None] = mapped_column(String(64))
    monitoring_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )


class MonitoringSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "monitoring_sources"
    __table_args__ = (
        UniqueConstraint("vendor_id", "source_url"),
        CheckConstraint(_in("source_type", SOURCE_TYPES), name="source_type"),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_content_hash: Mapped[str | None] = mapped_column(String(128))


class VendorChange(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vendor_changes"
    __table_args__ = (
        CheckConstraint(_in("change_type", CHANGE_TYPES), name="change_type"),
        CheckConstraint(_in("severity", SEVERITIES), name="severity"),
        Index(None, "vendor_id", "detected_at"),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitoring_sources.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'other'"))
    severity: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'unknown'"))
    # References point at stored snapshots (object storage keys / content hashes), not inline content.
    previous_content_reference: Mapped[str | None] = mapped_column(Text)
    current_content_reference: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source_url: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------------------------------
# Tenant-owned data
# --------------------------------------------------------------------------------------------------


class Integration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A vendor SDK/package detected in a repository."""

    __tablename__ = "integrations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["repository_id", "user_id"], ["repositories.id", "repositories.user_id"], ondelete="CASCADE"
        ),
        UniqueConstraint("repository_id", "vendor_id", "package_name"),
        CheckConstraint(_unit_interval("confidence"), name="confidence_range"),
    )

    user_id: Mapped[uuid.UUID] = _tenant_user_id()
    repository_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    package_name: Mapped[str] = mapped_column(String(255), nullable=False)
    detected_version: Mapped[str | None] = mapped_column(String(128))
    source_files: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    confidence: Mapped[Decimal | None] = mapped_column(Confidence)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Investigation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "investigations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["repository_id", "user_id"], ["repositories.id", "repositories.user_id"], ondelete="CASCADE"
        ),
        UniqueConstraint("id", "user_id"),  # target of child composite FKs
        CheckConstraint(_in("status", INVESTIGATION_STATUSES), name="status"),
        CheckConstraint(_in("impact_status", IMPACT_STATUSES), name="impact_status"),
        CheckConstraint(_unit_interval("confidence"), name="confidence_range"),
    )

    user_id: Mapped[uuid.UUID] = _tenant_user_id()
    change_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_changes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'queued'"))
    impact_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'unknown'"))
    confidence: Mapped[Decimal | None] = mapped_column(Confidence)
    summary: Mapped[str | None] = mapped_column(Text)
    temporal_workflow_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImpactEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "impact_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["investigation_id", "user_id"],
            ["investigations.id", "investigations.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("evidence_type", EVIDENCE_TYPES), name="evidence_type"),
        CheckConstraint(_unit_interval("confidence"), name="confidence_range"),
    )

    user_id: Mapped[uuid.UUID] = _tenant_user_id()
    investigation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    content_reference: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Confidence)


class Recommendation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["investigation_id", "user_id"],
            ["investigations.id", "investigations.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("recommendation_type", RECOMMENDATION_TYPES), name="recommendation_type"),
        CheckConstraint(_in("verification_status", VERIFICATION_STATUSES), name="verification_status"),
        CheckConstraint(_in("approval_status", APPROVAL_STATUSES), name="approval_status"),
    )

    user_id: Mapped[uuid.UUID] = _tenant_user_id()
    investigation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    recommendation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_patch_reference: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))


class AgentSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A coding-agent session attached to an investigation (no provider credentials stored here)."""

    __tablename__ = "agent_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["investigation_id", "user_id"],
            ["investigations.id", "investigations.user_id"],
            ondelete="CASCADE",
        ),
    )

    user_id: Mapped[uuid.UUID] = _tenant_user_id()
    investigation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_session_id: Mapped[str | None] = mapped_column(String(255))
    # "metadata" is reserved on declarative classes, hence the attribute name.
    session_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
