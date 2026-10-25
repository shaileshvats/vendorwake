"""Page fetching: plain HTTP (httpx + stdlib HTML parsing) with an optional Browserbase fallback."""

from __future__ import annotations

from collections.abc import Iterable

from vendorwake.core.config import Settings
from vendorwake.domain.contracts import PageFetcher
from vendorwake.integrations.http_fetch.fallback import FallbackPageFetcher
from vendorwake.integrations.http_fetch.fetcher import (
    FetchedPage,
    HttpPageFetcher,
    check_public_url,
    looks_like_js_shell,
    sha256_text,
)
from vendorwake.integrations.http_fetch.html_text import html_to_text


def from_settings(settings: Settings, **kwargs: object) -> HttpPageFetcher:
    """Plain HTTP needs no configuration."""
    return HttpPageFetcher(**kwargs)  # type: ignore[arg-type]


def build_page_fetcher(settings: Settings, *, dynamic_hosts: Iterable[str] = ()) -> PageFetcher:
    """`FallbackPageFetcher` (HTTP, then Browserbase when needed) if Browserbase is configured,
    otherwise plain HTTP only."""
    from vendorwake.integrations import browserbase  # local import: optional websockets dependency

    http = HttpPageFetcher()
    browser = browserbase.from_settings(settings) if browserbase.is_configured(settings) else None
    return FallbackPageFetcher(http, browser, dynamic_hosts=dynamic_hosts)


__all__ = [
    "FallbackPageFetcher",
    "FetchedPage",
    "HttpPageFetcher",
    "build_page_fetcher",
    "check_public_url",
    "from_settings",
    "html_to_text",
    "looks_like_js_shell",
    "sha256_text",
]
