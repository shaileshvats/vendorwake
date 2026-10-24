"""Sentry error and performance monitoring.

Sentry is enabled only when ``VW_SENTRY_DSN`` is set. PII is off and every event/transaction passes
through `scrub_event`, which removes auth headers and cookies and masks secret-looking values on top
of the SDK's own default scrubber.

Note for later milestones: with sentry-sdk 2.73, gen_ai (LLM) spans are sent as v2 span-streaming
envelope items, not inside ``transaction["spans"]``, so ``before_send_transaction`` does not see them.
Scrub prompt/response content at the point where those spans are created.
"""

from __future__ import annotations

import logging
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from vendorwake import __version__
from vendorwake.core.config import Settings
from vendorwake.core.logging import REDACTED, is_sensitive_key, redact_text, redact_value

logger = logging.getLogger(__name__)

_DROPPED_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"})


def _scrub_headers(headers: Any) -> Any:
    if isinstance(headers, dict):
        return {
            k: v
            for k, v in ((k, REDACTED if is_sensitive_key(k) else v) for k, v in headers.items())
            if not (isinstance(k, str) and k.lower() in _DROPPED_HEADERS)
        }
    if isinstance(headers, list):  # list of [name, value] pairs
        return [
            pair
            for pair in headers
            if not (isinstance(pair, list | tuple) and pair and str(pair[0]).lower() in _DROPPED_HEADERS)
        ]
    return headers


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Remove credentials from a Sentry error event or transaction before it leaves the process."""
    request = event.get("request")
    if isinstance(request, dict):
        if "headers" in request:
            request["headers"] = _scrub_headers(request["headers"])
        request.pop("cookies", None)
        if isinstance(request.get("query_string"), str):
            request["query_string"] = redact_text(request["query_string"])
        if "data" in request:
            request["data"] = redact_value(request["data"])
        if isinstance(request.get("url"), str):
            request["url"] = redact_text(request["url"])

    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else breadcrumbs
    if isinstance(values, list):
        for crumb in values:
            if isinstance(crumb, dict):
                if isinstance(crumb.get("message"), str):
                    crumb["message"] = redact_text(crumb["message"])
                if isinstance(crumb.get("data"), dict):
                    crumb["data"] = redact_value(crumb["data"])

    # Log-record events (LoggingIntegration hooks in before our logging filter runs) and exception text.
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        for key in ("message", "formatted"):
            if isinstance(logentry.get(key), str):
                logentry[key] = redact_text(logentry[key])
        if "params" in logentry:
            logentry["params"] = redact_value(logentry["params"])
    if isinstance(event.get("message"), str):
        event["message"] = redact_text(event["message"])
    exception = event.get("exception")
    exc_values = exception.get("values") if isinstance(exception, dict) else None
    if isinstance(exc_values, list):
        for exc in exc_values:
            if isinstance(exc, dict) and isinstance(exc.get("value"), str):
                exc["value"] = redact_text(exc["value"])

    if isinstance(event.get("extra"), dict):
        event["extra"] = redact_value(event["extra"])
    return event


def init_sentry(settings: Settings) -> bool:
    """Initialise Sentry if a DSN is configured. Returns True when Sentry was enabled."""
    dsn = settings.sentry_dsn.get_secret_value().strip() if settings.sentry_dsn else ""
    if not dsn:
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.env,
        release=f"vendorwake@{__version__}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        before_send=scrub_event,
        before_send_transaction=scrub_event,
    )
    logger.info("sentry enabled", extra={"sentry_environment": settings.env})
    return True
