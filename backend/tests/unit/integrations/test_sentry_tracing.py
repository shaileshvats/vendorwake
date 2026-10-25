from __future__ import annotations

import re

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from vendorwake.integrations.sentry import activity_span, current_trace, trace_url

from ..conftest import make_settings


class CaptureTransport(Transport):
    def __init__(self, options=None) -> None:
        super().__init__(options)
        self.envelopes: list = []

    def capture_envelope(self, envelope) -> None:
        self.envelopes.append(envelope)


@pytest.fixture
def sentry_on():
    transport = CaptureTransport()
    sentry_sdk.init(dsn="https://public@o0.ingest.sentry.io/0", traces_sample_rate=1.0, transport=transport)
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)


def test_without_sentry_there_is_no_trace_or_url() -> None:
    settings = make_settings(sentry_org_url="https://acme.sentry.io")
    with activity_span(settings, "fetch_sources") as info:
        assert info.trace_id is None and info.url is None
    assert current_trace(settings).url is None
    assert trace_url(settings, "abc") is None


def test_activity_span_yields_trace_id_and_url(sentry_on) -> None:
    settings = make_settings(sentry_org_url="https://acme.sentry.io/")
    with activity_span(settings, "analyze_repository", data={"repo": "acme/shop"}) as outer:
        assert outer.trace_id and re.fullmatch(r"[0-9a-f]{32}", outer.trace_id)
        assert outer.url == f"https://acme.sentry.io/performance/trace/{outer.trace_id}/"
        with activity_span(settings, "read_files") as inner:
            assert inner.trace_id == outer.trace_id  # child span, same trace
        assert current_trace(settings).trace_id == outer.trace_id
    sentry_sdk.flush()
    assert sentry_on.envelopes


def test_activity_span_without_org_url_has_trace_but_no_url(sentry_on) -> None:
    with activity_span(make_settings(sentry_org_url=""), "x") as info:
        assert info.trace_id and info.url is None


def test_activity_span_marks_errors_and_reraises(sentry_on) -> None:
    with pytest.raises(RuntimeError), activity_span(make_settings(), "boom"):
        raise RuntimeError("fail")
