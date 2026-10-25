"""What the API (and the worker's startup reconciliation) does with Temporal.

Workflow ids are deterministic so repeated requests attach to the running execution instead of starting a
second one:

* ``repository-analysis-<repository_id>``      (USE_EXISTING while running)
* ``discovery-<vendor>``                        (manual "discover now"; USE_EXISTING while running)
* ``investigation-<investigation_id>``
* ``verification-<recommendation_id>-<n>``
* schedule ``monitor-<vendor>`` starting ``DiscoveryWorkflow`` every ``DEFAULT_MONITORING_INTERVAL``
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
)
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError, RPCStatusCode

from vendorwake.core.config import Settings
from vendorwake.workflows import types as t
from vendorwake.workflows.definitions import (
    DiscoveryWorkflow,
    InvestigationWorkflow,
    RecommendationVerificationWorkflow,
    RepositoryAnalysisWorkflow,
)

logger = logging.getLogger(__name__)


def schedule_id_for(vendor: str) -> str:
    return f"monitor-{vendor}"


def investigation_workflow_id(investigation_id: uuid.UUID) -> str:
    return f"investigation-{investigation_id}"


class WorkflowGateway:
    def __init__(self, client: Client, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    @property
    def task_queue(self) -> str:
        return self._settings.temporal_task_queue

    # ---------------------------------------------------------------------------------- starts
    async def start_repository_analysis(self, repository_id: uuid.UUID, user_id: uuid.UUID) -> str:
        workflow_id = f"repository-analysis-{repository_id}"
        await self._client.start_workflow(
            RepositoryAnalysisWorkflow.run,
            t.RepositoryAnalysisInput(repository_id=repository_id, user_id=user_id),
            id=workflow_id,
            task_queue=self.task_queue,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            execution_timeout=timedelta(minutes=30),
        )
        return workflow_id

    async def start_discovery(self, vendor: str) -> str:
        workflow_id = f"discovery-{vendor}"
        await self._client.start_workflow(
            DiscoveryWorkflow.run,
            t.DiscoveryInput(vendor=vendor),
            id=workflow_id,
            task_queue=self.task_queue,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            execution_timeout=timedelta(hours=1),
        )
        return workflow_id

    async def start_investigation(self, investigation_id: uuid.UUID, user_id: uuid.UUID) -> str:
        workflow_id = investigation_workflow_id(investigation_id)
        await self._client.start_workflow(
            InvestigationWorkflow.run,
            t.InvestigationInput(investigation_id=investigation_id, user_id=user_id),
            id=workflow_id,
            task_queue=self.task_queue,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        return workflow_id

    async def start_verification(self, recommendation_id: uuid.UUID, user_id: uuid.UUID) -> str:
        workflow_id = f"verification-{recommendation_id}-{uuid.uuid4().hex[:8]}"
        await self._client.start_workflow(
            RecommendationVerificationWorkflow.run,
            t.VerifyInput(recommendation_id=recommendation_id, user_id=user_id),
            id=workflow_id,
            task_queue=self.task_queue,
            execution_timeout=timedelta(minutes=30),
        )
        return workflow_id

    # ---------------------------------------------------------------------------------- signals / status
    async def signal_decision(self, workflow_id: str, recommendation_id: uuid.UUID, decision: str) -> bool:
        """Deliver an approve/dismiss signal. False when the workflow no longer exists or has finished
        (e.g. a dev server lost its history): the decision is already persisted, so that is not an error."""
        handle = self._client.get_workflow_handle(workflow_id)
        signal = InvestigationWorkflow.approve if decision == "approve" else InvestigationWorkflow.dismiss
        try:
            await handle.signal(
                signal, t.DecisionSignal(recommendation_id=recommendation_id, decision=decision)
            )
        except RPCError as exc:
            if exc.status in (RPCStatusCode.NOT_FOUND, RPCStatusCode.FAILED_PRECONDITION):
                return False
            raise
        return True

    async def describe_status(self, workflow_id: str, run_id: str | None) -> str | None:
        """Temporal's execution status (lower-case, e.g. "running"), or None if the history is gone."""
        try:
            desc = await self._client.get_workflow_handle(workflow_id, run_id=run_id).describe(
                rpc_timeout=timedelta(seconds=2)
            )
        except RPCError:
            return None
        return desc.status.name.lower() if desc.status is not None else None

    # ---------------------------------------------------------------------------------- schedules
    async def ensure_schedule(self, vendor: str, every: timedelta = t.DEFAULT_MONITORING_INTERVAL) -> str:
        """Create the monitoring schedule, or unpause it if it exists. Idempotent."""
        schedule_id = schedule_id_for(vendor)
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                DiscoveryWorkflow.run,
                t.DiscoveryInput(vendor=vendor),
                id=f"discovery-{vendor}-scheduled",  # Temporal appends the scheduled time
                task_queue=self.task_queue,
                execution_timeout=timedelta(hours=1),
            ),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=every)]),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            state=ScheduleState(note="started by vendorwake"),
        )
        try:
            await self._client.create_schedule(schedule_id, schedule)
        except ScheduleAlreadyRunningError:
            await self._client.get_schedule_handle(schedule_id).unpause(note="monitoring started")
        return schedule_id

    async def pause_schedule(self, vendor: str) -> str:
        """Pause the monitoring schedule. A missing schedule counts as paused."""
        schedule_id = schedule_id_for(vendor)
        try:
            await self._client.get_schedule_handle(schedule_id).pause(note="monitoring stopped")
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
        return schedule_id

    async def reconcile_schedules(self, statuses: dict[str, str]) -> dict[str, str]:
        """Make Temporal match the DB (vendor -> monitoring_status). Used at worker startup, because an
        embedded dev server loses its schedules whenever the free instance restarts."""
        done: dict[str, str] = {}
        for vendor, status in sorted(statuses.items()):
            try:
                if status == "active":
                    await self.ensure_schedule(vendor)
                    done[vendor] = "active"
                elif status == "paused":
                    await self.pause_schedule(vendor)
                    done[vendor] = "paused"
            except RPCError as exc:
                logger.warning("schedule reconcile failed for %s: %s", vendor, exc.status.name)
                done[vendor] = "error"
        return done


def workflow_ui_url(settings: Settings, workflow_id: str, run_id: str | None) -> str:
    base = settings.temporal_ui_url.rstrip("/")
    url = f"{base}/namespaces/{settings.temporal_namespace}/workflows/{workflow_id}"
    return f"{url}/{run_id}/history" if run_id else url
