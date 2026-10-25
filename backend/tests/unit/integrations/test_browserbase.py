from __future__ import annotations

import base64
import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from vendorwake.integrations import browserbase
from vendorwake.integrations.browserbase import BrowserbasePageFetcher
from vendorwake.integrations.browserbase.cdp import CdpError, CdpSession
from vendorwake.integrations.errors import NotConfiguredError, RateLimitedError, UpstreamError

from ..conftest import make_settings
from .conftest import live_env, live_settings, mock_client

KEY = "bb_test_key"
CONNECT_URL = "wss://connect.browserbase.com?signingKey=SECRET-SIGNING-KEY"


class FakeApi:
    def __init__(self, create_status: int = 201) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.create_status = create_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["x-bb-api-key"] == KEY
        body = json.loads(request.content)
        self.calls.append((request.method, request.url.path, body))
        if request.url.path == "/v1/sessions":
            if self.create_status != 201:
                return httpx.Response(self.create_status, json={"error": "nope"})
            return httpx.Response(
                201, json={"id": "sess-123", "connectUrl": CONNECT_URL, "status": "RUNNING"}
            )
        if request.url.path == "/v1/sessions/sess-123":
            return httpx.Response(200, json={"id": "sess-123", "status": "COMPLETED"})
        raise AssertionError(request.url)


class FakeCdp:
    """Scripted CDP endpoint: answers by method, interleaving an unrelated event before each reply."""

    def __init__(
        self, *, nav_error: str | None = None, text_lengths: tuple[int, ...] = (0, 120, 120)
    ) -> None:
        self.sent: list[dict] = []
        self._replies: list[str] = []
        self.closed = False
        self.nav_error = nav_error
        self._lengths = list(text_lengths)
        self._ready = ["loading", "complete"]

    def send(self, message: str) -> None:
        msg = json.loads(message)
        self.sent.append(msg)
        method, params = msg["method"], msg["params"]
        if method == "Target.createTarget":
            result = {"targetId": "T1"}
        elif method == "Target.attachToTarget":
            assert params == {"targetId": "T1", "flatten": True}
            result = {"sessionId": "S1"}
        elif method == "Page.navigate":
            result = {"frameId": "F1", **({"errorText": self.nav_error} if self.nav_error else {})}
        elif method == "Runtime.evaluate":
            expr = params["expression"]
            if expr == "document.readyState":
                value: object = self._ready.pop(0) if len(self._ready) > 1 else self._ready[0]
            elif "innerText.length" in expr:
                value = self._lengths.pop(0) if len(self._lengths) > 1 else self._lengths[0]
            else:
                value = {
                    "title": "Changelog",
                    "href": "https://vendor.io/changelog",
                    "text": "  v2.0\n\n\n\nBreaking  ",
                }
            result = {"result": {"type": "object", "value": value}}
        elif method == "Page.captureScreenshot":
            result = {"data": base64.b64encode(b"PNG").decode()}
        else:
            result = {}
        if method != "Target.createTarget":
            assert msg.get("sessionId") == "S1" or method == "Target.attachToTarget"
        self._replies.append(json.dumps({"method": "Page.lifecycleEvent", "params": {}}))
        self._replies.append(json.dumps({"id": msg["id"], "result": result}))

    def recv(self, timeout: float | None = None) -> str:
        if not self._replies:
            raise TimeoutError
        return self._replies.pop(0)

    def close(self) -> None:
        self.closed = True


def make_fetcher(api: FakeApi, cdp: FakeCdp, **kwargs) -> BrowserbasePageFetcher:
    connected: list[str] = []

    def connect(url: str) -> FakeCdp:
        connected.append(url)
        return cdp

    f = BrowserbasePageFetcher(
        KEY,
        "proj-1",
        http_client=mock_client(api),
        connect=connect,
        poll_interval_s=0,
        check_url=lambda u: None,
        **kwargs,
    )
    f.connected = connected  # type: ignore[attr-defined]
    return f


def test_fetch_creates_session_renders_and_releases(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    api, cdp = FakeApi(), FakeCdp()
    shots: list[tuple[str, bytes]] = []
    f = make_fetcher(
        api, cdp, screenshot_sink=lambda sid, png: shots.append((sid, png)) or f"s3://shots/{sid}.png"
    )
    doc = f.fetch("https://vendor.io/changelog")

    assert doc.fetched_via == "browserbase" and doc.browser_session_id == "sess-123"
    assert doc.text == "v2.0\n\nBreaking" and doc.title == "Changelog"
    assert doc.screenshot_ref == "s3://shots/sess-123.png" and shots == [("sess-123", b"PNG")]
    create, release = api.calls
    assert create[1] == "/v1/sessions" and create[2]["projectId"] == "proj-1" and create[2]["timeout"] >= 60
    assert release[1] == "/v1/sessions/sess-123"
    assert release[2] == {"projectId": "proj-1", "status": "REQUEST_RELEASE"}
    assert f.connected == [CONNECT_URL] and cdp.closed
    assert [m["method"] for m in cdp.sent][:4] == [
        "Target.createTarget",
        "Target.attachToTarget",
        "Page.enable",
        "Page.navigate",
    ]
    assert "SECRET-SIGNING-KEY" not in caplog.text and KEY not in caplog.text


def test_session_is_released_when_navigation_fails() -> None:
    api, cdp = FakeApi(), FakeCdp(nav_error="net::ERR_NAME_NOT_RESOLVED")
    with pytest.raises(UpstreamError, match="navigation failed"):
        make_fetcher(api, cdp).fetch("https://nope.invalid/")
    assert api.calls[-1][2]["status"] == "REQUEST_RELEASE" and cdp.closed


@pytest.mark.parametrize(("status", "error"), [(429, RateLimitedError), (401, UpstreamError)])
def test_session_create_errors_are_typed(status: int, error: type[Exception]) -> None:
    with pytest.raises(error):
        make_fetcher(FakeApi(create_status=status), FakeCdp()).fetch("https://vendor.io/")


def test_cdp_error_reply_and_timeout() -> None:
    class ErrorCdp(FakeCdp):
        def send(self, message: str) -> None:
            msg = json.loads(message)
            self._replies.append(json.dumps({"id": msg["id"], "error": {"code": -32000, "message": "bad"}}))

    with pytest.raises(CdpError, match="bad"):
        CdpSession(ErrorCdp()).call("Page.enable")

    class SilentCdp(FakeCdp):
        def send(self, message: str) -> None:
            pass

    with pytest.raises(CdpError, match="timed out"):
        CdpSession(SilentCdp(), default_timeout_s=0.01).call("Page.enable")


def test_unsafe_url_is_refused_before_creating_a_session() -> None:
    api = FakeApi()
    f = BrowserbasePageFetcher(KEY, "proj-1", http_client=mock_client(api), connect=lambda u: FakeCdp())
    with pytest.raises(Exception, match="internal host|non-public"):
        f.fetch("http://localhost:8000/admin")
    assert api.calls == []


def test_from_settings() -> None:
    assert browserbase.is_configured(make_settings()) is False
    with pytest.raises(NotConfiguredError, match="VW_BROWSERBASE"):
        browserbase.from_settings(make_settings(browserbase_api_key=SecretStr(KEY)))
    built = browserbase.from_settings(
        make_settings(browserbase_api_key=SecretStr(KEY), browserbase_project_id="proj-1")
    )
    assert KEY not in repr(built)


@pytest.mark.live
@live_env("VW_BROWSERBASE_API_KEY", "VW_BROWSERBASE_PROJECT_ID")
def test_live_browserbase_fetch() -> None:
    doc = browserbase.from_settings(live_settings()).fetch("https://docs.stripe.com/changelog")
    assert doc.fetched_via == "browserbase" and doc.browser_session_id
    assert len(doc.text) > 500
