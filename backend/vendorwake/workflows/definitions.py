"""Temporal workflow definitions. Deterministic orchestration only: every I/O happens in an activity.

Activities are referenced by name (see ``types``); their implementations live in ``activities`` and are
registered by ``vendorwake.worker``. Each activity is idempotent, so retries and worker restarts never
repeat completed work (the investigation steps additionally persist their outputs keyed by
``(investigation_id, step)``).
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from vendorwake.workflows import types as t

# ------------------------------------------------------------------------------------------- policies
_DB = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    non_retryable_error_types=t.NON_RETRYABLE_ERRORS,
)
_NETWORK = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=4,
    non_retryable_error_types=t.NON_RETRYABLE_ERRORS,
)
# Model calls are rate-limited (free tiers allow ~50 requests/day): few attempts, long gaps.
_MODEL = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=6,  # per-minute model quotas: a few spaced retries (next_retry_delay) succeed
    non_retryable_error_types=t.NON_RETRYABLE_ERRORS,
)
_BEST_EFFORT = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=2,
    non_retryable_error_types=t.NON_RETRYABLE_ERRORS,
)
_DB_TIMEOUT = timedelta(seconds=30)

_STEP_POLICIES: dict[str, tuple[timedelta, RetryPolicy]] = {
    t.STEP_UPGRADE_CHECK: (timedelta(minutes=10), _NETWORK),
    t.STEP_ASSESSMENT: (timedelta(minutes=5), _MODEL),
    t.STEP_PATCH: (timedelta(minutes=5), _MODEL),
    t.STEP_VERIFICATION: (timedelta(minutes=10), _NETWORK),  # sandbox run + finalize
    t.STEP_AGENT_SESSION: (timedelta(minutes=2), _BEST_EFFORT),
    t.STEP_CONTEXT_SEED: (timedelta(minutes=2), _BEST_EFFORT),
    t.STEP_PERSIST: (timedelta(minutes=1), _DB),
}


def _error_type(exc: BaseException) -> str:
    """Best description of a failure that is safe to persist (an error *type*, never a message)."""
    cause = exc.cause if isinstance(exc, ActivityError | ChildWorkflowError) else exc
    if isinstance(cause, ApplicationError) and cause.type:
        return cause.type
    return type(cause).__name__ if cause is not None else type(exc).__name__


# ------------------------------------------------------------------------------------------- repository
@workflow.defn(name="RepositoryAnalysisWorkflow")
class RepositoryAnalysisWorkflow:
    """Read the repository (RepoReader) -> build the inventory (analysis) -> persist it."""

    @workflow.run
    async def run(self, inp: t.RepositoryAnalysisInput) -> t.RepositoryAnalysisResult:
        await workflow.execute_activity(
            t.REPO_SET_STATUS,
            t.RepositoryStatusInput(repository_id=inp.repository_id, user_id=inp.user_id, status="running"),
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB,
        )
        try:
            return await workflow.execute_activity(
                t.REPO_ANALYZE,
                inp,
                result_type=t.RepositoryAnalysisResult,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=_NETWORK,
            )
        except ActivityError as exc:
            await workflow.execute_activity(
                t.REPO_SET_STATUS,
                t.RepositoryStatusInput(
                    repository_id=inp.repository_id,
                    user_id=inp.user_id,
                    status="failed",
                    error=_error_type(exc),
                ),
                start_to_close_timeout=_DB_TIMEOUT,
                retry_policy=_DB,
            )
            raise ApplicationError(
                f"repository analysis failed: {_error_type(exc)}", type="RepositoryAnalysisFailed"
            ) from exc


# ------------------------------------------------------------------------------------------- discovery
@workflow.defn(name="DiscoveryWorkflow")
class DiscoveryWorkflow:
    """Search -> official-domain filter -> fetch + snapshot (skip unchanged) -> extract + ground -> embed."""

    @workflow.run
    async def run(self, inp: t.DiscoveryInput) -> t.DiscoveryResult:
        search = await workflow.execute_activity(
            t.DISCOVERY_SEARCH,
            t.DiscoverySearchInput(
                vendor=inp.vendor, max_sources=inp.max_sources, workflow_id=workflow.info().workflow_id
            ),
            result_type=t.DiscoverySearchResult,
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=_NETWORK,
        )
        errors: list[str] = [f"search: {search.search_error}"] if search.search_error else []
        changed = 0
        change_ids = []
        for url in search.urls:
            try:
                snapshot = await workflow.execute_activity(
                    t.DISCOVERY_FETCH,
                    t.FetchInput(vendor=inp.vendor, url=url),
                    result_type=t.SnapshotRef,
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=_NETWORK,
                )
            except ActivityError as exc:
                if _error_type(exc) == t.PERMISSION_DENIED:
                    raise
                errors.append(f"fetch {url}: {_error_type(exc)}")
                continue
            if snapshot.changed:
                changed += 1
            if not snapshot.changed and snapshot.already_extracted:
                continue  # same content as last time, and the model has already seen it
            try:
                extracted = await workflow.execute_activity(
                    t.DISCOVERY_EXTRACT,
                    t.ExtractInput(
                        vendor=inp.vendor,
                        snapshot=snapshot,
                        candidates=[c for c in search.candidates if c.url == url],
                    ),
                    result_type=t.ExtractResult,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_MODEL,
                )
            except ActivityError as exc:
                errors.append(f"extract {url}: {_error_type(exc)}")
                continue
            if extracted.change_id is None:
                continue
            change_ids.append(extracted.change_id)
            try:
                await workflow.execute_activity(
                    t.DISCOVERY_EMBED,
                    extracted.change_id,
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=_NETWORK,
                )
            except ActivityError as exc:  # related-changes is a nicety; the change itself is stored
                errors.append(f"embed {extracted.change_id}: {_error_type(exc)}")
        return t.DiscoveryResult(
            vendor=inp.vendor,
            sources_checked=len(search.urls),
            sources_changed=changed,
            change_ids=change_ids,
            errors=errors,
        )


# ------------------------------------------------------------------------------------------- investigation
@workflow.defn(name="InvestigationWorkflow")
class InvestigationWorkflow:
    """Impact investigation for one (change, repository), then a human decision per recommendation.

    Nothing consequential happens automatically. Approving a recommendation only records the decision; the
    workflow then asks the permission guard whether the migration agent may ``push_branch`` and records the
    (expected) denial: no agent holds ``repo:write``, and pushing is out of scope.
    """

    def __init__(self) -> None:
        self._queue: list[t.DecisionSignal] = []
        self._decisions: dict[str, str] = {}
        self._pending: set[str] = set()
        self._stage = "starting"

    @workflow.signal
    def approve(self, signal: t.DecisionSignal) -> None:
        self._queue.append(t.DecisionSignal(recommendation_id=signal.recommendation_id, decision="approve"))

    @workflow.signal
    def dismiss(self, signal: t.DecisionSignal) -> None:
        self._queue.append(t.DecisionSignal(recommendation_id=signal.recommendation_id, decision="dismiss"))

    @workflow.query
    def progress(self) -> dict:
        return {"stage": self._stage, "pending": sorted(self._pending), "decisions": dict(self._decisions)}

    async def _step(self, inp: t.InvestigationInput, trace_id: str, step: str) -> t.StepResult | None:
        timeout, policy = _STEP_POLICIES[step]
        self._stage = step
        try:
            return await workflow.execute_activity(
                t.INV_STEP,
                t.StepInput(
                    investigation_id=inp.investigation_id, user_id=inp.user_id, step=step, trace_id=trace_id
                ),
                result_type=t.StepResult,
                start_to_close_timeout=timeout,
                retry_policy=policy,
            )
        except ActivityError as exc:
            if step in t.BEST_EFFORT_STEPS and _error_type(exc) != t.PERMISSION_DENIED:
                return None  # the activity already recorded the failure in the timeline
            raise

    @workflow.run
    async def run(self, inp: t.InvestigationInput) -> t.InvestigationResult:
        info = workflow.info()
        trace_id = workflow.uuid4().hex  # deterministic; one Sentry trace for the whole investigation
        step = "start"
        try:
            ctx = await workflow.execute_activity(
                t.INV_START,
                t.InvestigationStartInput(
                    investigation_id=inp.investigation_id,
                    user_id=inp.user_id,
                    workflow_id=info.workflow_id,
                    run_id=info.run_id,
                    trace_id=trace_id,
                ),
                result_type=t.InvestigationContext,
                start_to_close_timeout=_DB_TIMEOUT,
                retry_policy=_DB,
            )
            trace_id = ctx.trace_id  # a re-run keeps the trace id stored by the first run
            if not ctx.has_inventory:
                step = "repository_analysis"
                self._stage = step
                await workflow.execute_child_workflow(
                    RepositoryAnalysisWorkflow.run,
                    t.RepositoryAnalysisInput(repository_id=ctx.repository_id, user_id=inp.user_id),
                    id=f"repository-analysis-{ctx.repository_id}-inv-{inp.investigation_id}",
                    execution_timeout=timedelta(minutes=30),
                )
            persisted: t.StepResult | None = None
            for step in t.INVESTIGATION_STEPS:
                result = await self._step(inp, trace_id, step)
                if step == t.STEP_PERSIST:
                    persisted = result
        except (ActivityError, ChildWorkflowError) as exc:
            await workflow.execute_activity(
                t.INV_FAIL,
                t.FailInput(
                    investigation_id=inp.investigation_id,
                    user_id=inp.user_id,
                    step=step,
                    error_type=_error_type(exc),
                ),
                start_to_close_timeout=_DB_TIMEOUT,
                retry_policy=_DB,
            )
            raise ApplicationError(
                f"investigation failed at {step}: {_error_type(exc)}", type="InvestigationFailed"
            ) from exc

        assert persisted is not None
        self._pending = {str(r) for r in persisted.recommendation_ids}
        if not self._pending:
            await self._finish(inp, "no_recommendations")
            return t.InvestigationResult(investigation_id=inp.investigation_id, status="completed")

        self._stage = "waiting_approval"
        timed_out = await self._await_decisions(inp, trace_id)
        await self._finish(inp, "approval_timeout" if timed_out else "completed")
        return t.InvestigationResult(
            investigation_id=inp.investigation_id,
            status="completed",
            decisions=dict(self._decisions),
            timed_out=timed_out,
        )

    async def _await_decisions(self, inp: t.InvestigationInput, trace_id: str) -> bool:
        deadline = workflow.now() + timedelta(seconds=inp.approval_timeout_seconds)
        while self._pending:
            remaining = deadline - workflow.now()
            if remaining <= timedelta(0):
                return True
            try:
                await workflow.wait_condition(lambda: bool(self._queue), timeout=remaining)
            except TimeoutError:
                return True
            while self._queue:
                signal = self._queue.pop(0)
                key = str(signal.recommendation_id)
                if key not in self._pending:
                    continue  # unknown or already decided
                await workflow.execute_activity(
                    t.INV_RECORD_DECISION,
                    t.DecisionInput(
                        investigation_id=inp.investigation_id,
                        user_id=inp.user_id,
                        recommendation_id=signal.recommendation_id,
                        decision=signal.decision,
                        trace_id=trace_id,
                    ),
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=_NETWORK,
                )
                self._pending.discard(key)
                self._decisions[key] = signal.decision
        return False

    async def _finish(self, inp: t.InvestigationInput, outcome: str) -> None:
        self._stage = "finished"
        await workflow.execute_activity(
            t.INV_FINISH,
            t.FinishInput(investigation_id=inp.investigation_id, user_id=inp.user_id, outcome=outcome),
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB,
        )


# ------------------------------------------------------------------------------------------- verification
@workflow.defn(name="RecommendationVerificationWorkflow")
class RecommendationVerificationWorkflow:
    """Re-run sandboxed verification for one recommendation (POST /recommendations/{id}/verify)."""

    @workflow.run
    async def run(self, inp: t.VerifyInput) -> t.VerifyResult:
        return await workflow.execute_activity(
            t.REC_VERIFY,
            inp,
            result_type=t.VerifyResult,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=_NETWORK,
        )


WORKFLOWS = [
    RepositoryAnalysisWorkflow,
    DiscoveryWorkflow,
    InvestigationWorkflow,
    RecommendationVerificationWorkflow,
]
