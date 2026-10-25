"""Sponsor integration adapters.

Each subpackage wraps exactly one external service behind a Protocol from `vendorwake.domain.contracts`
(or a small, documented interface of its own), so business logic never imports a sponsor SDK. Every
subpackage exposes a ``from_settings(settings)`` factory that raises `NotConfiguredError` with a clear
message when the service is not configured.

Subpackages are imported explicitly (``from vendorwake.integrations.github import GitHubRepoReader``);
only the dependency-free shared errors are re-exported here, so an optional SDK that is not installed never
breaks unrelated imports.
"""

from vendorwake.integrations.errors import (
    ContentRejectedError,
    IntegrationError,
    NotConfiguredError,
    RateLimitedError,
    ResourceNotFoundError,
    UpstreamError,
)

__all__ = [
    "ContentRejectedError",
    "IntegrationError",
    "NotConfiguredError",
    "RateLimitedError",
    "ResourceNotFoundError",
    "UpstreamError",
]
