"""ORM models for the Vendorwake schema.

Two kinds of tables:

* Global upstream data (vendors, monitoring_sources, vendor_changes, source_snapshots, discovery_queries,
  change_embeddings): public information shared by every tenant. The app role may only read it; the
  ``vendorwake_worker`` role (0002) writes it. Monitoring status changes from the API go through the
  narrow SECURITY DEFINER function ``vendorwake_set_vendor_monitoring``.
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

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
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
VENDOR_MONITORING_STATUSES = ("active", "paused", "off", "disabled")
SOURCE_TYPES = (
    "changelog",
    "documentation",
    "release_notes",
    "rss",
    "github_releases",
    "status_page",
    "other",
)
# 0001 values plus the contract's ChangeType (vendorwake.domain.contracts).
CHANGE_TYPES = (
    "breaking",
    "deprecation",
    "feature",
    "fix",
    "security",
    "documentation",
    "other",
    "removal",
    "rename",
    "schema_change",
    "auth_change",
    "rate_limit",
    "sdk_version",
    "behavioral",
    "additive",
)
SEVERITIES = ("critical", "high", "medium", "low", "info", "unknown")
INVESTIGATION_STATUSES = ("queued", "running", "waiting_approval", "completed", "failed", "cancelled")
IMPACT_STATUSES = ("confirmed", "potential", "unaffected", "unknown")
# 0001 values plus the contract's EvidenceKind.
EVIDENCE_TYPES = (
    "code_reference",
    "dependency_manifest",
    "documentation",
    "changelog",
    "test_result",
    "other",
    "vendor_source",
    "search_result",
    "dependency",
    "version_check",
    "model_inference",
)
# 0001 values plus the contract's Recommendation.recommendation_type.
RECOMMENDATION_TYPES = (
    "code_change",
    "dependency_upgrade",
    "configuration_change",
    "monitoring",
    "no_action",
    "other",
    "dependency_pin",
    "review",
    "test",
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
ANALYSIS_STATUSES = ("pending", "running", "completed", "failed")
FETCHED_VIA = ("http", "browserbase")
STEP_STATUSES = ("completed", "failed")
SNAPSHOT_EXTRACTION_STATUSES = ("change", "no_change", "failed")

#: Fixed dimension of change embeddings (BAAI/bge-small-en-v1.5). Must equal settings.embedding_dim (the
#: worker refuses to start otherwise). Changing it needs a migration.
EMBEDDING_DIM = 384

#: Tables protected by row-level security (policy column in parentheses: users -> id, others -> user_id).
TENANT_TABLES = (
    "users",
    "repositories",
    "integrations",
    "investigations",
    "impact_evidence",
    "recommendations",
    "agent_sessions",
    "investigation_steps",
)
#: Shared upstream tables, readable by every tenant and written only by the worker role.
GLOBAL_TABLES = (
    "vendors",
    "monitoring_sources",
    "vendor_changes",
    "source_snapshots",
    "discovery_queries",
    "change_embeddings",
)


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
        CheckConstraint(_in("analysis_status", ANALYSIS_STATUSES), name="analysis_status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )  # indexed by the leading column of uq(user_id, provider, external_repository_id)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'github'"))
    external_repository_id: Mapped[str] = mapped_column(String(255), nullable=False)
    repository_name: Mapped[str] = mapped_column(String(512), nullable=False)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    # 0002: what to analyse and the latest result. ``inventory`` is a contracts.RepositoryInventory.
    owner: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("'main'"))
    subpath: Mapped[str] = mapped_column(String(1024), nullable=False, server_default=text("''"))
    analysis_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    analysis_error: Mapped[str | None] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    inventory: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    monitoring_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'off'"))
    official_domains: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    schedule_id: Mapped[str | None] = mapped_column(String(255))


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
        CheckConstraint(_in("fetched_via", FETCHED_VIA), name="fetched_via"),
        Index(None, "vendor_id", "detected_at"),
        UniqueConstraint("vendor_id", "content_sha256"),
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
    # 0002: the extraction that produced this change and where its source text came from.
    package: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(128))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # source_snapshots.id (no FK)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_via: Mapped[str | None] = mapped_column(String(16))
    browser_session_id: Mapped[str | None] = mapped_column(String(255))
    is_official: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    change_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # contracts.ChangeExtraction
    grounding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # contracts.GroundingReport
    grounding_ok: Mapped[bool | None] = mapped_column(Boolean)
    model_calls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    discovery: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))


class SourceSnapshot(Base):
    """A fetched source document. TimescaleDB hypertable partitioned on ``retrieved_at``.

    Rows are inserted only when the content hash differs from the latest snapshot of the same URL. Nothing
    references this table with a foreign key (hypertables are not FK targets); vendor_changes keeps the id.
    """

    __tablename__ = "source_snapshots"
    __table_args__ = (
        PrimaryKeyConstraint("id", "retrieved_at"),  # a hypertable's unique keys must include the time column
        CheckConstraint(_in("fetched_via", FETCHED_VIA), name="fetched_via"),
        CheckConstraint(_in("extraction_status", SNAPSHOT_EXTRACTION_STATUSES), name="extraction_status"),
        Index(None, "url", "retrieved_at"),
        Index(None, "vendor_id", "retrieved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), server_default=text("gen_random_uuid()"))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitoring_sources.id", ondelete="SET NULL")
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # extracted page text
    title: Mapped[str | None] = mapped_column(Text)
    fetched_via: Mapped[str] = mapped_column(String(16), nullable=False)
    browser_session_id: Mapped[str | None] = mapped_column(String(255))
    screenshot_ref: Mapped[str | None] = mapped_column(Text)
    # Set once the extraction model has seen this snapshot, so it is never sent to the model twice.
    extraction_status: Mapped[str | None] = mapped_column(String(16))
    change_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DiscoveryQuery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One search-provider query issued by discovery, with its (official and unofficial) results."""

    __tablename__ = "discovery_queries"
    __table_args__ = (Index(None, "vendor_id", "executed_at"),)

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'serpapi'"))
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    results: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    workflow_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(String(255))


class ChangeEmbedding(TimestampMixin, Base):
    """Embedding of a vendor change, for "related changes" (cosine distance, HNSW index)."""

    __tablename__ = "change_embeddings"
    __table_args__ = (
        Index(
            "ix_change_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    change_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_changes.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)


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
    # 0002: the persisted outcome. The DB is the source of truth: the API never needs workflow history.
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # contracts.ImpactAssessment
    timeline: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    temporal_run_id: Mapped[str | None] = mapped_column(String(255))
    sentry_trace_id: Mapped[str | None] = mapped_column(String(64))
    backboard_thread_id: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(255))


class InvestigationStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted output of one investigation step, keyed by (investigation_id, step).

    Activities check this before doing expensive work (model calls, verification), so a retried activity, a
    restarted worker or a re-run workflow reuses the stored result instead of repeating it.
    """

    __tablename__ = "investigation_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["investigation_id", "user_id"],
            ["investigations.id", "investigations.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("investigation_id", "step"),
        CheckConstraint(_in("status", STEP_STATUSES), name="status"),
    )

    user_id: Mapped[uuid.UUID] = _tenant_user_id()
    investigation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    output: Mapped[Any | None] = mapped_column(JSONB)
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
    # 0002: contracts.EvidenceItem fields (content_reference = content_sha256, explanation = description)
    deterministic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    line: Mapped[int | None] = mapped_column(Integer)
    quote: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


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
        UniqueConstraint("investigation_id", "position"),
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
    # 0002: the full recommendation. Generating a patch never applies it.
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    file_path: Mapped[str | None] = mapped_column(Text)
    patch: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # contracts.VerificationResult
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A coding-agent session attached to an investigation (no provider credentials stored here)."""

    __tablename__ = "agent_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["investigation_id", "user_id"],
            ["investigations.id", "investigations.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("investigation_id", "provider"),
    )

    user_id: Mapped[uuid.UUID] = _tenant_user_id()
    investigation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_session_id: Mapped[str | None] = mapped_column(String(255))
    # "metadata" is reserved on declarative classes, hence the attribute name.
    session_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # 0002: Entire checkpoint details
    checkpoint_id: Mapped[str | None] = mapped_column(String(64))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(255))
    transcript_excerpt: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
