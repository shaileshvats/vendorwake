"""Temporal worker for every Vendorwake workflow and activity. Run: ``python -m vendorwake.worker``.

On start it (1) checks the embedding dimension, (2) connects to Temporal (local dev server or Temporal
Cloud), (3) re-creates monitoring schedules from ``vendors.monitoring_status`` (an embedded dev server
loses them when a free instance spins down), then polls ``settings.temporal_task_queue`` until SIGTERM or
SIGINT, finishing in-flight activities for up to ``SHUTDOWN_GRACE`` before exiting.

Needs ``VW_DATABASE_URL`` (app role, tenant data via RLS) and ``VW_WORKER_DATABASE_URL`` (worker role, global
data).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from temporalio.worker import Worker

from vendorwake.core.config import Settings, get_settings
from vendorwake.core.logging import configure_logging
from vendorwake.core.observability import init_sentry
from vendorwake.db.models import EMBEDDING_DIM
from vendorwake.services.adapters import LazyPorts, Ports
from vendorwake.workflows.activities import build_activities
from vendorwake.workflows.definitions import WORKFLOWS

logger = logging.getLogger("vendorwake.worker")

MAX_ACTIVITY_THREADS = 8
SHUTDOWN_GRACE = timedelta(seconds=20)


def _monitoring_statuses() -> dict[str, str]:
    from sqlalchemy.orm import Session

    from vendorwake.db.session import get_worker_engine
    from vendorwake.services.vendors import monitoring_statuses

    with Session(get_worker_engine()) as s:
        return monitoring_statuses(s)


async def reconcile_schedules(client, settings: Settings) -> None:  # noqa: ANN001
    from vendorwake.workflows.gateway import WorkflowGateway

    try:
        statuses = await asyncio.to_thread(_monitoring_statuses)
    except Exception as exc:  # the worker is still useful without schedules
        logger.warning("schedule reconcile skipped: %s", type(exc).__name__)
        return
    result = await WorkflowGateway(client, settings).reconcile_schedules(statuses)
    if result:
        logger.info("monitoring schedules reconciled", extra={"schedules": result})


async def run_worker(
    settings: Settings, ports: Ports | None = None, stop: asyncio.Event | None = None
) -> None:
    from vendorwake.workflows.client import connect_client

    if settings.embedding_dim != EMBEDDING_DIM:
        raise SystemExit(
            f"VW_EMBEDDING_DIM={settings.embedding_dim} but the database column is {EMBEDDING_DIM}"
        )
    client = await connect_client(settings)
    await reconcile_schedules(client, settings)
    stop = stop or asyncio.Event()
    activities = build_activities(ports or LazyPorts(settings), settings)
    with ThreadPoolExecutor(max_workers=MAX_ACTIVITY_THREADS, thread_name_prefix="vw-activity") as executor:
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=WORKFLOWS,
            activities=activities,
            activity_executor=executor,
            max_concurrent_activities=MAX_ACTIVITY_THREADS,
            graceful_shutdown_timeout=SHUTDOWN_GRACE,
        )
        logger.info("worker started", extra={"task_queue": settings.temporal_task_queue})
        async with worker:
            await stop.wait()
            logger.info("worker shutting down")
    logger.info("worker stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_sentry(settings)

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run_worker(settings, stop=stop)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(130)


if __name__ == "__main__":
    main()
