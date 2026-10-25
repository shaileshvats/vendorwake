"""Shared contracts for the upstream-to-impact pipeline.

Everything that crosses a module boundary (pipeline stages, sponsor adapters, workflows, API) uses these
types. Business logic depends only on these models and the Protocols at the bottom, never on a sponsor SDK.

Ground rules encoded here:
- An impact is CONFIRMED only with deterministic evidence (exact code reference + affected version, or a
  test result). Model output alone can at most produce POTENTIAL.
- Every evidence item says whether it is deterministic and where it came from (file/line or URL + hash).
- Model calls report real usage from the provider; nothing is estimated or invented.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------- enums


class ChangeType(StrEnum):
    breaking = "breaking"
    deprecation = "deprecation"
    removal = "removal"
    rename = "rename"
    schema_change = "schema_change"
    auth_change = "auth_change"
    rate_limit = "rate_limit"
    security = "security"
    sdk_version = "sdk_version"
    behavioral = "behavioral"
    additive = "additive"
    other = "other"


class Severity(StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class ImpactStatus(StrEnum):
    confirmed = "confirmed"
    potential = "potential"
    unaffected = "unaffected"
    unknown = "unknown"


class VerificationStatus(StrEnum):
    pending = "pending"
    passed = "passed"
    failed = "failed"
    partially_verified = "partially_verified"
    not_testable = "not_testable"
    requires_human_review = "requires_human_review"


class EvidenceKind(StrEnum):
    vendor_source = "vendor_source"  # official announcement / changelog text
    search_result = "search_result"  # discovery hit; never proof on its own
    dependency = "dependency"  # manifest/lockfile entry
    code_reference = "code_reference"  # exact symbol usage found by static analysis
    version_check = "version_check"  # installed version vs affected range
    test_result = "test_result"  # sandboxed verification output
    model_inference = "model_inference"  # model reasoning; never sufficient for CONFIRMED


# ---------------------------------------------------------------------------- model calls


class ModelCallInfo(BaseModel):
    agent: str  # e.g. "extraction", "impact", "migration"
    provider: str  # e.g. "openrouter"
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_s: float
    repaired: bool = False  # True if the first output failed validation and a repair call fixed it


# ---------------------------------------------------------------------------- vendors & sources


class VendorSignature(BaseModel):
    """How to recognise a vendor in a repository and where its authoritative sources live."""

    vendor: str
    category: str
    packages: dict[str, list[str]]  # ecosystem ("pypi"|"npm"|...) -> package names
    import_prefixes: list[str] = []  # e.g. ["stripe"]
    api_hosts: list[str] = []  # e.g. ["api.stripe.com"]
    env_vars: list[str] = []  # e.g. ["STRIPE_SECRET_KEY"]
    official_domains: list[str]  # only these count as authoritative sources
    changelog_urls: list[str] = []


class DiscoveryCandidate(BaseModel):
    url: str
    title: str
    snippet: str = ""
    query: str
    rank: int
    is_official: bool  # url host is in the vendor's official_domains


class SourceDocument(BaseModel):
    url: str
    retrieved_at: datetime
    fetched_via: Literal["http", "browserbase"]
    content_sha256: str
    text: str
    title: str | None = None
    browser_session_id: str | None = None  # Browserbase session, when used
    screenshot_ref: str | None = None


# ---------------------------------------------------------------------------- extraction (model output)


class ExtractedChange(BaseModel):
    title: str = Field(max_length=200)
    change_type: ChangeType
    severity: Severity
    affected_symbols: list[str] = Field(description="Exact names as written in the source")
    replacement: str | None = Field(description="Replacement API named in the source, or null")
    evidence_quote: str = Field(description="Short verbatim quote from the source")


class ChangeExtraction(BaseModel):
    vendor: str
    package: str
    version: str
    release_date: str | None
    pinned_api_version: str | None
    changes: list[ExtractedChange]
    requires_human_review: bool


class GroundingReport(BaseModel):
    """Deterministic check of a ChangeExtraction against its source text."""

    ungrounded_quotes: list[str]
    ungrounded_symbols: list[str]

    @property
    def ok(self) -> bool:
        return not self.ungrounded_quotes and not self.ungrounded_symbols


# ---------------------------------------------------------------------------- repository analysis


class RepoRef(BaseModel):
    provider: Literal["github"] = "github"
    owner: str
    name: str
    ref: str = "main"
    subpath: str = ""  # analyse only this directory (e.g. a fixture inside a monorepo)


class DependencyFinding(BaseModel):
    ecosystem: str
    package: str
    version_spec: str | None  # as written, e.g. "==15.6.1", "^4.2.0"
    pinned_version: str | None  # exact version when determinable
    manifest_path: str
    line: int | None = None


class ReferenceSite(BaseModel):
    file_path: str
    line: int
    symbol: str
    snippet: str  # the source line, stripped


class IntegrationFinding(BaseModel):
    vendor: str
    integration_type: Literal["sdk", "http_api", "config"]
    packages: list[DependencyFinding]
    detected_version: str | None
    references: list[ReferenceSite]  # imports / API host / env var usages
    confidence: float = Field(ge=0, le=1)


class RepositoryInventory(BaseModel):
    repo: RepoRef
    commit_sha: str | None
    analyzed_at: datetime
    files_scanned: int
    dependencies: list[DependencyFinding]
    integrations: list[IntegrationFinding]


# ---------------------------------------------------------------------------- impact


class EvidenceItem(BaseModel):
    kind: EvidenceKind
    deterministic: bool
    description: str
    file_path: str | None = None
    line: int | None = None
    source_url: str | None = None
    content_sha256: str | None = None
    quote: str | None = None


class AffectedComponent(BaseModel):
    change_title: str
    file_path: str
    line: int | None
    symbol: str
    status: ImpactStatus
    rationale: str
    evidence: list[EvidenceItem]


class EvidenceNode(BaseModel):
    id: str
    type: Literal["vendor_source", "change", "symbol", "integration", "file", "test", "risk"]
    label: str
    status: ImpactStatus | None = None


class EvidenceEdge(BaseModel):
    source: str
    target: str
    label: str
    evidence_index: int | None = None  # index into ImpactAssessment.evidence


class EvidenceGraph(BaseModel):
    nodes: list[EvidenceNode]
    edges: list[EvidenceEdge]


class VerificationResult(BaseModel):
    status: VerificationStatus
    command: str
    exit_code: int | None
    stdout_tail: str
    stderr_tail: str
    duration_s: float
    environment: str  # e.g. "subprocess venv, python 3.12, stripe==16.0.0, network=off"
    before_version: str | None = None
    after_version: str | None = None


class Recommendation(BaseModel):
    recommendation_type: Literal["code_change", "dependency_pin", "review", "test"]
    description: str
    file_path: str | None = None
    patch: str | None = None  # unified diff; generation is separate from application
    verification: VerificationResult | None = None


class ImpactAssessment(BaseModel):
    overall_status: ImpactStatus
    severity: Severity
    confidence: float = Field(ge=0, le=1)  # model estimate, not a calibrated probability
    summary: str
    affected: list[AffectedComponent]
    evidence: list[EvidenceItem]
    graph: EvidenceGraph
    recommendations: list[Recommendation]
    requires_human_review: bool
    model_calls: list[ModelCallInfo]


# ---------------------------------------------------------------------------- adapter protocols


class StructuredLLM(Protocol):
    def generate[T: BaseModel](
        self, *, agent: str, system: str, user: str, schema: type[T], max_tokens: int = 2048
    ) -> tuple[T, ModelCallInfo]: ...


class SearchProvider(Protocol):  # SerpApi
    def search(self, query: str, *, num: int = 10) -> list[DiscoveryCandidate]: ...


class PageFetcher(Protocol):  # plain HTTP, Browserbase fallback
    def fetch(self, url: str) -> SourceDocument: ...


class RepoReader(Protocol):  # GitHub
    def head_commit(self, repo: RepoRef) -> str: ...
    def list_files(self, repo: RepoRef) -> list[str]: ...
    def read_file(self, repo: RepoRef, path: str) -> str: ...


class ContextStore(Protocol):  # Backboard
    def seed_investigation(self, *, user_key: str, investigation_id: str, summary: str) -> str: ...
    def ask(self, *, user_key: str, thread_id: str, question: str) -> tuple[str, list[dict]]: ...


class SessionRecorder(Protocol):  # Entire
    def start(self, *, investigation_id: str, prompt: str, model: str) -> str: ...
    def record_turn(self, *, session_id: str, content: str, model: str, usage: dict | None) -> None: ...
    def end(self, *, session_id: str) -> dict: ...  # returns {"session_id", "checkpoint_id", ...}
