"""Sentry spans for workflow activities, plus trace ids/links for the UI.

sentry-sdk 2.73 API (installed source): ``sentry_sdk.start_transaction(op=, name=)`` and
``sentry_sdk.start_span(op=, name=)`` return context managers; ``sentry_sdk.get_current_span()`` gives the
active span; spans expose ``trace_id``, ``set_data`` and ``set_status``; ``sentry_sdk.get_client()
.is_active()`` is False when Sentry was not initialised (no DSN), in which case spans are no-ops and we
return no URL (a link to a trace that was never sent would be misleading).

Use `sentry_sdk.consts.OP` constants for gen_ai spans (docs/m0-findings.md section 2); this module only
covers the activity-level span. Cross-process propagation (client -> workflow -> activity) is not
handled here: pass ``sentry_sdk.get_traceparent()``/``get_baggage()`` through Temporal headers and call
``sentry_sdk.continue_trace`` on the worker side.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import sentry_sdk

from vendorwake.core.config import Settings

ACTIVITY_OP = "workflow.activity"
# Trace view path on sentry.io. Not verified against a live org; adjust here if the UI path differs.
TRACE_URL_TEMPLATE = "{org_url}/performance/trace/{trace_id}/"


@dataclass(frozen=True, slots=True)
class TraceInfo:
    trace_id: str | None
    url: str | None


def sentry_active() -> bool:
    try:
        return sentry_sdk.get_client().is_active()
    except Exception:  # pragma: no cover - defensive; never break the caller over tracing
        return False


def trace_url(settings: Settings, trace_id: str | None) -> str | None:
    org_url = settings.sentry_org_url.strip().rstrip("/")
    if not org_url or not trace_id or not sentry_active():
        return None
    return TRACE_URL_TEMPLATE.format(org_url=org_url, trace_id=trace_id)


def current_trace(settings: Settings) -> TraceInfo:
    """Trace id of the active span (if any) and its Sentry URL when Sentry is enabled and
    ``VW_SENTRY_ORG_URL`` is set."""
    span = sentry_sdk.get_current_span()
    trace_id = getattr(span, "trace_id", None) if span is not None else None
    if not sentry_active():
        return TraceInfo(trace_id=None, url=None)
    return TraceInfo(trace_id=trace_id, url=trace_url(settings, trace_id))


@contextmanager
def activity_span(
    settings: Settings,
    name: str,
    *,
    op: str = ACTIVITY_OP,
    data: Mapping[str, Any] | None = None,
) -> Iterator[TraceInfo]:
    """Run a block inside a Sentry span named ``name``: a child of the current span when there is one,
    otherwise a new transaction. Yields the trace id and URL. Exceptions mark the span as errored and
    propagate. ``data`` must not contain secrets or page/prompt content."""
    parent = sentry_sdk.get_current_span()
    manager = (
        sentry_sdk.start_span(op=op, name=name)
        if parent is not None
        else sentry_sdk.start_transaction(op=op, name=name)
    )
    with manager as span:
        for key, value in (data or {}).items():
            span.set_data(key, value)
        trace_id = getattr(span, "trace_id", None)
        info = (
            TraceInfo(trace_id=trace_id, url=trace_url(settings, trace_id))
            if sentry_active()
            else TraceInfo(trace_id=None, url=None)
        )
        try:
            yield info
        except BaseException:
            span.set_status("internal_error")
            raise
