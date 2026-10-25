"""Temporal client factory: local dev server or Temporal Cloud (API key + TLS)."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from vendorwake.core.config import Settings


async def connect_client(settings: Settings) -> Client:
    """Connect to ``settings.temporal_address`` / ``temporal_namespace``.

    With ``VW_TEMPORAL_API_KEY`` set (Temporal Cloud) the connection uses TLS and the API key, and sends the
    ``temporal-namespace`` header that Temporal Cloud's API-key endpoints route on. Without it, a plaintext
    connection to a local dev server (``temporal server start-dev``).
    """
    api_key = settings.temporal_api_key.get_secret_value() if settings.temporal_api_key else None
    if api_key:
        return await Client.connect(
            settings.temporal_address,
            namespace=settings.temporal_namespace,
            api_key=api_key,
            tls=True,
            rpc_metadata={"temporal-namespace": settings.temporal_namespace},
            data_converter=pydantic_data_converter,
        )
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
