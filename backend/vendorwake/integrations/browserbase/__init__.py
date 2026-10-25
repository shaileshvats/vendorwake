"""Browserbase remote-browser fetcher (REST over httpx + raw CDP over ``websockets``; no SDK)."""

from __future__ import annotations

from vendorwake.core.config import Settings
from vendorwake.integrations.browserbase.fetcher import BrowserbasePageFetcher, ScreenshotSink
from vendorwake.integrations.errors import NotConfiguredError


def is_configured(settings: Settings) -> bool:
    return bool(settings.browserbase_api_key and settings.browserbase_project_id.strip())


def from_settings(settings: Settings, **kwargs: object) -> BrowserbasePageFetcher:
    if not is_configured(settings):
        raise NotConfiguredError(
            "Browserbase is not configured: set VW_BROWSERBASE_API_KEY and VW_BROWSERBASE_PROJECT_ID",
            service="browserbase",
        )
    assert settings.browserbase_api_key is not None
    return BrowserbasePageFetcher(
        settings.browserbase_api_key.get_secret_value(),
        settings.browserbase_project_id.strip(),
        **kwargs,  # type: ignore[arg-type]
    )


__all__ = ["BrowserbasePageFetcher", "ScreenshotSink", "from_settings", "is_configured"]
