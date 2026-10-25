"""Workflow tests run against a real Temporal server with *fake* activities registered under the real names.

Server: ``VW_TEST_TEMPORAL_ADDRESS`` (default ``localhost:7233``, the docker-compose dev server). Tests are
skipped when nothing listens there. We do not use ``WorkflowEnvironment.start_time_skipping()``: it downloads
a test-server binary on first use, which the slow network here cannot afford; timeouts are kept short instead.
Every test uses its own task queue and workflow ids, so it never interferes with a running Vendorwake worker.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid

import pytest

ADDRESS = os.environ.get("VW_TEST_TEMPORAL_ADDRESS", "localhost:7233")


def _reachable(address: str) -> bool:
    host, _, port = address.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _reachable(ADDRESS):
        return
    skip = pytest.mark.skip(
        reason=f"workflow tests need a Temporal server at {ADDRESS} (VW_TEST_TEMPORAL_ADDRESS)"
    )
    for item in items:
        if "workflows" in str(item.path):
            item.add_marker(skip)


@pytest.fixture()
def task_queue() -> str:
    return f"vw-test-{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def run():
    def _run(coro):
        return asyncio.run(coro)

    return _run
