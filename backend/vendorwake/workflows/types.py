"""Inputs/outputs of workflows and activities, plus activity names and timeouts.

These are plain Pydantic models (serialised with ``temporalio.contrib.pydantic.pydantic_data_converter``).
Workflows call activities by *name* so workflow modules never import activity (I/O) code; tests register
fake activities under the same names.

Payloads stay small: large artefacts (snapshot text, inventories, reports) live in Postgres and activities
pass ids/hashes between each other.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from vendorwake.domain.contracts import DiscoveryCandidate

# ----------------------------------------------------------------------------------------- error types
#: ApplicationError.type values that must never be retried.
PERMISSION_DENIED = "PermissionDenied"
ADAPTER_UNAVAILABLE = "AdapterUnavailable"
NOT_FOUND = "NotFound"
INVALID_INPUT = "InvalidInput"
#: Model/provider quota exhausted (free tiers: ~50 requests/day). Retrying within minutes cannot help; re-run
#: the investigation later and completed steps are reused.
RATE_LIMITED = "RateLimited"
#: Per-minute model quota hit (e.g. Google AI Studio free tier: 16K tokens/min). Retryable, with the
#: provider's retry delay; completed steps are persisted, so a retry never re-sends finished work.
MODEL_RATE_LIMITED_RETRY = "ModelRateLimitedRetry"
NON_RETRYABLE_ERRORS = [PERMISSION_DENIED, ADAPTER_UNAVAILABLE, NOT_FOUND, INVALID_INPUT, RATE_LIMITED]

# ----------------------------------------------------------------------------------------- activity names
REPO_SET_STATUS = "repository.set_status"
REPO_ANALYZE = "repository.analyze"

DISCOVERY_SEARCH = "discovery.search"
DISCOVERY_FETCH = "discovery.fetch_snapshot"
DISCOVERY_EXTRACT = "discovery.extract"
DISCOVERY_EMBED = "discovery.embed"

INV_START = "investigation.start"
INV_STEP = "investigation.step"
INV_RECORD_DECISION = "investigation.record_decision"
INV_FINISH = "investigation.finish"
INV_FAIL = "investigation.fail"

REC_VERIFY = "recommendation.verify"

# ------------------------------------------------------------------------------------ investigation steps
STEP_UPGRADE_CHECK = "version_comparison"  # repo tests on pinned vs new version (sandbox, deterministic)
STEP_ASSESSMENT = "impact_assessment"  # deterministic matching + one impact-model call
STEP_PATCH = "patch_proposal"
STEP_VERIFICATION = "verification"
STEP_AGENT_SESSION = "agent_session"  # Entire
STEP_CONTEXT_SEED = "context_seed"  # Backboard
STEP_PERSIST = "persist_report"
INVESTIGATION_STEPS = (
    STEP_UPGRADE_CHECK,
    STEP_ASSESSMENT,
    STEP_PATCH,
    STEP_VERIFICATION,
    STEP_AGENT_SESSION,
    STEP_CONTEXT_SEED,
    STEP_PERSIST,
)
#: Sponsor steps whose failure is recorded but does not fail the investigation.
BEST_EFFORT_STEPS = frozenset({STEP_AGENT_SESSION, STEP_CONTEXT_SEED})
#: Steps that call the (rate-limited) model.
MODEL_STEPS = frozenset({STEP_ASSESSMENT, STEP_PATCH})

DEFAULT_APPROVAL_TIMEOUT = timedelta(days=7)
DEFAULT_MONITORING_INTERVAL = timedelta(hours=6)


# ----------------------------------------------------------------------------------------- repository
class RepositoryAnalysisInput(BaseModel):
    repository_id: uuid.UUID
    user_id: uuid.UUID


class RepositoryStatusInput(BaseModel):
    repository_id: uuid.UUID
    user_id: uuid.UUID
    status: Literal["pending", "running", "completed", "failed"]
    error: str | None = None


class RepositoryAnalysisResult(BaseModel):
    repository_id: uuid.UUID
    status: Literal["completed", "failed"]
    commit_sha: str | None = None
    integration_count: int = 0
    reused: bool = False  # inventory for this commit already existed; nothing was re-read


# ----------------------------------------------------------------------------------------- discovery
class DiscoveryInput(BaseModel):
    vendor: str
    max_sources: int = Field(default=5, ge=1, le=20)


class DiscoverySearchInput(BaseModel):
    vendor: str
    max_sources: int
    workflow_id: str


class DiscoverySearchResult(BaseModel):
    vendor: str
    urls: list[str]
    candidates: list[DiscoveryCandidate]
    queries: list[str]
    search_error: str | None = None


class FetchInput(BaseModel):
    vendor: str
    url: str


class SnapshotRef(BaseModel):
    vendor: str
    url: str
    snapshot_id: uuid.UUID
    retrieved_at: datetime
    content_sha256: str
    fetched_via: Literal["http", "browserbase"]
    changed: bool  # False: same hash as the latest stored snapshot, nothing inserted
    already_extracted: bool = False


class ExtractInput(BaseModel):
    vendor: str
    snapshot: SnapshotRef
    candidates: list[DiscoveryCandidate] = []


class ExtractResult(BaseModel):
    change_id: uuid.UUID | None
    created: bool
    reason: str


class DiscoveryResult(BaseModel):
    vendor: str
    sources_checked: int
    sources_changed: int
    change_ids: list[uuid.UUID]
    errors: list[str]


# ----------------------------------------------------------------------------------------- investigation
class InvestigationInput(BaseModel):
    investigation_id: uuid.UUID
    user_id: uuid.UUID
    approval_timeout_seconds: int = int(DEFAULT_APPROVAL_TIMEOUT.total_seconds())


class InvestigationStartInput(BaseModel):
    investigation_id: uuid.UUID
    user_id: uuid.UUID
    workflow_id: str
    run_id: str
    trace_id: str


class InvestigationContext(BaseModel):
    investigation_id: uuid.UUID
    repository_id: uuid.UUID
    change_id: uuid.UUID
    has_inventory: bool
    trace_id: str


class StepInput(BaseModel):
    investigation_id: uuid.UUID
    user_id: uuid.UUID
    step: str
    trace_id: str | None = None


class StepResult(BaseModel):
    step: str
    status: Literal["completed", "skipped"]
    reused: bool = False  # output loaded from investigation_steps; no work was repeated
    detail: str = ""
    recommendation_ids: list[uuid.UUID] = []  # persist_report only
    investigation_status: str | None = None  # persist_report only


class DecisionSignal(BaseModel):
    recommendation_id: uuid.UUID
    decision: Literal["approve", "dismiss"] = "approve"


class DecisionInput(BaseModel):
    investigation_id: uuid.UUID
    user_id: uuid.UUID
    recommendation_id: uuid.UUID
    decision: Literal["approve", "dismiss"]
    trace_id: str | None = None


class FinishInput(BaseModel):
    investigation_id: uuid.UUID
    user_id: uuid.UUID
    outcome: Literal["completed", "approval_timeout", "no_recommendations"]


class FailInput(BaseModel):
    investigation_id: uuid.UUID
    user_id: uuid.UUID
    step: str
    error_type: str


class InvestigationResult(BaseModel):
    investigation_id: uuid.UUID
    status: Literal["completed", "failed"]
    decisions: dict[str, str] = {}
    timed_out: bool = False


# ----------------------------------------------------------------------------------------- verification
class VerifyInput(BaseModel):
    recommendation_id: uuid.UUID
    user_id: uuid.UUID


class VerifyResult(BaseModel):
    recommendation_id: uuid.UUID
    verification_status: str
