"""Workflow orchestration: ordering, approval signals, timeouts, non-retryable denials, worker restarts."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.worker import Worker

from vendorwake.workflows import types as t
from vendorwake.workflows.definitions import (
    WORKFLOWS,
    DiscoveryWorkflow,
    InvestigationWorkflow,
    RepositoryAnalysisWorkflow,
)

from .fakes import Fakes, connect


def _worker(client, task_queue: str, fakes: Fakes) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=WORKFLOWS,
        activities=fakes.all(),
        graceful_shutdown_timeout=timedelta(seconds=1),
    )


def _inv_input(timeout_s: int = 60) -> t.InvestigationInput:
    return t.InvestigationInput(
        investigation_id=uuid.uuid4(), user_id=uuid.uuid4(), approval_timeout_seconds=timeout_s
    )


async def _start_investigation(client, task_queue: str, inp: t.InvestigationInput):
    return await client.start_workflow(
        InvestigationWorkflow.run,
        inp,
        id=f"test-investigation-{inp.investigation_id}",
        task_queue=task_queue,
        execution_timeout=timedelta(minutes=2),
    )


async def _wait_for(predicate, timeout: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.1)


def test_investigation_runs_every_step_once_then_waits_for_approval(run, task_queue: str) -> None:
    fakes = Fakes(recommendations=2)

    async def scenario() -> t.InvestigationResult:
        client = await connect()
        async with _worker(client, task_queue, fakes):
            handle = await _start_investigation(client, task_queue, _inv_input())
            await _wait_for(lambda: fakes.calls[t.STEP_PERSIST] == 1)
            await asyncio.sleep(0.3)
            assert fakes.calls[t.INV_FINISH] == 0  # waiting for a human, nothing decided yet
            first, second = fakes.recommendations
            await handle.signal(InvestigationWorkflow.approve, t.DecisionSignal(recommendation_id=first))
            await handle.signal(
                InvestigationWorkflow.dismiss, t.DecisionSignal(recommendation_id=second, decision="dismiss")
            )
            # duplicates and unknown ids are ignored
            await handle.signal(InvestigationWorkflow.approve, t.DecisionSignal(recommendation_id=first))
            await handle.signal(
                InvestigationWorkflow.approve, t.DecisionSignal(recommendation_id=uuid.uuid4())
            )
            return await handle.result()

    result = run(scenario())
    assert result.status == "completed" and not result.timed_out
    assert result.decisions == {
        str(fakes.recommendations[0]): "approve",
        str(fakes.recommendations[1]): "dismiss",
    }
    assert all(fakes.calls[s] == 1 for s in t.INVESTIGATION_STEPS)
    assert fakes.calls[t.INV_RECORD_DECISION] == 2
    assert fakes.finished == ["completed"]
    assert fakes.calls[t.REPO_ANALYZE] == 0  # inventory existed


def test_investigation_without_recommendations_completes_immediately(run, task_queue: str) -> None:
    fakes = Fakes(recommendations=0, has_inventory=False)

    async def scenario() -> t.InvestigationResult:
        client = await connect()
        async with _worker(client, task_queue, fakes):
            handle = await _start_investigation(client, task_queue, _inv_input())
            return await handle.result()

    result = run(scenario())
    assert result.status == "completed"
    assert fakes.finished == ["no_recommendations"]
    assert fakes.calls[t.REPO_ANALYZE] == 1  # missing inventory -> child RepositoryAnalysisWorkflow


def test_approval_times_out(run, task_queue: str) -> None:
    fakes = Fakes(recommendations=1)

    async def scenario() -> t.InvestigationResult:
        client = await connect()
        async with _worker(client, task_queue, fakes):
            handle = await _start_investigation(client, task_queue, _inv_input(timeout_s=2))
            return await handle.result()

    result = run(scenario())
    assert result.timed_out and result.decisions == {}
    assert fakes.finished == ["approval_timeout"]
    assert fakes.calls[t.INV_RECORD_DECISION] == 0


def test_permission_denial_fails_without_retry(run, task_queue: str) -> None:
    fakes = Fakes(deny_step=t.STEP_PATCH)

    async def scenario() -> None:
        client = await connect()
        async with _worker(client, task_queue, fakes):
            handle = await _start_investigation(client, task_queue, _inv_input())
            with pytest.raises(WorkflowFailureError):
                await handle.result()

    run(scenario())
    assert fakes.calls[t.STEP_PATCH] == 1  # non-retryable: exactly one attempt
    assert fakes.calls[t.STEP_VERIFICATION] == 0  # nothing after the denied step ran
    assert fakes.failed == [(t.STEP_PATCH, t.PERMISSION_DENIED)]


def test_best_effort_sponsor_step_failure_does_not_fail_investigation(run, task_queue: str) -> None:
    fakes = Fakes(recommendations=0, fail_step=t.STEP_CONTEXT_SEED)

    async def scenario() -> t.InvestigationResult:
        client = await connect()
        async with _worker(client, task_queue, fakes):
            return await (await _start_investigation(client, task_queue, _inv_input())).result()

    assert run(scenario()).status == "completed"
    assert fakes.calls[t.STEP_PERSIST] == 1 and fakes.failed == []


def test_worker_restart_resumes_without_repeating_completed_steps(run, task_queue: str) -> None:
    """Kill the worker while verification runs; a new worker (fresh process state) finishes the workflow.

    Completed activities are in the workflow history, so the second worker replays them instead of running
    them again: the model-calling steps run exactly once across both workers.
    """
    first = Fakes(recommendations=0, block_step=t.STEP_VERIFICATION)
    second = Fakes(recommendations=0)

    async def scenario() -> t.InvestigationResult:
        client = await connect()
        inp = _inv_input()
        async with _worker(client, task_queue, first):
            handle = await _start_investigation(client, task_queue, inp)
            await asyncio.wait_for(first.blocked.wait(), timeout=20)
        # first worker is gone (its blocked activity was cancelled by the shutdown)
        async with _worker(await connect(), task_queue, second):
            return await asyncio.wait_for(handle.result(), timeout=90)

    result = run(scenario())
    assert result.status == "completed"
    for step in (t.STEP_UPGRADE_CHECK, t.STEP_ASSESSMENT, t.STEP_PATCH):
        assert first.calls[step] == 1 and second.calls[step] == 0, step
    assert first.calls[t.INV_START] == 1 and second.calls[t.INV_START] == 0
    assert second.calls[t.STEP_VERIFICATION] == 1  # the interrupted step is retried on the new worker
    assert second.calls[t.STEP_PERSIST] == 1 and second.finished == ["no_recommendations"]


@pytest.mark.parametrize(
    ("changed", "already_extracted", "extracts"),
    [(True, False, 1), (False, False, 1), (False, True, 0)],
)
def test_discovery_skips_extraction_of_seen_content(
    run, task_queue: str, changed: bool, already_extracted: bool, extracts: int
) -> None:
    fakes = Fakes(snapshot_changed=changed, already_extracted=already_extracted)

    async def scenario() -> t.DiscoveryResult:
        client = await connect()
        async with _worker(client, task_queue, fakes):
            return await client.execute_workflow(
                DiscoveryWorkflow.run,
                t.DiscoveryInput(vendor="stripe"),
                id=f"test-discovery-{uuid.uuid4().hex}",
                task_queue=task_queue,
                execution_timeout=timedelta(minutes=1),
            )

    result = run(scenario())
    assert fakes.calls[t.DISCOVERY_EXTRACT] == extracts
    assert fakes.calls[t.DISCOVERY_EMBED] == extracts
    assert len(result.change_ids) == extracts
    assert result.sources_checked == 1 and result.sources_changed == int(changed)


def test_repository_analysis_failure_marks_failed(run, task_queue: str) -> None:
    fakes = Fakes(fail_step=t.REPO_ANALYZE)

    async def scenario() -> None:
        client = await connect()
        async with _worker(client, task_queue, fakes):
            with pytest.raises(WorkflowFailureError):
                await client.execute_workflow(
                    RepositoryAnalysisWorkflow.run,
                    t.RepositoryAnalysisInput(repository_id=uuid.uuid4(), user_id=uuid.uuid4()),
                    id=f"test-repo-{uuid.uuid4().hex}",
                    task_queue=task_queue,
                    execution_timeout=timedelta(minutes=1),
                )

    run(scenario())
    assert fakes.repo_statuses == ["running", "failed"]
    assert fakes.calls[t.REPO_ANALYZE] == 1
