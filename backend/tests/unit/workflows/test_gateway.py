"""WorkflowGateway against the real Temporal dev server: idempotent schedules, missing workflows tolerated."""

from __future__ import annotations

import uuid

from tests.unit.conftest import make_settings
from vendorwake.workflows.gateway import WorkflowGateway, schedule_id_for

from .fakes import connect


def test_schedules_are_idempotent_and_reconcilable(run, task_queue: str) -> None:
    vendor = f"test-{uuid.uuid4().hex[:8]}"

    async def scenario() -> None:
        client = await connect()
        gateway = WorkflowGateway(client, make_settings(temporal_task_queue=task_queue))
        handle = client.get_schedule_handle(schedule_id_for(vendor))
        try:
            assert await gateway.ensure_schedule(vendor) == f"monitor-{vendor}"
            assert await gateway.ensure_schedule(vendor) == f"monitor-{vendor}"  # exists: unpause, no error
            await gateway.pause_schedule(vendor)
            assert (await handle.describe()).schedule.state.paused
            # worker startup after a dev-server restart: DB says active -> schedule active again
            assert await gateway.reconcile_schedules({vendor: "active"}) == {vendor: "active"}
            assert not (await handle.describe()).schedule.state.paused
            assert await gateway.pause_schedule(f"missing-{vendor}") == f"monitor-missing-{vendor}"
        finally:
            await handle.delete()

    run(scenario())


def test_missing_workflow_is_not_an_error(run) -> None:
    async def scenario() -> None:
        gateway = WorkflowGateway(await connect(), make_settings())
        missing = f"investigation-{uuid.uuid4()}"
        assert await gateway.signal_decision(missing, uuid.uuid4(), "approve") is False
        assert await gateway.describe_status(missing, None) is None

    run(scenario())
