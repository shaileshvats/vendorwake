"""Fixtures for adapter tests. Unit tests never touch the network (httpx.MockTransport / fakes).

``@pytest.mark.live`` tests call the real service and run only when its credentials are in the
environment (``live_env``); they are skipped otherwise.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import httpx
import pytest

from vendorwake.core.config import Settings


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: calls a real sponsor service; needs its credentials in the env")


def live_env(*names: str) -> pytest.MarkDecorator:
    """Skip unless every env var in ``names`` is set (non-empty)."""
    missing = [n for n in names if not os.environ.get(n, "").strip()]
    return pytest.mark.skipif(bool(missing), reason=f"live test needs {', '.join(missing)}")


def live_settings() -> Settings:
    return Settings(_env_file=None)


Handler = Callable[[httpx.Request], httpx.Response]


def mock_client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def public_resolver(host: str) -> list[str]:
    return ["93.184.215.14"]  # documentation-style public address; no DNS in unit tests
