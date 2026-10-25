"""Fake activities with the real activity names; they count calls and can block or fail on demand."""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter

from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError

from vendorwake.workflows import types as t

from .conftest import ADDRESS


async def connect() -> Client:
    return await Client.connect(ADDRESS, data_converter=pydantic_data_converter)


class Fakes:
    """State is shared between "worker incarnations" only when the same instance is reused on purpose."""

    def __init__(
        self,
        *,
        recommendations: int = 1,
        deny_step: str | None = None,
        fail_step: str | None = None,
        block_step: str | None = None,
        has_inventory: bool = True,
        snapshot_changed: bool = True,
        already_extracted: bool = False,
    ) -> None:
        self.calls: Counter[str] = Counter()
        self.recommendations = [uuid.uuid4() for _ in range(recommendations)]
        self.deny_step, self.fail_step, self.block_step = deny_step, fail_step, block_step
        self.has_inventory = has_inventory
        self.snapshot_changed, self.already_extracted = snapshot_changed, already_extracted
        self.blocked = asyncio.Event()  # set when the blocking step has started
        self.decisions: list[tuple[str, str]] = []
        self.finished: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.repo_statuses: list[str] = []

    # ---------------------------------------------------------------------------------------- repository
    @activity.defn(name=t.REPO_SET_STATUS)
    async def repo_set_status(self, inp: t.RepositoryStatusInput) -> None:
        self.calls[t.REPO_SET_STATUS] += 1
        self.repo_statuses.append(inp.status)

    @activity.defn(name=t.REPO_ANALYZE)
    async def repo_analyze(self, inp: t.RepositoryAnalysisInput) -> t.RepositoryAnalysisResult:
        self.calls[t.REPO_ANALYZE] += 1
        if self.fail_step == t.REPO_ANALYZE:
            raise ApplicationError("no such repo", type=t.NOT_FOUND, non_retryable=True)
        return t.RepositoryAnalysisResult(
            repository_id=inp.repository_id, status="completed", integration_count=1
        )

    # ---------------------------------------------------------------------------------------- discovery
    @activity.defn(name=t.DISCOVERY_SEARCH)
    async def search(self, inp: t.DiscoverySearchInput) -> t.DiscoverySearchResult:
        self.calls[t.DISCOVERY_SEARCH] += 1
        return t.DiscoverySearchResult(
            vendor=inp.vendor, urls=["https://example.test/changelog"], candidates=[], queries=["q"]
        )

    @activity.defn(name=t.DISCOVERY_FETCH)
    async def fetch(self, inp: t.FetchInput) -> t.SnapshotRef:
        self.calls[t.DISCOVERY_FETCH] += 1
        return t.SnapshotRef(
            vendor=inp.vendor,
            url=inp.url,
            snapshot_id=uuid.uuid4(),
            retrieved_at="2026-10-25T00:00:00Z",
            content_sha256="0" * 64,
            fetched_via="http",
            changed=self.snapshot_changed,
            already_extracted=self.already_extracted,
        )

    @activity.defn(name=t.DISCOVERY_EXTRACT)
    async def extract(self, inp: t.ExtractInput) -> t.ExtractResult:
        self.calls[t.DISCOVERY_EXTRACT] += 1
        return t.ExtractResult(change_id=uuid.uuid4(), created=True, reason="extracted")

    @activity.defn(name=t.DISCOVERY_EMBED)
    async def embed(self, change_id: uuid.UUID) -> bool:
        self.calls[t.DISCOVERY_EMBED] += 1
        return True

    # ---------------------------------------------------------------------------------------- investigation
    @activity.defn(name=t.INV_START)
    async def start(self, inp: t.InvestigationStartInput) -> t.InvestigationContext:
        self.calls[t.INV_START] += 1
        return t.InvestigationContext(
            investigation_id=inp.investigation_id,
            repository_id=uuid.uuid4(),
            change_id=uuid.uuid4(),
            has_inventory=self.has_inventory,
            trace_id=inp.trace_id,
        )

    @activity.defn(name=t.INV_STEP)
    async def step(self, inp: t.StepInput) -> t.StepResult:
        self.calls[inp.step] += 1
        if inp.step == self.deny_step:
            raise ApplicationError("permission denied", type=t.PERMISSION_DENIED, non_retryable=True)
        if inp.step == self.fail_step:
            raise ApplicationError("boom", type="Boom", non_retryable=True)
        if inp.step == self.block_step:
            self.blocked.set()
            await asyncio.Event().wait()  # until the worker shuts down (cancellation)
        result = t.StepResult(step=inp.step, status="completed")
        if inp.step == t.STEP_PERSIST:
            result.recommendation_ids = list(self.recommendations)
            result.investigation_status = "waiting_approval" if self.recommendations else "completed"
        return result

    @activity.defn(name=t.INV_RECORD_DECISION)
    async def record_decision(self, inp: t.DecisionInput) -> str:
        self.calls[t.INV_RECORD_DECISION] += 1
        self.decisions.append((str(inp.recommendation_id), inp.decision))
        return inp.decision

    @activity.defn(name=t.INV_FINISH)
    async def finish(self, inp: t.FinishInput) -> None:
        self.calls[t.INV_FINISH] += 1
        self.finished.append(inp.outcome)

    @activity.defn(name=t.INV_FAIL)
    async def fail(self, inp: t.FailInput) -> None:
        self.calls[t.INV_FAIL] += 1
        self.failed.append((inp.step, inp.error_type))

    @activity.defn(name=t.REC_VERIFY)
    async def verify(self, inp: t.VerifyInput) -> t.VerifyResult:
        self.calls[t.REC_VERIFY] += 1
        return t.VerifyResult(recommendation_id=inp.recommendation_id, verification_status="passed")

    def all(self) -> list:
        return [
            self.repo_set_status,
            self.repo_analyze,
            self.search,
            self.fetch,
            self.extract,
            self.embed,
            self.start,
            self.step,
            self.record_decision,
            self.finish,
            self.fail,
            self.verify,
        ]
