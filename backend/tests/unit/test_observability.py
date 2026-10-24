from __future__ import annotations

import sentry_sdk
from pydantic import SecretStr

from vendorwake.core.observability import init_sentry, scrub_event

from .conftest import make_settings


def test_init_sentry_is_noop_without_dsn() -> None:
    assert init_sentry(make_settings(sentry_dsn=None)) is False
    assert init_sentry(make_settings(sentry_dsn=SecretStr(""))) is False
    assert init_sentry(make_settings(sentry_dsn=SecretStr("   "))) is False
    assert sentry_sdk.get_client().is_active() is False


def test_scrub_event_removes_auth_headers_and_cookies() -> None:
    event = {
        "request": {
            "url": "https://api.example/api/me",
            "headers": {
                "Authorization": "Bearer abc.def.ghi",
                "Cookie": "s=1",
                "Accept": "*/*",
                "X-Api-Key": "k",
            },
            "cookies": {"s": "1"},
            "query_string": "access_token=leaky&x=1",
            "data": {"password": "hunter2", "name": "ok"},
        },
        "breadcrumbs": {"values": [{"message": "GET with Bearer abc.def.ghi", "data": {"token": "t"}}]},
    }
    scrubbed = scrub_event(event, {})
    request = scrubbed["request"]
    assert request["headers"] == {"Accept": "*/*"}
    assert "cookies" not in request
    assert "leaky" not in request["query_string"] and "x=1" in request["query_string"]
    assert request["data"] == {"password": "[REDACTED]", "name": "ok"}
    crumb = scrubbed["breadcrumbs"]["values"][0]
    assert "abc.def.ghi" not in crumb["message"]
    assert crumb["data"] == {"token": "[REDACTED]"}


def test_scrub_event_masks_log_messages_and_exception_values() -> None:
    event = {
        "logentry": {"message": "upstream said %s", "params": ["Authorization: Bearer abc.def.ghi"]},
        "exception": {"values": [{"type": "ValueError", "value": "bad password=hunter2"}]},
    }
    scrubbed = scrub_event(event, {})
    assert "abc.def.ghi" not in str(scrubbed["logentry"]["params"])
    assert "hunter2" not in scrubbed["exception"]["values"][0]["value"]


def test_init_sentry_with_dsn_passes_safe_options(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: captured.update(kwargs))
    settings = make_settings(sentry_dsn=SecretStr("https://public@o0.ingest.sentry.io/0"), env="staging")
    assert init_sentry(settings) is True
    assert captured["dsn"] == "https://public@o0.ingest.sentry.io/0"
    assert captured["environment"] == "staging"
    assert captured["send_default_pii"] is False
    assert captured["before_send"] is scrub_event and captured["before_send_transaction"] is scrub_event
    assert {type(i).__name__ for i in captured["integrations"]} == {
        "StarletteIntegration",
        "FastApiIntegration",
    }


def test_real_sentry_init_accepts_our_options() -> None:
    # Smoke-check the kwargs against the installed SDK, then shut the client down again.
    settings = make_settings(sentry_dsn=SecretStr("https://public@o0.ingest.sentry.io/0"))
    try:
        assert init_sentry(settings) is True
        assert sentry_sdk.get_client().is_active()
        assert sentry_sdk.get_client().options["send_default_pii"] is False
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)  # back to NonRecordingClient
    assert sentry_sdk.get_client().is_active() is False
