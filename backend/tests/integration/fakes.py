"""A fake ``Ports`` for DB-backed tests: deterministic contract objects, call counters, no network."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from vendorwake.domain.contracts import (
    AffectedComponent,
    ChangeExtraction,
    ChangeType,
    DependencyFinding,
    EvidenceGraph,
    EvidenceItem,
    EvidenceKind,
    ExtractedChange,
    GroundingReport,
    ImpactAssessment,
    ImpactStatus,
    IntegrationFinding,
    ModelCallInfo,
    Recommendation,
    ReferenceSite,
    RepoRef,
    RepositoryInventory,
    Severity,
    SourceDocument,
    VendorSignature,
    VerificationResult,
    VerificationStatus,
)
from vendorwake.services.adapters import OPERATION_SCOPES, AdapterUnavailableError, PermissionDecision

VENDOR = "acme"
SIGNATURE = VendorSignature(
    vendor=VENDOR,
    category="payments",
    packages={"pypi": ["acme-sdk"]},
    import_prefixes=["acme"],
    official_domains=["docs.acme.test"],
    changelog_urls=["https://docs.acme.test/changelog"],
)
CHANGELOG = "## 2.0.0\n- Removed `acme.Charge.legacy()`; use `acme.Charge.create()` instead.\n" * 20
CALL = ModelCallInfo(
    agent="extraction", provider="fake", model="fake-gemma", input_tokens=10, output_tokens=5, latency_s=0.1
)


def extraction(version: str = "2.0.0") -> ChangeExtraction:
    return ChangeExtraction(
        vendor=VENDOR,
        package="acme-sdk",
        version=version,
        release_date="2026-10-01",
        pinned_api_version=None,
        changes=[
            ExtractedChange(
                title="Charge.legacy removed",
                change_type=ChangeType.removal,
                severity=Severity.high,
                affected_symbols=["acme.Charge.legacy"],
                replacement="acme.Charge.create",
                evidence_quote="Removed `acme.Charge.legacy()`",
            )
        ],
        requires_human_review=False,
    )


def inventory(repo: RepoRef, commit: str) -> RepositoryInventory:
    dep = DependencyFinding(
        ecosystem="pypi",
        package="acme-sdk",
        version_spec="==1.0.0",
        pinned_version="1.0.0",
        manifest_path="requirements.txt",
    )
    return RepositoryInventory(
        repo=repo,
        commit_sha=commit,
        analyzed_at=datetime.now(UTC),
        files_scanned=2,
        dependencies=[dep],
        integrations=[
            IntegrationFinding(
                vendor=VENDOR,
                integration_type="sdk",
                packages=[dep],
                detected_version="1.0.0",
                references=[
                    ReferenceSite(file_path="app.py", line=2, symbol="acme.Charge.legacy", snippet="x")
                ],
                confidence=0.9,
            )
        ],
    )


def assessment(status: ImpactStatus = ImpactStatus.confirmed, deterministic: bool = True) -> ImpactAssessment:
    ev = EvidenceItem(
        kind=EvidenceKind.code_reference,
        deterministic=deterministic,
        description="uses Charge.legacy",
        file_path="app.py",
        line=2,
    )
    return ImpactAssessment(
        overall_status=status,
        severity=Severity.high,
        confidence=0.8,
        summary="app.py calls a removed method",
        affected=[
            AffectedComponent(
                change_title="Charge.legacy removed",
                file_path="app.py",
                line=2,
                symbol="acme.Charge.legacy",
                status=status,
                rationale="exact call",
                evidence=[ev],
            )
        ],
        evidence=[ev],
        graph=EvidenceGraph(nodes=[], edges=[]),
        recommendations=[],
        requires_human_review=False,
        model_calls=[CALL.model_copy(update={"agent": "impact"})],
    )


PASSED = VerificationResult(
    status=VerificationStatus.passed,
    command="pytest -q",
    exit_code=0,
    stdout_tail="1 passed",
    stderr_tail="",
    duration_s=1.0,
    environment="fake",
)


class FakeGuard:
    def __init__(self, denied: set[tuple[str, str]]) -> None:
        self.denied = denied
        self.checks: list[tuple[str, str]] = []

    def check(self, agent: str, operation: str) -> PermissionDecision:
        self.checks.append((agent, operation))
        allowed = (agent, operation) not in self.denied and operation != "push_branch"
        return PermissionDecision(
            agent,
            operation,
            OPERATION_SCOPES[operation],
            allowed,
            "granted" if allowed else "insufficient_scope",
        )

    def list_agents(self) -> list[dict[str, Any]]:
        return [{"agent": "migration", "client_id": "client-123", "scopes": ["patch:propose", "repo:read"]}]


class FakeStore:
    def __init__(self) -> None:
        self.seeded: list[str] = []

    def seed_investigation(self, *, user_key: str, investigation_id: str, summary: str) -> str:
        self.seeded.append(investigation_id)
        return f"thread-{investigation_id[:8]}"

    def ask(self, *, user_key: str, thread_id: str, question: str) -> tuple[str, list[dict]]:
        return f"answer to {question}", [
            {"memory": "app.py uses Charge.legacy", "score": 0.91, "id": "mem-1"}
        ]


class FakePorts:
    def __init__(self, *, denied: set[tuple[str, str]] | None = None, page: str = CHANGELOG) -> None:
        self.calls: Counter[str] = Counter()
        self.guard = FakeGuard(denied or set())
        self.store = FakeStore()
        self.page = page
        self.head = "a" * 40

    # catalog
    def signature(self, vendor: str) -> VendorSignature | None:
        return SIGNATURE if vendor == VENDOR else None

    def signatures(self) -> list[VendorSignature]:
        return [SIGNATURE]

    # integrations
    def repo_reader(self) -> Any:
        return SimpleNamespace(head_commit=lambda repo: self.head)

    def search(self, signature: VendorSignature) -> None:
        return None

    def fetcher(self) -> Any:
        def fetch(url: str) -> SourceDocument:
            self.calls["fetch"] += 1
            return SourceDocument(
                url=url,
                retrieved_at=datetime.now(UTC),
                fetched_via="http",
                content_sha256=hashlib.sha256(self.page.encode()).hexdigest(),
                text=self.page,
                title="Changelog",
            )

        return SimpleNamespace(fetch=fetch)

    def context_store(self) -> FakeStore:
        return self.store

    def session_recorder(self, worktree: str) -> Any:
        raise AdapterUnavailableError("Entire CLI not installed")

    def permission_guard(self) -> FakeGuard:
        return self.guard

    def embed(self, text: str) -> list[float]:
        self.calls["embed"] += 1
        digest = hashlib.sha256(text.encode()).digest()
        return [(digest[i % 32] - 128) / 128 for i in range(384)]

    # pipeline
    def repo_files(self, repo: RepoRef, commit_sha: str) -> dict[str, str]:
        self.calls["repo_files"] += 1
        return {"app.py": "import acme\nacme.Charge.legacy()\n", "requirements.txt": "acme-sdk==1.0.0\n"}

    def analyze(self, repo: RepoRef, files: dict[str, str], commit_sha: str | None) -> RepositoryInventory:
        self.calls["analyze"] += 1
        return inventory(repo, commit_sha or self.head)

    def extract(self, text: str, *, vendor: str, package: str, version: str | None) -> Any:
        self.calls["model:extract"] += 1
        return SimpleNamespace(
            extraction=extraction(),
            grounding=GroundingReport(ungrounded_quotes=[], ungrounded_symbols=[]),
            model_calls=[CALL],
        )

    def verify_upgrade(self, extraction: Any, files: dict, inventory: RepositoryInventory) -> dict:
        self.calls["sandbox:upgrade"] += 1
        run = {"label": "before", "result": PASSED.model_dump(mode="json")}
        return {"before": run, "after": {**run, "label": "after"}}

    def assess(
        self, extraction: Any, source: dict, files: dict, inventory: RepositoryInventory, comparison: Any
    ) -> ImpactAssessment:
        self.calls["model:assess"] += 1
        return assessment()

    def propose(self, assessment: ImpactAssessment, extraction: Any, files: dict) -> dict:
        self.calls["model:propose"] += 1
        rec = Recommendation(
            recommendation_type="code_change",
            description="Use Charge.create",
            file_path="app.py",
            patch="--- a/app.py\n+++ b/app.py\n",
        )
        return {
            "recommendations": [rec.model_dump(mode="json")],
            "patched_files": {"app.py": "acme.Charge.create()\n"},
            "model_calls": [CALL.model_dump(mode="json")],
        }

    def verify_patches(
        self, extraction: Any, files: dict, inventory: RepositoryInventory, patch_set: dict
    ) -> dict:
        self.calls["sandbox:patched"] += 1
        return {"label": "patched", "result": PASSED.model_dump(mode="json")}

    def finalize(
        self,
        assessment: ImpactAssessment,
        extraction: Any,
        inventory: RepositoryInventory,
        patch_set: Any,
        patched_run: Any,
    ) -> ImpactAssessment:
        recs = [Recommendation.model_validate(r) for r in (patch_set or {}).get("recommendations", [])]
        if patched_run:
            recs = [
                r.model_copy(
                    update={"verification": VerificationResult.model_validate(patched_run["result"])}
                )
                for r in recs
            ]
        return assessment.model_copy(update={"recommendations": recs})
