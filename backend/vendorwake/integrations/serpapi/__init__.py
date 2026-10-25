"""SerpApi web discovery (plain HTTPS; no SDK)."""

from __future__ import annotations

from collections.abc import Sequence

from vendorwake.core.config import Settings
from vendorwake.integrations.errors import NotConfiguredError
from vendorwake.integrations.serpapi.search import (
    SerpApiSearch,
    canonical_url,
    is_official_url,
)


def from_settings(
    settings: Settings, *, official_domains: Sequence[str] = (), **kwargs: object
) -> SerpApiSearch:
    if not settings.serpapi_api_key:
        raise NotConfiguredError("SerpApi is not configured: set VW_SERPAPI_API_KEY", service="serpapi")
    return SerpApiSearch(
        settings.serpapi_api_key.get_secret_value(),
        official_domains=official_domains,
        **kwargs,  # type: ignore[arg-type]
    )


__all__ = ["SerpApiSearch", "canonical_url", "from_settings", "is_official_url"]
