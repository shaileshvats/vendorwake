"""Remote-browser page fetcher on Browserbase (PageFetcher).

REST calls follow the official API reference and the ``browserbase==1.21.0`` SDK source
(``resources/sessions/sessions.py``), without taking the SDK dependency:
- create: ``POST https://api.browserbase.com/v1/sessions`` with header ``X-BB-API-Key`` and JSON
  ``{"projectId", "timeout" (seconds, 60..21600), "userMetadata"}``; the response has ``id`` and
  ``connectUrl`` (signed WebSocket URL: treat as a secret, never log it).
- end: ``POST /v1/sessions/{id}`` with ``{"status": "REQUEST_RELEASE", "projectId"}`` ("Use before
  session's timeout to avoid additional charges").

The page itself is driven over CDP (see `cdp.py`). The browser runs remotely; nothing is rendered or
executed locally. Extracted text is untrusted data.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from vendorwake.domain.contracts import SourceDocument
from vendorwake.integrations.browserbase.cdp import CdpError, CdpSession, ConnectFn, websocket_connect
from vendorwake.integrations.errors import RateLimitedError, UpstreamError
from vendorwake.integrations.http_fetch.fetcher import check_public_url, sha256_text, system_resolver
from vendorwake.integrations.http_fetch.html_text import normalise_text

logger = logging.getLogger(__name__)

SERVICE = "browserbase"
API_BASE = "https://api.browserbase.com"
MIN_SESSION_TIMEOUT_S = 60  # API minimum

# One round trip for everything we read from the page.
_EXTRACT_JS = (
    "({title: document.title || null, href: location.href,"
    " text: document.body ? document.body.innerText : ''})"
)

ScreenshotSink = Callable[[str, bytes], str]
"""``(browser_session_id, png_bytes) -> screenshot_ref`` (e.g. an object-store key)."""


class BrowserbasePageFetcher:
    def __init__(
        self,
        api_key: str,
        project_id: str,
        *,
        http_client: httpx.Client | None = None,
        connect: ConnectFn = websocket_connect,
        api_base: str = API_BASE,
        session_timeout_s: int = 120,
        navigation_timeout_s: float = 30.0,
        settle_timeout_s: float = 6.0,
        poll_interval_s: float = 0.5,
        max_text_chars: int = 1_000_000,
        screenshot_sink: ScreenshotSink | None = None,
        check_url: Callable[[str], None] | None = None,
    ) -> None:
        if not api_key or not project_id:
            raise ValueError("api_key and project_id are required")
        self._api_key = api_key
        self.project_id = project_id
        self._http = http_client or httpx.Client(timeout=30.0)
        self._owns_http = http_client is None
        self._connect = connect
        self._api_base = api_base.rstrip("/")
        self.session_timeout_s = max(MIN_SESSION_TIMEOUT_S, session_timeout_s)
        self.navigation_timeout_s = navigation_timeout_s
        self.settle_timeout_s = settle_timeout_s
        self.poll_interval_s = poll_interval_s
        self.max_text_chars = max_text_chars
        self.screenshot_sink = screenshot_sink
        self._check_url = check_url or (lambda u: check_public_url(u, system_resolver))

    def __repr__(self) -> str:
        return f"BrowserbasePageFetcher(project_id={self.project_id!r})"

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # ------------------------------------------------------------------ PageFetcher

    def fetch(self, url: str) -> SourceDocument:
        self._check_url(url)
        session_id, connect_url = self._create_session()
        try:
            return self._render(url, session_id, connect_url)
        finally:
            self._release_session(session_id)

    # ------------------------------------------------------------------ REST

    def _api(self, method: str, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._http.request(
                method,
                f"{self._api_base}{path}",
                json=json,
                headers={"X-BB-API-Key": self._api_key, "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                f"Browserbase request failed ({type(exc).__name__})", service=SERVICE
            ) from exc
        if response.status_code == 429:
            retry = response.headers.get("retry-after")
            raise RateLimitedError(
                "Browserbase rate or concurrency limit reached",
                service=SERVICE,
                status_code=429,
                retry_after_s=float(retry) if retry and retry.isdigit() else None,
            )
        if response.status_code in (401, 403):
            raise UpstreamError(
                "Browserbase rejected the API key or project",
                service=SERVICE,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise UpstreamError(
                f"Browserbase returned HTTP {response.status_code}",
                service=SERVICE,
                status_code=response.status_code,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError("Browserbase returned a non-JSON response", service=SERVICE) from exc
        return body if isinstance(body, dict) else {}

    def _create_session(self) -> tuple[str, str]:
        body = self._api(
            "POST",
            "/v1/sessions",
            {
                "projectId": self.project_id,
                "timeout": self.session_timeout_s,
                "userMetadata": {"app": "vendorwake", "purpose": "source-fetch"},
            },
        )
        session_id, connect_url = body.get("id"), body.get("connectUrl")
        if not isinstance(session_id, str) or not isinstance(connect_url, str):
            raise UpstreamError("Browserbase session response lacks id/connectUrl", service=SERVICE)
        logger.info("browserbase session created", extra={"browser_session_id": session_id})
        return session_id, connect_url

    def _release_session(self, session_id: str) -> None:
        try:
            self._api(
                "POST",
                f"/v1/sessions/{session_id}",
                {"projectId": self.project_id, "status": "REQUEST_RELEASE"},
            )
        except Exception as exc:  # the session also ends at its timeout; don't mask the fetch result
            logger.warning(
                "browserbase session release failed",
                extra={"browser_session_id": session_id, "error": type(exc).__name__},
            )

    # ------------------------------------------------------------------ CDP

    def _render(self, url: str, session_id: str, connect_url: str) -> SourceDocument:
        try:
            transport = self._connect(connect_url)
        except Exception as exc:
            raise UpstreamError(
                f"could not connect to the Browserbase session ({type(exc).__name__})", service=SERVICE
            ) from None
        cdp = CdpSession(transport, default_timeout_s=self.navigation_timeout_s)
        try:
            target = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
            page = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
            cdp.call("Page.enable", session_id=page)
            nav = cdp.call("Page.navigate", {"url": url}, session_id=page)
            if nav.get("errorText"):
                raise UpstreamError(
                    f"browser navigation failed: {str(nav['errorText'])[:120]}", service=SERVICE
                )
            self._wait_ready(cdp, page)
            data = cdp.evaluate(_EXTRACT_JS, session_id=page) or {}
            screenshot_ref = self._screenshot(cdp, page, session_id)
        except CdpError as exc:
            raise UpstreamError(f"browser session failed: {exc}", service=SERVICE) from exc
        except (KeyError, TypeError) as exc:
            raise UpstreamError("unexpected CDP reply shape", service=SERVICE) from exc
        finally:
            cdp.close()

        final_url = data.get("href") if isinstance(data.get("href"), str) else url
        text = normalise_text(str(data.get("text") or ""))[: self.max_text_chars]
        title = data.get("title") if isinstance(data.get("title"), str) else None
        return SourceDocument(
            url=final_url,
            retrieved_at=datetime.now(UTC),
            fetched_via="browserbase",
            content_sha256=sha256_text(text),
            text=text,
            title=title or None,
            browser_session_id=session_id,
            screenshot_ref=screenshot_ref,
        )

    def _wait_ready(self, cdp: CdpSession, page: str) -> None:
        """Wait for ``readyState == complete``, then until the visible text length stops changing
        (client-rendered pages keep filling in after the load event)."""
        deadline = time.monotonic() + self.navigation_timeout_s
        while cdp.evaluate("document.readyState", session_id=page) != "complete":
            if time.monotonic() > deadline:
                logger.info("browser page not fully loaded before timeout; extracting anyway")
                break
            time.sleep(self.poll_interval_s)
        settle_deadline = time.monotonic() + self.settle_timeout_s
        previous = -1
        while time.monotonic() < settle_deadline:
            length = cdp.evaluate("document.body ? document.body.innerText.length : 0", session_id=page)
            if isinstance(length, int) and length > 0 and length == previous:
                return
            previous = length if isinstance(length, int) else -1
            time.sleep(self.poll_interval_s)

    def _screenshot(self, cdp: CdpSession, page: str, session_id: str) -> str | None:
        if self.screenshot_sink is None:
            return None
        try:
            shot = cdp.call("Page.captureScreenshot", {"format": "png"}, session_id=page)
            return self.screenshot_sink(session_id, base64.b64decode(shot["data"]))
        except Exception as exc:  # a screenshot is optional evidence; never fail the fetch for it
            logger.warning("browser screenshot failed", extra={"error": type(exc).__name__})
            return None
