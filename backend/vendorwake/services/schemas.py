"""Request/response models for the REST API (docs/api-contract.md). Contract types are reused as-is."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from vendorwake.domain.contracts import (
    ChangeExtraction,
    DiscoveryCandidate,
    ImpactAssessment,
    ModelCallInfo,
    Recommendation,
)

AnalysisStatus = Literal["pending", "running", "completed", "failed"]
MonitoringStatus = Literal["active", "paused", "off"]
InvestigationStatus = Literal["queued", "running", "waiting_approval", "completed", "failed"]
ApprovalStatus = Literal["pending", "approved", "dismissed"]
Operation = Literal["read_docs", "read_repo", "propose_patch", "push_branch"]

_GITHUB_NAME = r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})$"


# ------------------------------------------------------------------------------------------ repositories
class RepositoryCreate(BaseModel):
    owner: str = Field(pattern=_GITHUB_NAME, max_length=100)
    name: str = Field(pattern=_GITHUB_NAME, max_length=100)
    ref: str = Field(default="main", min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._/-]+$")
    subpath: str = Field(default="", max_length=1024)

    @field_validator("ref")
    @classmethod
    def _ref_safe(cls, value: str) -> str:
        if ".." in value or value.startswith("/") or value.endswith("/"):
            raise ValueError("invalid ref")
        return value

    @field_validator("subpath")
    @classmethod
    def _subpath_safe(cls, value: str) -> str:
        value = value.strip().strip("/")
        parts = [p for p in value.split("/") if p]
        if any(p in (".", "..") for p in parts) or "\\" in value or "\x00" in value:
            raise ValueError("subpath must be a relative path inside the repository")
        return "/".join(parts)


class RepositoryOut(BaseModel):
    id: uuid.UUID
    provider: str
    owner: str
    name: str
    ref: str
    subpath: str
    html_url: str
    analysis_status: AnalysisStatus
    last_analyzed_at: datetime | None
    commit_sha: str | None
    integration_count: int
    created_at: datetime


# ------------------------------------------------------------------------------------------ vendors
class VendorSource(BaseModel):
    url: str
    source_type: str
    last_checked_at: datetime | None


class VendorOut(BaseModel):
    name: str
    category: str | None
    website: str | None
    monitoring_status: MonitoringStatus
    official_domains: list[str]
    sources: list[VendorSource]


class MonitoringRequest(BaseModel):
    vendor: str = Field(min_length=1, max_length=64)


class MonitoringStarted(BaseModel):
    vendor: str
    monitoring_status: Literal["active"] = "active"
    schedule_id: str


class MonitoringStopped(BaseModel):
    vendor: str
    monitoring_status: Literal["paused"] = "paused"


class WorkflowStarted(BaseModel):
    workflow_id: str


# ------------------------------------------------------------------------------------------ changes
class ChangeSummary(BaseModel):
    id: uuid.UUID
    vendor: str
    title: str
    change_type: str
    severity: str
    published_at: datetime | None
    detected_at: datetime
    source_url: str | None
    is_official: bool
    change_count: int


class GroundingOut(BaseModel):
    ungrounded_quotes: list[str]
    ungrounded_symbols: list[str]
    ok: bool


class ChangeSource(BaseModel):
    url: str | None
    retrieved_at: datetime | None
    fetched_via: str | None
    content_sha256: str | None
    browser_session_id: str | None


class AffectedRepository(BaseModel):
    repository_id: uuid.UUID
    investigation_id: uuid.UUID
    impact_status: str


class ChangeDetail(ChangeSummary):
    extraction: ChangeExtraction | None
    grounding: GroundingOut | None
    source: ChangeSource
    discovery: list[DiscoveryCandidate]
    model_calls: list[ModelCallInfo]
    affected_repositories: list[AffectedRepository]


class RelatedChange(BaseModel):
    id: uuid.UUID
    vendor: str
    title: str
    similarity: float
    detected_at: datetime


class InvestigateRequest(BaseModel):
    repository_id: uuid.UUID


class InvestigationStarted(BaseModel):
    investigation_id: uuid.UUID
    workflow_id: str


# ------------------------------------------------------------------------------------------ investigations
class ChangeRef(BaseModel):
    id: uuid.UUID
    vendor: str
    title: str
    severity: str


class RepositoryRef(BaseModel):
    id: uuid.UUID
    owner: str | None
    name: str | None


class InvestigationSummary(BaseModel):
    id: uuid.UUID
    change: ChangeRef
    repository: RepositoryRef
    status: InvestigationStatus
    impact_status: str
    confidence: float | None
    created_at: datetime
    completed_at: datetime | None


class WorkflowInfo(BaseModel):
    id: str | None
    run_id: str | None
    status: str | None
    ui_url: str | None


class TraceInfo(BaseModel):
    sentry_trace_id: str
    url: str | None


class AgentSessionOut(BaseModel):
    provider: Literal["entire"] = "entire"
    session_id: str | None
    checkpoint_id: str | None
    commit_sha: str | None
    transcript_excerpt: str | None
    url: str | None


class TimelineEntry(BaseModel):
    at: datetime
    step: str
    status: str
    detail: str = ""


class InvestigationDetail(InvestigationSummary):
    summary: str | None
    assessment: ImpactAssessment | None
    workflow: WorkflowInfo
    trace: TraceInfo | None
    agent_session: AgentSessionOut | None
    timeline: list[TimelineEntry]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class RetrievedMemory(BaseModel):
    memory: str
    score: float | None = None
    id: str | None = None


class AskResponse(BaseModel):
    answer: str
    retrieved_memories: list[RetrievedMemory]
    model: str


class RecommendationOut(Recommendation):
    id: uuid.UUID
    approval_status: ApprovalStatus
    verification_status: str


# ------------------------------------------------------------------------------------------ security
class AgentOut(BaseModel):
    agent: str
    client_id: str | None
    scopes: list[str]


class AgentCheckRequest(BaseModel):
    agent: str = Field(min_length=1, max_length=64)
    operation: Operation


class AgentCheckResponse(BaseModel):
    agent: str
    operation: str
    required_scope: str
    allowed: bool
    reason: str


def as_float(value: Any) -> float | None:
    return float(value) if value is not None else None
