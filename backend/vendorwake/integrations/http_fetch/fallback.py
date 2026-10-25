"""HTTP first, browser only when needed (PageFetcher).

A remote browser session costs money and ~5-15 s, so it is used only when the plain-HTTP result looks
like a client-rendered shell (`looks_like_js_shell`), the host is known to need JavaScript, or the
host blocked the plain request (HTTP 403/429/503, typical of bot protection).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from urllib.parse import urlsplit

from vendorwake.domain.contracts import PageFetcher, SourceDocument
from vendorwake.integrations.errors import IntegrationError, UpstreamError
from vendorwake.integrations.http_fetch.fetcher import FetchedPage, HttpPageFetcher, looks_like_js_shell

logger = logging.getLogger(__name__)

BLOCKED_STATUSES = frozenset({403, 429, 503})


class FallbackPageFetcher:
    def __init__(
        self,
        http: HttpPageFetcher,
        browser: PageFetcher | None,
        *,
        dynamic_hosts: Iterable[str] = (),
        min_text_chars: int = 400,
    ) -> None:
        self.http = http
        self.browser = browser
        self.dynamic_hosts = frozenset(h.lower().strip(".") for h in dynamic_hosts)
        self.min_text_chars = min_text_chars

    def _is_dynamic_host(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        return any(host == h or host.endswith("." + h) for h in self.dynamic_hosts)

    def fetch(self, url: str) -> SourceDocument:
        if self.browser is not None and self._is_dynamic_host(url):
            logger.info("fetch via browser (dynamic host)", extra={"host": urlsplit(url).hostname})
            return self.browser.fetch(url)

        page: FetchedPage | None = None
        try:
            page = self.http.fetch_page(url)
        except UpstreamError as exc:
            if self.browser is None or exc.status_code not in BLOCKED_STATUSES:
                raise
            reason = f"http_{exc.status_code}"
        else:
            if self.browser is None or not looks_like_js_shell(page, min_text_chars=self.min_text_chars):
                return page.document
            reason = "js_shell"

        logger.info("fetch falling back to browser", extra={"host": urlsplit(url).hostname, "reason": reason})
        try:
            return self.browser.fetch(url)
        except IntegrationError as exc:
            if page is None:
                raise
            # Keep the (thin) HTTP result rather than failing outright; callers see fetched_via="http".
            logger.warning(
                "browser fallback failed; using HTTP result",
                extra={"host": urlsplit(url).hostname, "error": type(exc).__name__},
            )
            return page.document
