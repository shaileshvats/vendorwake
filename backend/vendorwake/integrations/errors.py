"""Typed errors shared by all sponsor adapters.

Messages are safe to log and to show to an operator: they never contain credentials, signed URLs or
response bodies that might echo a secret back.
"""

from __future__ import annotations


class IntegrationError(Exception):
    """Base class. ``service`` names the external service (e.g. "github", "serpapi")."""

    def __init__(self, message: str, *, service: str) -> None:
        super().__init__(message)
        self.service = service


class NotConfiguredError(IntegrationError):
    """A required setting (API key, project id, ...) is missing, so the adapter cannot be built."""


class UpstreamError(IntegrationError):
    """The external service failed or rejected the request (network error, 4xx/5xx, bad payload)."""

    def __init__(self, message: str, *, service: str, status_code: int | None = None) -> None:
        super().__init__(message, service=service)
        self.status_code = status_code


class RateLimitedError(UpstreamError):
    """The service rate-limited us. ``retry_after_s`` is the server's hint when it gave one."""

    def __init__(
        self,
        message: str,
        *,
        service: str,
        status_code: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message, service=service, status_code=status_code)
        self.retry_after_s = retry_after_s


class ResourceNotFoundError(UpstreamError):
    """The repository, file, thread, ... does not exist (or is not visible with our credentials)."""


class ContentRejectedError(IntegrationError):
    """The content was fetched but is not acceptable (too large, binary, unsupported type, unsafe URL)."""
